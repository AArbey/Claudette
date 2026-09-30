#!/usr/bin/env python3
"""Small, on-demand text editor shared by terminal clients and runners."""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
from contextlib import contextmanager

MAX_FILE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_INSPECT_FILE_BYTES = 1024 * 1024
MAX_INSPECT_OUTPUT_BYTES = 48 * 1024
MAX_SEARCH_FILES = 2000
MAX_SEARCH_BYTES = 16 * 1024 * 1024
MAX_SEARCH_ENTRIES = 10000
SKIP_DIRECTORIES = {".git", ".venv", "node_modules", "__pycache__", ".cache", "dist", "build"}


class FileError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def digest(content: str | None) -> str | None:
    return hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None


def text_value(value):
    if not isinstance(value, str) or "\0" in value:
        raise FileError("Only UTF-8 text without NUL bytes is supported")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise FileError("Invalid UTF-8 text") from error
    if length > MAX_FILE_BYTES:
        raise FileError("File exceeds 256 KiB limit", 413)
    return value


def read_file(path: Path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"content": None, "mode": 0o644, "uid": os.geteuid(), "gid": os.getegid()}
    except OSError as error:
        raise FileError(f"Cannot read file: {error}", 409) from error
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise FileError("Only regular files with one hard link are supported")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise FileError("File exceeds 256 KiB limit", 413)
    try:
        content = text_value(data.decode("utf-8"))
    except UnicodeError as error:
        raise FileError("Only UTF-8 text files are supported") from error
    return {"content": content, "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def atomic_json(path: Path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".record-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_dir(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_file(path: Path, content: str | None, attributes, expected_hash):
    # Recheck immediately before committing. All helper writers share a path lock.
    if digest(read_file(path)["content"]) != expected_hash:
        raise FileError("File changed since review. Reload before saving or restoring.", 409)
    if content is None:
        if path.exists():
            path.unlink()
            sync_dir(path.parent)
        return
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.ai-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            info = os.fstat(stream.fileno())
            if (info.st_uid, info.st_gid) != (attributes["uid"], attributes["gid"]):
                os.fchown(stream.fileno(), attributes["uid"], attributes["gid"])
            os.fchmod(stream.fileno(), attributes["mode"])
            stream.flush()
            os.fsync(stream.fileno())
        if digest(read_file(path)["content"]) != expected_hash:
            raise FileError("File changed during edit. Nothing overwritten.", 409)
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def changes(before, after, path):
    lines = list(difflib.unified_diff(
        (before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True),
        fromfile=path if before is not None else "/dev/null",
        tofile=path if after is not None else "/dev/null", n=3,
    ))
    # Preserve missing final newline in both display and saved text.
    patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
    return {"diff": patch,
            "added": sum(line.startswith("+") for line in lines[2:]),
            "removed": sum(line.startswith("-") for line in lines[2:])}


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
        raise FileError("Invalid file operation ID")
    return value


@contextmanager
def locked(root: Path, key: str):
    lock_path = root / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def resolve_path(cwd, value):
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise FileError("Absolute working directory required")
    if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
        raise FileError("Invalid file path")
    raw = Path(value) if os.path.isabs(value) else Path(cwd) / value
    if raw.is_symlink():
        raise FileError("Editing symbolic links is unsupported")
    parent = raw.parent.resolve(strict=True)
    return parent / raw.name


def inspect_path(cwd, value):
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise FileError("Absolute working directory required")
    if not isinstance(value, str) or not value or any(c in value for c in "\0\r\n"):
        raise FileError("Invalid file path")
    path = Path(value) if os.path.isabs(value) else Path(cwd) / value
    if path.is_symlink():
        raise FileError("Symbolic links are unsupported")
    try:
        return path.resolve(strict=True)
    except FileNotFoundError as error:
        raise FileError("File or directory not found", 404) from error
    except OSError as error:
        raise FileError(f"Cannot access path: {error}", 409) from error


def inspect_text(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise FileError("Only regular files are supported")
            if info.st_size > MAX_INSPECT_FILE_BYTES:
                raise FileError("File exceeds 1 MiB inspection limit", 413)
            data = stream.read(MAX_INSPECT_FILE_BYTES + 1)
    except OSError as error:
        raise FileError(f"Cannot read file: {error}", 409) from error
    if len(data) > MAX_INSPECT_FILE_BYTES or b"\0" in data:
        raise FileError("File is too large or binary", 413)
    try:
        return data.decode("utf-8")
    except UnicodeError as error:
        raise FileError("Only UTF-8 text files are supported") from error


def inspect_integer(request, key, default, maximum):
    value = request.get(key, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise FileError(f"{key} must be between 1 and {maximum}")
    return value


def inspect_string(request, key, default=None):
    value = request.get(key, default)
    if not isinstance(value, str) or not value or len(value) > 500 or "\0" in value:
        raise FileError(f"Invalid {key}")
    return value


def matching_files(root, glob, state):
    if root.is_file():
        if fnmatch.fnmatch(root.name, glob):
            yield root, root.name
        return
    if not root.is_dir():
        raise FileError("Search path must be a regular file or directory")
    entries = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        entries += len(dirs)
        if entries > MAX_SEARCH_ENTRIES:
            state["truncated"] = True
            return
        dirs[:] = sorted(name for name in dirs
            if name not in SKIP_DIRECTORIES and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            entries += 1
            if entries > MAX_SEARCH_ENTRIES:
                state["truncated"] = True
                return
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = str(path.relative_to(root))
            if fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(relative, glob):
                yield path, relative


def inspect(request):
    action = request.get("action")
    cwd = request.get("cwd")
    if action == "read_file":
        path = inspect_path(cwd, inspect_string(request, "path"))
        start = inspect_integer(request, "start_line", 1, 1000000)
        limit = inspect_integer(request, "max_lines", 200, 500)
        lines = inspect_text(path).splitlines(keepends=True)
        selected = []
        size = 0
        for line in lines[start - 1:start - 1 + limit]:
            length = len(line.encode("utf-8"))
            if size + length > MAX_INSPECT_OUTPUT_BYTES:
                break
            selected.append(line)
            size += length
        if not selected and start <= len(lines):
            raise FileError("Line exceeds 48 KiB output limit", 413)
        return {"ok": True, "path": str(path), "start_line": start,
                "end_line": start + len(selected) - 1, "total_lines": len(lines),
                "content": "".join(selected),
                "truncated": start - 1 + len(selected) < len(lines)}
    if action not in {"find_files", "search_text"}:
        raise FileError("Unsupported file action")
    root = inspect_path(cwd, inspect_string(request, "path", "."))
    limit = inspect_integer(request, "max_results", 100, 200)
    matches = []
    state = {"truncated": False}
    output_size = 0
    if action == "find_files":
        glob = inspect_string(request, "glob")
        for path, _ in matching_files(root, glob, state):
            item = str(path)
            if len(matches) >= limit or output_size + len(item.encode("utf-8")) > MAX_INSPECT_OUTPUT_BYTES:
                state["truncated"] = True
                break
            matches.append(item)
            output_size += len(item.encode("utf-8"))
        return {"ok": True, "path": str(root), "matches": matches,
                "truncated": state["truncated"]}
    pattern = inspect_string(request, "pattern")
    glob = inspect_string(request, "file_glob", "*")
    if type(request.get("case_sensitive", True)) is not bool:
        raise FileError("case_sensitive must be boolean")
    try:
        expression = re.compile(pattern, 0 if request.get("case_sensitive", True) else re.IGNORECASE)
    except re.error as error:
        raise FileError(f"Invalid search pattern: {error}") from error
    scanned = 0
    scanned_bytes = 0
    for path, _ in matching_files(root, glob, state):
        scanned += 1
        if scanned > MAX_SEARCH_FILES:
            state["truncated"] = True
            break
        try:
            content = inspect_text(path)
        except FileError:
            continue
        scanned_bytes += len(content.encode("utf-8"))
        if scanned_bytes > MAX_SEARCH_BYTES:
            state["truncated"] = True
            break
        for number, line in enumerate(content.splitlines(), 1):
            if expression.search(line):
                item = {"path": str(path), "line": number, "text": line[:500]}
                size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
                if len(matches) >= limit or output_size + size > MAX_INSPECT_OUTPUT_BYTES:
                    state["truncated"] = True
                    break
                matches.append(item)
                output_size += size
        if state["truncated"]:
            break
    return {"ok": True, "path": str(root), "matches": matches,
            "truncated": state["truncated"]}


def finish_journal(record, record_path):
    journal = record.get("journal")
    if journal is None:
        return
    path = Path(record["path"])
    current_hash = digest(read_file(path)["content"])
    desired = journal["content"]
    desired_hash = digest(desired)
    if current_hash != desired_hash:
        if current_hash != journal["expected_hash"]:
            raise FileError("Interrupted edit conflicts with current file. Nothing overwritten.", 409)
        write_file(path, desired, journal["attributes"], current_hash)
    record["edit"].update(changes(record["before"]["content"], desired, record["path"]))
    record["edit"].update(after_hash=desired_hash, status=journal["status"],
                          manually_edited=journal["manually_edited"])
    record["last_request_id"] = journal["request_id"]
    record["last_request_hash"] = journal["request_hash"]
    record.pop("journal")
    atomic_json(record_path, record)


def perform(root: Path, request):
    if not isinstance(request, dict):
        raise FileError("JSON object required")
    if request.get("action") in {"read_file", "find_files", "search_text"}:
        return inspect(request)
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_uid != os.geteuid():
        raise FileError("File edit state must belong to current user")
    root.chmod(0o700)
    action = request.get("action", "apply")
    edit_id = identifier(request.get("edit_id") or request.get("request_id"))
    record_path = root / (edit_id + ".json")
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    with locked(root, "edit:" + edit_id):
        if record_path.exists():
            if record_path.is_symlink():
                raise FileError("Invalid file edit record")
            record = json.loads(record_path.read_text(encoding="utf-8"))
            path = Path(record["path"])
        elif action == "apply":
            path = resolve_path(request.get("cwd"), request.get("path"))
            record = None
        else:
            raise FileError("Original file backup is unavailable on this host", 404)
        # State files must never be edit targets, including their lock files.
        if path == root.resolve() or root.resolve() in path.parents:
            raise FileError("Cannot edit file-tool state")
        with locked(root, "path:" + str(path)):
            if record:
                finish_journal(record, record_path)
            if action == "apply":
                if record:
                    if record["request_hash"] != request_hash:
                        raise FileError("File edit ID was already used with different arguments", 409)
                    return {"ok": True, "edit": record["edit"]}
                operation = request.get("operation")
                old = text_value(request.get("old_text"))
                new = text_value(request.get("new_text"))
                before = read_file(path)
                original = before["content"]
                if operation == "create":
                    if original is not None or old:
                        raise FileError("Create requires missing file and empty old_text", 409)
                    desired = new
                elif operation == "replace":
                    if original is None or not old or original.count(old) != 1:
                        raise FileError("old_text must match exactly once; read file and supply unique context", 409)
                    desired = text_value(original.replace(old, new, 1))
                elif operation == "delete":
                    if original is None or original != old or new:
                        raise FileError("Delete requires full current content in old_text and empty new_text", 409)
                    desired = None
                else:
                    raise FileError("operation must be create, replace, or delete")
                record = {"path": str(path), "request_hash": request_hash, "before": before,
                          "edit": {"id": edit_id, "path": str(path), "operation": operation,
                                   "before_hash": digest(original)},
                          "journal": {"request_id": edit_id, "request_hash": request_hash,
                                      "expected_hash": digest(original), "content": desired,
                                      "attributes": before, "status": "applied", "manually_edited": False}}
                atomic_json(record_path, record)
                finish_journal(record, record_path)
                return {"ok": True, "edit": record["edit"]}
            if action == "read":
                current = read_file(path)
                return {"ok": True, "edit": record["edit"], "content": current["content"],
                        "hash": digest(current["content"])}
            if action not in {"save", "restore"}:
                raise FileError("Unsupported file action")
            request_id = identifier(request.get("request_id"))
            if record.get("last_request_id") == request_id:
                if record.get("last_request_hash") != request_hash:
                    raise FileError("File action ID already used", 409)
                return {"ok": True, "edit": record["edit"]}
            current = read_file(path)
            if "expected_hash" not in request or digest(current["content"]) != request["expected_hash"]:
                raise FileError("File changed since review. Reload before saving or restoring.", 409)
            desired = record["before"]["content"] if action == "restore" else text_value(request.get("content"))
            attributes = record["before"] if action == "restore" else current
            record["journal"] = {"request_id": request_id, "request_hash": request_hash,
                                 "expected_hash": request["expected_hash"], "content": desired,
                                 "attributes": attributes, "status": "restored" if action == "restore" else "applied",
                                 "manually_edited": action == "save"}
            atomic_json(record_path, record)
            finish_journal(record, record_path)
            return {"ok": True, "edit": record["edit"]}


def inspection_timeout(_signum, _frame):
    raise FileError("File inspection exceeded 10 second limit", 408)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise FileError("Request too large", 413)
        request = json.loads(data)
        if isinstance(request, dict) and request.get("action") in {"read_file", "find_files", "search_text"}:
            signal.signal(signal.SIGALRM, inspection_timeout)
            signal.alarm(10)
        result = perform(Path(args.state_dir), request)
    except FileError as error:
        result = {"ok": False, "error": str(error), "status": error.status}
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"ok": False, "error": str(error), "status": 400}
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
