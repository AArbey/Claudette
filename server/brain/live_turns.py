import secrets
import threading
from copy import deepcopy
from typing import Any

from server.brain.utils import utc_now

class LiveTurns:
    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._conditions: dict[str, threading.Condition] = {}
        self._revisions: dict[str, int] = {}
        self._turns: dict[str, dict[str, Any]] = {}
        self._generation_ids: dict[str, str] = {}

    def _condition(self, session_id: str) -> threading.Condition:
        return self._conditions.setdefault(
            session_id, threading.Condition(self._guard)
        )

    def revision(self, session_id: str) -> int:
        with self._guard:
            return self._revisions.get(session_id, 0)

    def wait_for_change(
        self, session_id: str, revision: int, timeout: float
    ) -> tuple[bool, int]:
        with self._guard:
            condition = self._condition(session_id)
            changed = condition.wait_for(
                lambda: self._revisions.get(session_id, 0) != revision,
                timeout=timeout,
            )
            return changed, self._revisions.get(session_id, 0)

    def start(
        self, session_id: str, transient_messages: list[dict[str, Any]]
    ) -> str:
        with self._guard:
            generation_id = self._generation_ids.setdefault(
                session_id, secrets.token_urlsafe(12)
            )
            self._turns[session_id] = {
                "active": True,
                "started_at": utc_now(),
                "generation_id": generation_id,
                "reasoning": "",
                "content": "",
                "transient_messages": transient_messages,
                "activity": {"phase": "waiting", "tools": []},
            }
            self.changed(session_id)
            return generation_id

    def changed(self, session_id: str) -> None:
        with self._guard:
            self._revisions[session_id] = self._revisions.get(session_id, 0) + 1
            self._condition(session_id).notify_all()

    def append(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        with self._guard:
            turn = self._turns.get(session_id)
            if turn is None:
                return
            if event in {"reasoning", "content"}:
                delta = data.get("delta")
                if not isinstance(delta, str):
                    return
                turn[event] += delta
            elif event == "research":
                turn["research"] = deepcopy(data)
            elif event == "activity":
                phase = data.get("phase")
                tools = data.get("tools", turn["activity"].get("tools", []))
                if not isinstance(phase, str) or not isinstance(tools, list):
                    return
                turn["activity"] = {"phase": phase, "tools": deepcopy(tools)}
            else:
                return
            self.changed(session_id)

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._guard:
            turn = self._turns.get(session_id)
            if turn is None:
                return None
            return deepcopy(turn)

    def remove(self, session_id: str) -> None:
        with self._guard:
            self._turns.pop(session_id, None)
            self.changed(session_id)

    def finish(self, session_id: str) -> None:
        with self._guard:
            self._turns.pop(session_id, None)
            self._generation_ids.pop(session_id, None)
            self.changed(session_id)

