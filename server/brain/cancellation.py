import threading
from typing import Any
from urllib.request import urlopen

from requests import Request

from server.brain.errors import TurnCancelled


class TurnCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()
        self._guard = threading.Lock()
        self._response: Any = None
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TurnCancelled(self._reason or "stop")

    def bind(self, response: Any) -> None:
        with self._guard:
            self._response = response
            cancelled = self.cancelled
        if cancelled:
            response.close()
            raise TurnCancelled(self._reason or "stop")

    def unbind(self, response: Any) -> None:
        with self._guard:
            if self._response is response:
                self._response = None

    @property
    def reason(self) -> str | None:
        with self._guard:
            return self._reason

    def consume_refresh(self) -> bool:
        with self._guard:
            if self._reason != "refresh":
                return False
            self._reason = None
            self._response = None
            self._event.clear()
            return True

    def cancel(self, reason: str = "stop") -> None:
        with self._guard:
            if self._reason is None or reason == "stop":
                self._reason = reason
            response = self._response
            self._event.set()
        if response is not None:
            try:
                response.close()
            except (OSError, ValueError):
                pass


def cancellable_urlopen(
    request: Request, timeout: float, cancellation: TurnCancellation | None
) -> Any:
    """Open an upstream request without making Stop wait for response headers."""
    if cancellation is None:
        return urlopen(request, timeout=timeout)

    finished = threading.Event()
    result: dict[str, Any] = {}

    def open_request() -> None:
        try:
            response = urlopen(request, timeout=timeout)
            if cancellation.cancelled:
                response.close()
                result["error"] = TurnCancelled()
            else:
                result["response"] = response
        except Exception as error:
            result["error"] = error
        finally:
            finished.set()

    threading.Thread(
        target=open_request, name="llm-response-open", daemon=True
    ).start()
    while not finished.wait(.05):
        cancellation.raise_if_cancelled()
    if cancellation.cancelled:
        response = result.get("response")
        if response is not None:
            response.close()
        raise TurnCancelled()
    error = result.get("error")
    if error is not None:
        raise error
    return result["response"]
