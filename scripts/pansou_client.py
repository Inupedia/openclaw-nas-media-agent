import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


class PanSouError(RuntimeError):
    pass


_ATTACHED_SEQUEL = re.compile(r"^(?P<title>.*\D)(?P<number>\d{1,2})$")


def query_variants(query: str) -> list[str]:
    exact = str(query).strip()
    if not exact:
        return []
    match = _ATTACHED_SEQUEL.fullmatch(exact)
    if not match:
        return [exact]
    title = match.group("title").strip()
    number = int(match.group("number"))
    if not title or not 1 <= number <= 20:
        return [exact]
    return [
        exact,
        title,
        f"{title} 第{number}季",
        f"{title} S{number:02d}",
    ]


def normalize_quark_url(value: object) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname != "pan.quark.cn":
        return None
    path = parsed.path.rstrip("/")
    if not path.startswith("/s/") or len(path) <= len("/s/"):
        return None
    return urllib.parse.urlunsplit(("https", "pan.quark.cn", path, "", ""))


class PanSouClient:
    def __init__(
        self,
        base_url: str,
        *,
        opener: Callable = urllib.request.urlopen,
        timeout: int = 30,
        max_candidates: int = 50,
    ):
        self._base_url = str(base_url).rstrip("/")
        self._opener = opener
        self.timeout = timeout
        self.max_candidates = (
            int(max_candidates) if 1 <= int(max_candidates) <= 100 else 50
        )

    def __repr__(self) -> str:
        return (
            "PanSouClient(base_url='***', "
            f"timeout={self.timeout!r}, max_candidates={self.max_candidates!r})"
        )

    def search(self, query: str) -> list[dict]:
        params = urllib.parse.urlencode(
            {
                "kw": query,
                "cloud_types": "quark",
                "res": "all",
                "src": "all",
            }
        )
        request = urllib.request.Request(
            f"{self._base_url}/api/search?{params}",
            method="GET",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                content = response.read()
        except urllib.error.HTTPError as error:
            raise PanSouError(f"PanSou HTTP {error.code}") from None
        except urllib.error.URLError:
            raise PanSouError("PanSou connection failed") from None
        except TimeoutError:
            raise PanSouError("PanSou request timed out") from None
        except Exception:
            raise PanSouError("PanSou request failed") from None

        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PanSouError("PanSou returned invalid JSON") from None

        data = payload.get("data") if isinstance(payload, dict) else None
        merged = data.get("merged_by_type") if isinstance(data, dict) else None
        items = merged.get("quark") if isinstance(merged, dict) else None
        if not isinstance(items, list):
            raise PanSouError("PanSou returned an invalid response") from None

        results = []
        seen = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            share_url = normalize_quark_url(item.get("url"))
            if not share_url or share_url in seen:
                continue
            seen.add(share_url)
            results.append(
                {
                    "taskname": str(item.get("note") or "PanSou candidate"),
                    "shareurl": share_url,
                    "discoverySource": "pansou",
                    "datetime": str(item.get("datetime") or ""),
                }
            )
            if len(results) >= self.max_candidates:
                break
        return results
