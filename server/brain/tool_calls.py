
from typing import Any

from server.brain.errors import BrainError


def validate_tool_calls(tool_calls: list[dict[str, Any]]) -> None:
    if len(tool_calls) > 64:
        raise BrainError("upstream returned too many tool calls")
    seen_ids: set[str] = set()
    for call in tool_calls:
        function = call.get("function")
        if (
            not isinstance(call.get("id"), str)
            or not call["id"]
            or call.get("type") != "function"
            or not isinstance(function, dict)
            or not isinstance(function.get("name"), str)
            or not function["name"]
            or not isinstance(function.get("arguments"), str)
        ):
            raise BrainError("upstream returned incomplete tool calls")
        if call["id"] in seen_ids:
            raise BrainError("upstream returned duplicate tool call IDs")
        seen_ids.add(call["id"])


def merge_tool_call_deltas(
    current: list[dict[str, Any] | None], deltas: Any
) -> list[dict[str, Any] | None]:
    if not isinstance(deltas, list):
        raise BrainError("invalid tool call delta")

    for delta in deltas:
        if not isinstance(delta, dict):
            raise BrainError("invalid tool call delta")
        index = delta.get("index")
        function = delta.get("function", {})
        call_id = delta.get("id", "")
        call_type = delta.get("type", "function")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 0
            or index >= 64
            or not isinstance(call_id, str)
            or call_type != "function"
            or not isinstance(function, dict)
            or not isinstance(function.get("name", ""), str)
            or not isinstance(function.get("arguments", ""), str)
        ):
            raise BrainError("invalid tool call delta")

        while len(current) <= index:
            current.append(None)
        if current[index] is None:
            current[index] = {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
        item = current[index]
        assert item is not None
        if call_id:
            item["id"] = call_id
        item["function"]["name"] += function.get("name", "")
        item["function"]["arguments"] += function.get("arguments", "")
    return current

