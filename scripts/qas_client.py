import json
import random
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


class ClientError(RuntimeError):
    pass


DEFAULT_SAMPLE_MAX_DEPTH = 4
DEFAULT_SAMPLE_MAX_REQUESTS = 4
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")


def share_url_for_directory(share_url: str, pdir_fid: str) -> str:
    """Build a Quark share deep-link that QAS resolves to pdir_fid."""
    base = str(share_url or "").split("#", 1)[0].rstrip("/")
    fid = str(pdir_fid or "").strip()
    if not base or not fid:
        return str(share_url or "")
    return f"{base}#/list/share/{fid}"


def _is_video_item(item: dict) -> bool:
    name = str(item.get("file_name") or "").casefold()
    return bool(name) and name.endswith(VIDEO_EXTENSIONS)


class QasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        opener: Callable = urllib.request.urlopen,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._opener = opener
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"QasClient(base_url={self.base_url!r}, token='***')"

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict | None = None,
        body: dict | None = None,
        raw: bool = False,
    ):
        query = dict(params or {})
        query["token"] = self._token
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                content = response.read()
        except urllib.error.HTTPError as error:
            raise ClientError(f"QAS HTTP {error.code}: {error.reason}") from None
        except urllib.error.URLError as error:
            raise ClientError(f"QAS connection failed: {error.reason}") from None
        except TimeoutError:
            raise ClientError("QAS request timed out") from None
        except Exception as error:
            message = str(error).replace(self._token, "***")
            raise ClientError(f"QAS request failed: {message}") from None

        if raw:
            return content.decode("utf-8", errors="replace")
        try:
            result = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("QAS returned invalid JSON") from None
        if not result.get("success"):
            message = (
                result.get("message")
                or result.get("data", {}).get("error")
                or "unknown error"
            )
            raise ClientError(str(message).replace(self._token, "***"))
        return result.get("data")

    def get_config(self) -> dict:
        return self._request("/data")

    def search(self, query: str, deep: bool = True) -> list[dict]:
        data = self._request(
            "/task_suggestions",
            params={"q": query, "d": "1" if deep else "0"},
        )
        return list(data or [])

    def get_share(
        self,
        share_url: str,
        show_all: bool = True,
        *,
        stoken: str | None = None,
        pdir_fid: str | None = None,
    ) -> dict:
        request_url = (
            share_url_for_directory(share_url, pdir_fid)
            if pdir_fid
            else str(share_url or "")
        )
        body: dict = {"shareurl": request_url}
        if stoken:
            body["stoken"] = stoken
        data = self._request(
            "/get_share_detail",
            method="POST",
            body=body,
        )
        if not show_all and isinstance(data, dict):
            data = dict(data)
            data["list"] = list(data.get("list", []))[:10]
        return data

    def get_share_preview(
        self,
        share_url: str,
        *,
        max_depth: int = DEFAULT_SAMPLE_MAX_DEPTH,
        max_requests: int = DEFAULT_SAMPLE_MAX_REQUESTS,
        rng: random.Random | None = None,
    ) -> dict:
        """Root metadata plus a single random folder walk for sample filenames."""
        root = self.get_share(share_url, show_all=True)
        if not isinstance(root, dict):
            return {"list": []}

        picker = rng or random.Random()
        root_items = [item for item in list(root.get("list") or []) if isinstance(item, dict)]
        root_files = [
            item
            for item in root_items
            if item.get("file_name") and not item.get("dir")
        ]
        root_dirs = [
            item for item in root_items if item.get("dir") and item.get("fid")
        ]

        sample_files: list[dict] = []
        requests_used = 1
        hops = 0
        stoken = root.get("stoken") if isinstance(root.get("stoken"), str) else None

        # Root already has videos, or no folders to sample.
        if any(_is_video_item(item) for item in root_files) or not root_dirs:
            previewed = dict(root)
            previewed["preview"] = {
                "mode": "root",
                "requests": requests_used,
                "hops": 0,
                "sampleFiles": 0,
            }
            return previewed

        current_dirs = root_dirs
        while (
            current_dirs
            and hops < max_depth
            and requests_used < max_requests
            and not any(_is_video_item(item) for item in sample_files)
        ):
            chosen = picker.choice(current_dirs)
            fid = str(chosen.get("fid") or "").strip()
            if not fid:
                break
            try:
                page = self.get_share(
                    share_url,
                    show_all=True,
                    stoken=stoken,
                    pdir_fid=fid,
                )
            except ClientError:
                break
            requests_used += 1
            hops += 1
            if isinstance(page, dict) and isinstance(page.get("stoken"), str) and not stoken:
                stoken = page.get("stoken")
            page_items = [
                item
                for item in list((page or {}).get("list") or [])
                if isinstance(item, dict) and item.get("file_name")
            ]
            page_files = [item for item in page_items if not item.get("dir")]
            page_dirs = [item for item in page_items if item.get("dir") and item.get("fid")]
            sample_files.extend(page_files)
            if any(_is_video_item(item) for item in page_files):
                break
            current_dirs = page_dirs

        seen_fids = {
            str(item.get("fid"))
            for item in root_items
            if item.get("fid") is not None
        }
        merged = list(root_items)
        for item in sample_files:
            fid = item.get("fid")
            key = str(fid) if fid is not None else None
            if key and key in seen_fids:
                continue
            if key:
                seen_fids.add(key)
            merged.append(item)

        previewed = dict(root)
        previewed["list"] = merged
        previewed["preview"] = {
            "mode": "sample",
            "requests": requests_used,
            "hops": hops,
            "sampleFiles": len(sample_files),
        }
        return previewed

    def add_task(self, task: dict) -> dict:
        return self._request(
            "/api/add_task",
            method="POST",
            body=task,
        ) or {"message": "task added"}

    def run_task(self, task: dict) -> dict:
        content = self._request(
            "/run_script_now",
            method="POST",
            body={"tasklist": [task]},
            raw=True,
        )
        events = []
        for line in content.splitlines():
            if line.startswith("data: "):
                value = line[6:]
                if value and value != "[DONE]":
                    events.append(value)
        return {"submitted": True, "events": events}
