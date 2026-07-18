import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


class ClientError(RuntimeError):
    pass


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

    def get_share(self, share_url: str, show_all: bool = True) -> dict:
        data = self._request(
            "/get_share_detail",
            method="POST",
            body={"shareurl": share_url},
        )
        if not show_all and isinstance(data, dict):
            data = dict(data)
            data["list"] = list(data.get("list", []))[:10]
        return data

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

