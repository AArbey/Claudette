#!/usr/bin/env python3
"""Small, on-demand text editor shared by terminal clients and runners."""

from __future__ import annotations

import argparse
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from contextlib import contextmanager

MAX_FILE_BYTES = 256 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(data) > MAX_REQUEST_BYTES:
            raise FileError("Request too large", 413)
        result = perform(Path(args.state_dir), json.loads(data))
    except FileError as error:
        result = {"ok": False, "error": str(error), "status": error.status}
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {"ok": False, "error": str(error), "status": 400}
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
