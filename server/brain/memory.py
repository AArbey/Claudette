from .errors import BrainError
from typing import Any


def clean_memory_key(value: Any) -> str:
    if not isinstance(value, str):
        raise BrainError("memory key must be a string")
    key = " ".join(value.strip().split())
    if not key or len(key) > 120 or any(character in key for character in "\r\n\0"):
        raise BrainError("memory key must contain 1-120 characters on one line")
    return key


def clean_memory_value(value: Any) -> str:
    if not isinstance(value, str):
        raise BrainError("memory value must be a string")
    text = value.strip()
    if not text or len(text) > 4000 or "\0" in text:
        raise BrainError("memory value must contain 1-4000 characters")
    return text