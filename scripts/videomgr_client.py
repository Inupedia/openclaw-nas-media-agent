"""UGREEN Theater (com.ugreen.videomgr) local library search.

Uses the undocumented HTTP search API with a live UGOS session token.
Prefer VIDEOMGR_TOKEN, or discover tokens from Redis keys UGTOKEN-*.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPConnection
from typing import Callable


class VideomgrError(RuntimeError):
    pass


# video_info.type from Theater search responses
_TYPE_TO_MEDIA = {
    1: "movie",
    2: "drama",
    3: "other",
}


class _UnixHTTPConnection(HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost")
        self._unix_path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._unix_path)


def _redis_call(host: str, port: int, *args: str, timeout: float = 2.0) -> str:
    """Redis GET/KEYS via redis-cli when present, else minimal RESP."""
    try:
        completed = subprocess.run(
            ["redis-cli", "-h", host, "-p", str(port), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if completed.returncode == 0:
            return (completed.stdout or "").rstrip("\n")
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    payload = f"*{len(args)}\r\n".encode()
    for arg in args:
        raw = arg.encode("utf-8")
        payload += f"${len(raw)}\r\n".encode() + raw + b"\r\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)
        chunks: list[bytes] = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
            joined = b"".join(chunks)
            if joined.startswith(b"$-1\r\n"):
                return ""
            if joined.startswith((b"+", b":", b"-")) and b"\r\n" in joined:
                break
            if joined.startswith(b"$"):
                header, _, rest = joined.partition(b"\r\n")
                size = int(header[1:])
                if size < 0:
                    return ""
                if len(rest) >= size + 2:
                    break
            if joined.startswith(b"*") and len(data) < 65536:
                break
        raw = b"".join(chunks)

    if raw.startswith(b"-"):
        raise VideomgrError(raw.decode("utf-8", "replace").strip())
    if raw.startswith(b"$-1\r\n"):
        return ""
    if raw.startswith((b"+", b":")):
        return raw[1:].split(b"\r\n", 1)[0].decode("utf-8", "replace")
    if raw.startswith(b"$"):
        header, _, rest = raw.partition(b"\r\n")
        size = int(header[1:])
        if size < 0:
            return ""
        return rest[:size].decode("utf-8", "replace")
    if raw.startswith(b"*"):
        lines = raw.split(b"\r\n")
        values = []
        i = 1
        while i < len(lines):
            line = lines[i]
            if line.startswith(b"$"):
                size = int(line[1:])
                i += 1
                if size < 0:
                    values.append("")
                else:
                    values.append(
                        lines[i].decode("utf-8", "replace")
                        if i < len(lines)
                        else ""
                    )
            i += 1
        return "\n".join(values)
    return raw.decode("utf-8", "replace")


def discover_tokens(
    *,
    redis_host: str = "127.0.0.1",
    redis_port: int = 6379,
    prefer_user: str = "",
) -> list[str]:
    """Return UGOS api tokens, preferring admin / prefer_user sessions."""
    try:
        keys_blob = _redis_call(redis_host, redis_port, "KEYS", "UGTOKEN-*")
    except OSError as exc:
        raise VideomgrError(f"redis unavailable: {exc}") from exc

    ranked: list[tuple[int, str]] = []
    for key in (keys_blob or "").splitlines():
        key = key.strip()
        if not key.startswith("UGTOKEN-"):
            continue
        token = key[len("UGTOKEN-") :]
        try:
            meta = json.loads(_redis_call(redis_host, redis_port, "GET", key) or "{}")
        except (VideomgrError, json.JSONDecodeError, OSError):
            meta = {}
        score = 0
        if meta.get("type") == "admin":
            score += 10
        username = str(meta.get("username") or "")
        if prefer_user and username == prefer_user:
            score += 20
        ranked.append((score, token))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    # unique preserve order
    seen: set[str] = set()
    out: list[str] = []
    for _, token in ranked:
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


class VideomgrClient:
    def __init__(
        self,
        *,
        base_url: str = "",
        sock_path: str = "/var/ugreen/video_serv.sock",
        token: str = "",
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        prefer_user: str = "",
        timeout: float = 8.0,
        token_provider: Callable[[], list[str]] | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.sock_path = sock_path
        self.token = (token or "").strip()
        self.redis_host = redis_host
        self.redis_port = int(redis_port)
        self.prefer_user = prefer_user.strip()
        self.timeout = timeout
        self._token_provider = token_provider

    def _tokens(self) -> list[str]:
        if self.token:
            return [self.token]
        if self._token_provider is not None:
            return list(self._token_provider())
        return discover_tokens(
            redis_host=self.redis_host,
            redis_port=self.redis_port,
            prefer_user=self.prefer_user,
        )

    def _request(self, path_with_query: str) -> dict:
        if self.base_url:
            url = f"{self.base_url}{path_with_query}"
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
            except urllib.error.URLError as exc:
                raise VideomgrError(f"videomgr request failed: {exc}") from exc
            try:
                return json.loads(body)
            except json.JSONDecodeError as exc:
                raise VideomgrError("videomgr returned non-json") from exc

        if not self.sock_path or not os.path.exists(self.sock_path):
            raise VideomgrError("videomgr sock/url unavailable")
        conn = _UnixHTTPConnection(self.sock_path)
        try:
            conn.request(
                "GET",
                path_with_query,
                headers={"Host": "localhost", "Accept": "application/json"},
            )
            response = conn.getresponse()
            body = response.read().decode("utf-8", "replace")
        except OSError as exc:
            raise VideomgrError(f"videomgr sock failed: {exc}") from exc
        finally:
            conn.close()
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise VideomgrError("videomgr returned non-json") from exc

    def search(self, keyword: str, *, limit: int = 20) -> list[dict]:
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        tokens = self._tokens()
        if not tokens:
            raise VideomgrError("no videomgr session token")

        last_error: Exception | None = None
        for token in tokens[:8]:
            query = urllib.parse.urlencode(
                {
                    "language": "zh-CN",
                    "search_type": 1,
                    "offset": 0,
                    "limit": max(1, min(int(limit), 200)),
                    "keyword": keyword,
                    "token": token,
                }
            )
            path = f"/ugreen/v1/video/search?{query}"
            try:
                payload = self._request(path)
            except VideomgrError as exc:
                last_error = exc
                continue
            code = payload.get("code")
            if code == 1024:
                last_error = VideomgrError("login expired")
                continue
            if code not in (0, 200):
                last_error = VideomgrError(
                    str(payload.get("msg") or f"videomgr code {code}")
                )
                continue
            return _normalize_search_payload(payload.get("data") or {})

        if last_error is not None:
            raise VideomgrError(str(last_error))
        return []


def _normalize_search_payload(data: dict) -> list[dict]:
    movies = (data.get("movies_list") or {}).get("video_arr") or []
    results: list[dict] = []
    for item in movies:
        if not isinstance(item, dict):
            continue
        info = item.get("video_info") or {}
        if not isinstance(info, dict):
            continue
        paths = info.get("file_path") or []
        if isinstance(paths, str):
            paths = [paths]
        paths = [str(path) for path in paths if path]
        if not paths:
            continue
        media_type = _TYPE_TO_MEDIA.get(int(info.get("type") or 0), "other")
        year = info.get("year")
        try:
            year = int(year) if year not in (None, "", 0) else None
        except (TypeError, ValueError):
            year = None
        results.append(
            {
                "name": str(info.get("name") or ""),
                "year": year,
                "mediaType": media_type,
                "filePaths": paths,
                "tmdbId": info.get("tmdb_id"),
                "score": info.get("score"),
            }
        )
    return results


def client_from_env() -> VideomgrClient | None:
    """Build a client when Theater lookup is enabled and reachable."""
    mode = (os.environ.get("VIDEOMGR_ENABLED") or "auto").strip().casefold()
    if mode in {"0", "false", "off", "no", "disabled"}:
        return None

    token = (os.environ.get("VIDEOMGR_TOKEN") or "").strip()
    base_url = (os.environ.get("VIDEOMGR_BASE_URL") or "").strip()
    sock = (
        os.environ.get("VIDEOMGR_SOCK") or "/var/ugreen/video_serv.sock"
    ).strip()
    redis_host = (os.environ.get("VIDEOMGR_REDIS_HOST") or "127.0.0.1").strip()
    redis_port = int(os.environ.get("VIDEOMGR_REDIS_PORT") or "6379")
    prefer_user = (os.environ.get("VIDEOMGR_PREFER_USER") or "").strip()

    has_transport = bool(base_url) or os.path.exists(sock)
    if mode == "auto" and not token and not has_transport:
        return None
    if mode in {"1", "true", "on", "yes", "enabled"} and not has_transport and not token:
        return None

    client = VideomgrClient(
        base_url=base_url,
        sock_path=sock,
        token=token,
        redis_host=redis_host,
        redis_port=redis_port,
        prefer_user=prefer_user,
    )
    if mode == "auto" and not token:
        # Probe redis; if no live tokens, stay silent and use filesystem only.
        try:
            if not discover_tokens(
                redis_host=redis_host,
                redis_port=redis_port,
                prefer_user=prefer_user,
            ):
                return None
        except VideomgrError:
            if not has_transport:
                return None
            # Transport exists but redis failed — still return client so explicit
            # token injection later works; search will error softly in catalog.
            return None
    return client
