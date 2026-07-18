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
DEFAULT_PLAN_MAX_REQUESTS = 24
DEFAULT_PLAN_MAX_FILES = 80
DEFAULT_TREE_MAX_DEPTH = 6
DEFAULT_TREE_MAX_NODES = 200
DEFAULT_TREE_MAX_REQUESTS = 40
VIDEO_EXTENSIONS = (".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts")
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS + (".ass", ".ssa", ".srt", ".vtt")
_DIR_BONUS = (
    "1080",
    "2160",
    "720",
    "4k",
    "s01",
    "s02",
    "season",
    "第",
    "季",
    "ova",
    "mkv",
    "mp4",
    "anime",
    "动画",
    "bd",
    "web",
)
_DIR_PENALTY = (
    "小说",
    "漫画",
    "comic",
    "manga",
    "mobi",
    "epub",
    "pdf",
    "扫码",
    "群",
    "剧场",
    "gekijouban",
)


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


def _is_media_item(item: dict) -> bool:
    name = str(item.get("file_name") or "").casefold()
    return bool(name) and name.endswith(MEDIA_EXTENSIONS)


def _dir_priority(item: dict) -> int:
    name = str(item.get("file_name") or "").casefold()
    score = 0
    for token in _DIR_BONUS:
        if token in name:
            score += 3
    for token in _DIR_PENALTY:
        if token in name:
            score -= 5
    return score


