

import datetime
from pathlib import Path
from time import timezone
from .errors import BrainError
import json
import sys
from typing import Any


def log_event(event: str, **fields: Any) -> None:
    record = {"timestamp": utc_now(), "event": event, **fields}
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")), file=sys.stderr)



def load_system_prompt(
    prompt_path: Path, knowledge_dir: Path, max_knowledge_bytes: int
) -> str:
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise BrainError(f"cannot read system prompt: {error}") from error
    except UnicodeError as error:
        raise BrainError(f"system prompt is not valid UTF-8: {error}") from error

    if not prompt:
        raise BrainError("system prompt must not be empty")
    if not knowledge_dir.is_dir():
        raise BrainError(f"knowledge directory does not exist: {knowledge_dir}")

    files = sorted(
        path
        for path in knowledge_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    )
    sections: list[str] = []
    total_bytes = 0
    for path in files:
        try:
            data = path.read_bytes()
            text = data.decode("utf-8").strip()
        except OSError as error:
            raise BrainError(f"cannot read knowledge file {path}: {error}") from error
        except UnicodeError as error:
            raise BrainError(f"knowledge file is not valid UTF-8: {path}") from error

        total_bytes += len(data)
        if total_bytes > max_knowledge_bytes:
            raise BrainError(
                f"knowledge files exceed MAX_KNOWLEDGE_BYTES ({max_knowledge_bytes})"
            )
        if text:
            relative_name = path.relative_to(knowledge_dir).as_posix()
            sections.append(f"## Knowledge: {relative_name}\n\n{text}")

    if sections:
        prompt += "\n\n# Trusted operator knowledge\n\n" + "\n\n".join(sections)
    return prompt

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def routing_arguments(arguments: Any) -> tuple[dict[str, Any], str | None]:
    if not isinstance(arguments, dict):
        raise BrainError("tool arguments must be an object")
    clean = dict(arguments)
    runner_id = clean.pop("runner_id", None)
    if "runner_id" in arguments and (not isinstance(runner_id, str) or not runner_id.strip()):
        raise BrainError("runner_id must be a non-empty registered runner ID")
    return clean, runner_id