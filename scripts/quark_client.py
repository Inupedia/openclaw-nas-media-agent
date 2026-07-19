"""Minimal Quark Drive client for re-pushing cloud files into aria2."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from qas_client import ClientError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 "
    "Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
)
BASE_URL = "https://drive-pc.quark.cn"


class QuarkClient:
    def __init__(
        self,
        cookie: str,
        *,
        opener: Callable = urllib.request.urlopen,
        timeout: int = 30,
    ):
        self._cookie = str(cookie or "").strip()
        if not self._cookie:
            raise ClientError("Quark cookie is empty")
        self._opener = opener
        self.timeout = timeout

    def __repr__(self) -> str:
        return "QuarkClient(cookie='***')"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: dict | None = None,
    ) -> dict:
        query = {"pr": "ucpro", "fr": "pc", **(params or {})}
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {
            "cookie": self._cookie,
            "content-type": "application/json",
            "user-agent": USER_AGENT,
        }
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ClientError(f"quark HTTP {error.code}: {error.reason}") from None
        except urllib.error.URLError as error:
            raise ClientError(f"quark connection failed: {error.reason}") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("quark returned invalid JSON") from None
        except TimeoutError:
            raise ClientError("quark request timed out") from None
        if not isinstance(payload, dict):
            raise ClientError("quark returned unexpected payload")
        if int(payload.get("code") or 0) != 0:
            message = str(payload.get("message") or "quark request failed")
            raise ClientError(message)
        return payload

    def get_fid(self, path: str) -> str:
        payload = self._request(
            "POST",
            "/1/clouddrive/file/info/path_list",
            body={"file_path": [path], "namespace": "0"},
        )
        rows = payload.get("data") or []
        if not rows:
            raise ClientError(f"quark path not found: {path}")
        fid = str(rows[0].get("fid") or "").strip()
        if not fid:
            raise ClientError(f"quark path missing fid: {path}")
        return fid

    def list_dir(self, path: str) -> list[dict]:
        fid = self.get_fid(path)
        merged: list[dict] = []
        page = 1
        total = None
        while True:
            payload = self._request(
                "GET",
                "/1/clouddrive/file/sort",
                params={
                    "pdir_fid": fid,
                    "_page": str(page),
                    "_size": "50",
                    "_fetch_total": "1",
                    "_fetch_sub_dirs": "0",
                    "_sort": "file_type:asc,updated_at:desc",
                    "fetch_all_file": "1",
                    "fetch_risk_file_name": "1",
                },
            )
            data = payload.get("data") or {}
            batch = list(data.get("list") or [])
            if not batch:
                break
            merged.extend(batch)
            metadata = payload.get("metadata") or {}
            total = metadata.get("_total", total)
            if total is not None and len(merged) >= int(total):
                break
            page += 1
            if page > 40:
                break
        return merged

    def list_files(self, path: str) -> list[dict]:
        files = []
        for item in self.list_dir(path):
            if item.get("dir") is True or item.get("file_type") == 0:
                continue
            name = str(item.get("file_name") or "").strip()
            fid = str(item.get("fid") or "").strip()
            if not name or not fid:
                continue
            files.append(
                {
                    "fid": fid,
                    "name": name,
                    "size": int(item.get("size") or 0),
                }
            )
        return files

    def download_entries(self, fids: list[str]) -> list[dict]:
        if not fids:
            return []
        payload, set_cookie = self._request_download(list(fids))
        entries = []
        for item in payload.get("data") or []:
            url = str(item.get("download_url") or "").strip()
            name = str(item.get("file_name") or "").strip()
            fid = str(item.get("fid") or "").strip()
            if not url or not name:
                continue
            entry = {"fid": fid, "name": name, "download_url": url}
            if set_cookie:
                entry["cookie"] = set_cookie
            entries.append(entry)
        return entries

    def _request_download(self, fids: list[str]) -> tuple[dict, str]:
        query = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        url = f"{BASE_URL}/1/clouddrive/file/download?{urllib.parse.urlencode(query)}"
        data = json.dumps({"fids": list(fids)}, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "cookie": self._cookie,
                "content-type": "application/json",
                "user-agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                set_cookie = _cookie_from_headers(response.headers)
        except urllib.error.HTTPError as error:
            raise ClientError(f"quark HTTP {error.code}: {error.reason}") from None
        except urllib.error.URLError as error:
            raise ClientError(f"quark connection failed: {error.reason}") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("quark returned invalid JSON") from None
        except TimeoutError:
            raise ClientError("quark request timed out") from None
        if not isinstance(payload, dict):
            raise ClientError("quark returned unexpected payload")
        if int(payload.get("code") or 0) != 0:
            message = str(payload.get("message") or "quark request failed")
            raise ClientError(message)
        return payload, set_cookie


def _cookie_from_headers(headers) -> str:
    """Build a Cookie header value from Set-Cookie response headers."""
    if headers is None:
        return ""
    values: list[str] = []
    getter = getattr(headers, "get_all", None)
    if callable(getter):
        raw_list = getter("Set-Cookie") or getter("set-cookie") or []
    else:
        single = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        raw_list = [single] if single else []
    for raw in raw_list:
        part = str(raw or "").split(";", 1)[0].strip()
        if part and "=" in part:
            values.append(part)
    if not values:
        return ""
    # Keep last occurrence per cookie name.
    merged: dict[str, str] = {}
    for item in values:
        name, _, value = item.partition("=")
        merged[name.strip()] = value.strip()
    return "; ".join(f"{name}={value}" for name, value in merged.items())
