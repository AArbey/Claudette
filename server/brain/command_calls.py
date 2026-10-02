import json
import os
from shlex import shlex
from typing import Any

from server.brain.utils import routing_arguments
from .errors import BrainError
import re

def validate_trusted_prefixes(prefixes: Any) -> None:
    if not isinstance(prefixes, list) or any(
        not isinstance(prefix, list)
        or not 1 <= len(prefix) <= 65
        or any(
            not isinstance(token, str)
            or not token
            or any(character in token for character in "\r\n\0")
            for token in prefix
        )
        for prefix in prefixes
    ):
        raise BrainError("trusted prefixes must be arrays of 1-65 non-empty argv tokens")


def validate_argv(argv: Any) -> None:
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 65
        or not isinstance(argv[0], str)
        or not argv[0]
        or any(
            not isinstance(token, str)
            or any(character in token for character in "\r\n\0")
            for token in argv
        )
    ):
        raise BrainError(
            "argv must contain a non-empty program and up to 64 string arguments"
        )


def shell_command_wrapper(argv: list[str]) -> bool:
    shells = {"bash", "sh", "dash", "zsh", "ksh", "fish"}
    for index, token in enumerate(argv[:-1]):
        if os.path.basename(token) not in shells:
            continue
        if any(re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*|--command", option)
               for option in argv[index + 1:]):
            return True
    return False


def split_model_command(command: Any) -> list[str]:
    if not isinstance(command, str) or not command.strip():
        raise BrainError("command must be a non-empty string")
    quote = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char == quote:
            quote = None
        elif quote is None and char in "\"'":
            quote = char
        elif quote is None and char in "|&;<>":
            raise BrainError("Shell operators and redirects are PROHIBITED: no pipes (|), redirects (>, <, 2>), &&, ||, ;, or &. Use only direct commands with standard arguments. find -o (OR predicate) is allowed.")
    try:
        argv = shlex.split(command, comments=False, posix=True)
    except ValueError as error:
        raise BrainError("command has invalid quoting") from error
    validate_argv(argv)
    if shell_command_wrapper(argv):
        raise BrainError("shell -c wrappers are PROHIBITED: use direct commands only (no bash -lc, sh -c, etc.)")
    return argv


def model_command_arguments(arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict) or set(arguments) != {"command", "reason"}:
        raise BrainError("run_command requires a command string and reason")
    if not isinstance(arguments["reason"], str):
        raise BrainError("run_command reason must be a string")
    argv = split_model_command(arguments["command"])
    return {
        "program": argv[0], "arguments": argv[1:],
        "reason": arguments["reason"], "trust_prefix": argv,
    }


def normalize_model_command_calls(calls: list[dict[str, Any]]) -> None:
    """Keep existing client argv protocol while exposing a smaller model tool."""
    for call in calls:
        function = call["function"]
        if function["name"] != "run_command":
            continue
        try:
            model_args, runner_id = routing_arguments(json.loads(function["arguments"]))
            if not isinstance(model_args, dict) or set(model_args) != {"command", "reason"}:
                continue
            command = model_command_arguments(model_args)
            if runner_id is not None:
                command["runner_id"] = runner_id
        except (json.JSONDecodeError, BrainError):
            continue
        function["arguments"] = json.dumps(
            command, ensure_ascii=False, separators=(",", ":")
        )


def parse_command_call(call: dict[str, Any]) -> dict[str, Any]:
    try:
        if call["function"]["name"] != "run_command":
            raise BrainError("unsupported remote tool")
        command, _ = routing_arguments(json.loads(call["function"]["arguments"]))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise BrainError("run_command arguments must be valid JSON with command and reason") from error
    if isinstance(command, dict) and set(command) == {"command", "reason"}:
        return model_command_arguments(command)
    if not isinstance(command, dict) or set(command) != {
        "program", "arguments", "reason", "trust_prefix",
    }:
        raise BrainError("run_command requires a command string and reason")
    if not isinstance(command["reason"], str):
        raise BrainError("invalid remote command reason")
    argv = [command.get("program"), *command.get("arguments", [])] \
        if isinstance(command.get("arguments"), list) else []
    validate_argv(argv)
    if shell_command_wrapper(argv):
        raise BrainError("shell -c wrappers are PROHIBITED: use direct commands only (no bash -lc, sh -c, etc.)")
    validate_trusted_prefixes([command["trust_prefix"]])
    if argv[:len(command["trust_prefix"])] != command["trust_prefix"]:
        raise BrainError("trusted prefix does not match remote command")
    return command



def parse_file_call(call: dict[str, Any]) -> dict[str, Any]:
    try:
        arguments, _ = routing_arguments(json.loads(call["function"]["arguments"]))
    except (KeyError, TypeError, ValueError) as error:
        raise BrainError("invalid file edit arguments") from error
    if (not isinstance(arguments, dict) or set(arguments) != {
        "path", "operation", "old_text", "new_text", "reason"
    } or any(not isinstance(value, str) for value in arguments.values())):
        raise BrainError("invalid file edit arguments")
    if (arguments["operation"] not in {"create", "replace", "delete"}
        or not arguments["path"] or any(c in arguments["path"] for c in "\r\n\0")
        or any("\0" in arguments[key] or len(arguments[key].encode("utf-8")) > 262144
               for key in ("old_text", "new_text"))):
        raise BrainError("invalid file edit operation, path, or text (256 KiB limit)")
    return arguments


def parse_file_inspect_call(call: dict[str, Any]) -> dict[str, Any]:
    name = call.get("function", {}).get("name")
    allowed = {
        "find_files": ({"glob"}, {"path", "max_results"}),
        "search_text": ({"pattern"}, {"path", "file_glob", "case_sensitive", "max_results"}),
        "read_file": ({"path"}, {"start_line", "max_lines"}),
    }
    if name not in allowed:
        raise BrainError("unknown file inspection tool")
    try:
        arguments, _ = routing_arguments(json.loads(call["function"]["arguments"]))
    except (KeyError, TypeError, ValueError) as error:
        raise BrainError("invalid file inspection arguments") from error
    required, optional = allowed[name]
    if not isinstance(arguments, dict) or not required <= arguments.keys() or not arguments.keys() <= required | optional:
        raise BrainError("invalid file inspection arguments")
    return {"action": name, **arguments}


