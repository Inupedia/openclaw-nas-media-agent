import json
import urllib.error
import urllib.request
from typing import Callable

from qas_client import ClientError


STATUS_FIELDS = [
    "gid",
    "status",
    "totalLength",
    "completedLength",
    "downloadSpeed",
    "files",
    "dir",
    "errorCode",
    "errorMessage",
]


class Aria2Client:
    def __init__(
        self,
        rpc_url: str,
        secret: str,
        *,
        opener: Callable = urllib.request.urlopen,
        timeout: int = 10,
    ):
        self.rpc_url = rpc_url
        self._secret = secret
        self._opener = opener
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Aria2Client(rpc_url={self.rpc_url!r}, secret='***')"

    def _call(self, method: str, params: list | None = None):
        rpc_params = [f"token:{self._secret}", *(params or [])]
        payload = {
            "jsonrpc": "2.0",
            "id": "resource-download-agent",
            "method": f"aria2.{method}",
            "params": rpc_params,
        }
        request = urllib.request.Request(
            self.rpc_url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ClientError(f"aria2 HTTP {error.code}: {error.reason}") from None
        except urllib.error.URLError as error:
            raise ClientError(f"aria2 connection failed: {error.reason}") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("aria2 returned invalid JSON") from None
        except TimeoutError:
            raise ClientError("aria2 request timed out") from None
        except Exception as error:
            message = str(error).replace(self._secret, "***")
            raise ClientError(f"aria2 request failed: {message}") from None

        if "error" in result:
            error = result["error"]
            message = str(error.get("message", "unknown error")).replace(
                self._secret,
                "***",
            )
            raise ClientError(
                f"aria2 RPC {error.get('code', 'error')}: {message}"
            )
        return result.get("result")

    def get_version(self) -> dict:
        return self._call("getVersion")

    def tell_active(self) -> list[dict]:
        return list(self._call("tellActive", [STATUS_FIELDS]) or [])

    def tell_waiting(self, offset: int = 0, count: int = 100) -> list[dict]:
        return list(
            self._call("tellWaiting", [offset, count, STATUS_FIELDS]) or []
        )

    def tell_stopped(self, offset: int = 0, count: int = 100) -> list[dict]:
        return list(
            self._call("tellStopped", [offset, count, STATUS_FIELDS]) or []
        )

    def pause(self, gid: str) -> str:
        return str(self._call("pause", [gid]))

    def unpause(self, gid: str) -> str:
        return str(self._call("unpause", [gid]))

    def remove(self, gid: str) -> str:
        return str(self._call("remove", [gid]))

    def remove_result(self, gid: str) -> str:
        return str(self._call("removeDownloadResult", [gid]))
