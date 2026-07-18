import json
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Callable


class ClientError(RuntimeError):
    pass


DEFAULT_BFS_MAX_DEPTH = 8
DEFAULT_BFS_MAX_DIRECTORIES = 64
DEFAULT_BFS_MAX_FILES = 500
DEFAULT_BFS_MAX_REQUESTS = 80


def share_url_for_directory(share_url: str, pdir_fid: str) -> str:
    """Build a Quark share deep-link that QAS resolves to pdir_fid."""
    base = str(share_url or "").split("#", 1)[0].rstrip("/")
    fid = str(pdir_fid or "").strip()
    if not base or not fid:
        return str(share_url or "")
    return f"{base}#/list/share/{fid}"


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

    def get_share_expanded(
        self,
        share_url: str,
        *,
        max_depth: int = DEFAULT_BFS_MAX_DEPTH,
        max_directories: int = DEFAULT_BFS_MAX_DIRECTORIES,
        max_files: int = DEFAULT_BFS_MAX_FILES,
        max_requests: int = DEFAULT_BFS_MAX_REQUESTS,
    ) -> dict:
        """Load a share and BFS into nested folders until files or caps are hit."""
        root = self.get_share(share_url, show_all=True)
        if not isinstance(root, dict):
            return {"list": []}

        root_items = list(root.get("list") or [])
        files: list[dict] = []
        directories: list[dict] = []
        for item in root_items:
            if not isinstance(item, dict):
                continue
            if item.get("dir"):
                directories.append(item)
            elif item.get("file_name"):
                files.append(item)

        # Flat file shares need no traversal.
        if files and not directories:
            return root

        stoken = root.get("stoken")
        visited: set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        for item in directories:
            fid = str(item.get("fid") or "").strip()
            if fid:
                queue.append((fid, 1))

        requests_used = 1
        directories_seen = 0
        while queue and directories_seen < max_directories and requests_used < max_requests:
            if len(files) >= max_files:
                break
            fid, depth = queue.popleft()
            if not fid or fid in visited:
                continue
            visited.add(fid)
            directories_seen += 1
            if depth > max_depth:
                continue
            try:
                page = self.get_share(
                    share_url,
                    show_all=True,
                    stoken=stoken if isinstance(stoken, str) else None,
                    pdir_fid=fid,
                )
            except ClientError:
                continue
            requests_used += 1
            if isinstance(page, dict) and page.get("stoken") and not stoken:
                stoken = page.get("stoken")
            for item in list((page or {}).get("list") or []):
                if not isinstance(item, dict) or not item.get("file_name"):
                    continue
                if item.get("dir"):
                    child_fid = str(item.get("fid") or "").strip()
                    if (
                        child_fid
                        and child_fid not in visited
                        and depth < max_depth
                        and directories_seen + len(queue) < max_directories
                    ):
                        queue.append((child_fid, depth + 1))
                else:
                    files.append(item)
                    if len(files) >= max_files:
                        break

        expanded = dict(root)
        # Prefer discovered files for ranking/specs; keep root dirs for context.
        expanded["list"] = [*files, *directories] if files else root_items
        expanded["expanded"] = True
        expanded["expansion"] = {
            "requests": requests_used,
            "directories": directories_seen,
            "files": len(files),
            "truncated": bool(queue) or len(files) >= max_files,
        }
        return expanded

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