def _pick_dir(dirs: list[dict], picker: random.Random) -> dict:
    if not dirs:
        raise ValueError("empty dirs")
    ranked = sorted(dirs, key=_dir_priority, reverse=True)
    top = ranked[: min(3, len(ranked))]
    return picker.choice(top)


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
            chosen = _pick_dir(current_dirs, picker)
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
            # Prefer keeping video samples; ignore pure ebook/image noise when videos exist deeper.
            page_videos = [item for item in page_files if _is_video_item(item)]
            sample_files.extend(page_videos or page_files)
            if page_videos:
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

    def get_share_for_download(
        self,
        share_url: str,
        *,
        max_requests: int = DEFAULT_PLAN_MAX_REQUESTS,
        max_files: int = DEFAULT_PLAN_MAX_FILES,
    ) -> dict:
        """BFS folders until enough video/subtitle files are collected for planning."""
        root = self.get_share(share_url, show_all=True)
        if not isinstance(root, dict):
            return {"list": []}

        stoken = root.get("stoken") if isinstance(root.get("stoken"), str) else None
        root_items = [
            item for item in list(root.get("list") or []) if isinstance(item, dict)
        ]
        media_files: list[dict] = [
            item
            for item in root_items
            if not item.get("dir") and _is_media_item(item)
        ]
        queue = sorted(
            [
                item
                for item in root_items
                if item.get("dir") and item.get("fid")
            ],
            key=_dir_priority,
            reverse=True,
        )
        seen_dirs = {
            str(item.get("fid"))
            for item in queue
            if item.get("fid") is not None
        }
        requests_used = 1

        while queue and requests_used < max_requests and len(media_files) < max_files:
            current = queue.pop(0)
            fid = str(current.get("fid") or "").strip()
            if not fid:
                continue
            try:
                page = self.get_share(
                    share_url,
                    show_all=True,
                    stoken=stoken,
                    pdir_fid=fid,
                )
            except ClientError:
                continue
            requests_used += 1
            if isinstance(page, dict) and isinstance(page.get("stoken"), str) and not stoken:
                stoken = page.get("stoken")
            page_items = [
                item
                for item in list((page or {}).get("list") or [])
                if isinstance(item, dict) and item.get("file_name")
            ]
            for item in page_items:
                if item.get("dir") and item.get("fid"):
                    key = str(item.get("fid"))
                    if key not in seen_dirs:
                        seen_dirs.add(key)
                        queue.append(item)
                        queue.sort(key=_dir_priority, reverse=True)
                elif not item.get("dir") and _is_media_item(item):
                    media_files.append(item)
                    if len(media_files) >= max_files:
                        break

        merged = list(root_items)
        seen_fids = {
            str(item.get("fid"))
            for item in merged
            if item.get("fid") is not None
        }
        for item in media_files:
            key = str(item.get("fid")) if item.get("fid") is not None else None
            if key and key in seen_fids:
                continue
            if key:
                seen_fids.add(key)
            merged.append(item)

        result = dict(root)
        result["list"] = merged
        result["preview"] = {
            "mode": "download_bfs",
            "requests": requests_used,
            "sampleFiles": len(media_files),
        }
        return result

    def get_share_tree(
        self,
        share_url: str,
        *,
        max_depth: int = DEFAULT_TREE_MAX_DEPTH,
        max_nodes: int = DEFAULT_TREE_MAX_NODES,
        max_requests: int = DEFAULT_TREE_MAX_REQUESTS,
    ) -> dict:
        """BFS expand a share into a nested directory tree (bounded)."""
        root = self.get_share(share_url, show_all=True)
        if not isinstance(root, dict):
            return {
                "share": {},
                "tree": [],
                "stats": {
                    "directories": 0,
                    "files": 0,
                    "videos": 0,
                    "truncated": False,
                    "requests": 0,
                },
            }

        stoken = root.get("stoken") if isinstance(root.get("stoken"), str) else None
        root_items = [
            item
            for item in list(root.get("list") or [])
            if isinstance(item, dict) and item.get("file_name")
        ]

        def make_node(item: dict, path: str, depth: int) -> dict:
            name = str(item.get("file_name") or "")
            is_dir = bool(item.get("dir"))
            node_path = f"{path}/{name}" if path else name
            return {
                "name": name,
                "isDirectory": is_dir,
                "size": int(item.get("size") or 0),
                "fid": str(item.get("fid") or "").strip() or None,
                "path": node_path,
                "depth": depth,
                "children": [],
            }

        tree: list[dict] = []
        # queue entries: (node_dict, depth)
        queue: list[tuple[dict, int]] = []
        node_count = 0
        dir_count = 0
        file_count = 0
        video_count = 0
        truncated = False
        requests_used = 1

        for item in root_items:
            if node_count >= max_nodes:
                truncated = True
                break
            node = make_node(item, "", 0)
            tree.append(node)
            node_count += 1
            if node["isDirectory"]:
                dir_count += 1
                if node["fid"]:
                    queue.append((node, 0))
            else:
                file_count += 1
                if _is_video_item(item):
                    video_count += 1

        while queue and requests_used < max_requests and node_count < max_nodes:
            parent, depth = queue.pop(0)
            if depth >= max_depth:
                truncated = True
                continue
            fid = parent.get("fid")
            if not fid:
                continue
            try:
                page = self.get_share(
                    share_url,
                    show_all=True,
                    stoken=stoken,
                    pdir_fid=fid,
                )
            except ClientError:
                truncated = True
                continue
            requests_used += 1
            if (
                isinstance(page, dict)
                and isinstance(page.get("stoken"), str)
                and not stoken
            ):
                stoken = page.get("stoken")
            page_items = [
                item
                for item in list((page or {}).get("list") or [])
                if isinstance(item, dict) and item.get("file_name")
            ]
            for item in page_items:
                if node_count >= max_nodes:
                    truncated = True
                    break
                child = make_node(item, parent["path"], depth + 1)
                parent["children"].append(child)
                node_count += 1
                if child["isDirectory"]:
                    dir_count += 1
                    if child["fid"] and depth + 1 < max_depth:
                        queue.append((child, depth + 1))
                    elif child["fid"]:
                        truncated = True
                else:
                    file_count += 1
                    if _is_video_item(item):
                        video_count += 1
            if node_count >= max_nodes:
                truncated = True
                break

        if queue:
            truncated = True

        return {
            "share": root.get("share") if isinstance(root.get("share"), dict) else {},
            "tree": tree,
            "stats": {
                "directories": dir_count,
                "files": file_count,
                "videos": video_count,
                "truncated": truncated,
                "requests": requests_used,
                "nodes": node_count,
            },
        }

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
