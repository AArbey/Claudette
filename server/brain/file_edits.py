
import hashlib
import re
from typing import Any

from server.brain.errors import BrainError


def file_request_id(session_id: str, call_id: str) -> str:
    return hashlib.sha256(f"{session_id}:{call_id}".encode()).hexdigest()


def validate_file_edit(data: Any, expected_id: str) -> dict[str, Any]:
    if (not isinstance(data, dict) or data.get("id") != expected_id
        or not isinstance(data.get("path"), str) or not data["path"].startswith("/")
        or any(c in data["path"] for c in "\r\n\0")
        or data.get("operation") not in {"create", "replace", "delete"}
        or data.get("status") not in {"applied", "restored"}
        or not isinstance(data.get("diff"), str) or len(data["diff"].encode()) > 2 * 1024 * 1024
        or any(key not in data or (data[key] is not None and (not isinstance(data[key], str)
               or re.fullmatch(r"[a-f0-9]{64}", data[key]) is None))
               for key in ("before_hash", "after_hash"))
        or any(type(data.get(key)) is not int or data[key] < 0 for key in ("added", "removed"))
        or type(data.get("manually_edited")) is not bool):
        raise BrainError("invalid file edit result")
    return {key: data[key] for key in (
        "id", "path", "operation", "status", "diff", "before_hash", "after_hash",
        "added", "removed", "manually_edited",
    )}


def file_edit_summary(edit: dict[str, Any]) -> str:
    action = "Restored" if edit["status"] == "restored" else "Edited" if edit["manually_edited"] else {
        "create": "Created", "replace": "Edited", "delete": "Deleted",
    }[edit["operation"]]
    return f"{action} {edit['path']} (+{edit['added']} / -{edit['removed']}). Diff and restore available in Web UI."
