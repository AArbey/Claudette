#!/usr/bin/env python3
"""Run one approved command and publish bounded progress to Brain."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


HEARTBEAT_SECONDS = 2.0
STOP_GRACE_SECONDS = 1.0


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def parent_start_time(pid: int) -> str | None:
    try:
        # comm may contain spaces and parentheses; fields after final ')' start at field 3.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19]  # field 22: process start ticks
    except (OSError, IndexError):
        return None


class OutputBuffer:
    def __init__(self, limit: int):
        if not 1 <= limit <= 65536:
            raise ValueError("max_output_bytes must be between 1 and 65536")
        self.limit = limit
        self.head_limit = min(limit, 8192, max(1, limit // 8))
        self.tail_limit = limit - self.head_limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, data: bytes) -> None:
        self.total += len(data)
        remaining = self.head_limit - len(self.head)
        if remaining:
            self.head.extend(data[:remaining])
            data = data[remaining:]
        if data:
            self.tail.extend(data)
            if len(self.tail) > self.tail_limit:
                del self.tail[: len(self.tail) - self.tail_limit]

    @property
    def truncated(self) -> bool:
        return self.total > self.limit

    def text(self) -> str:
        if not self.truncated:
            rendered = bytes(self.head) + bytes(self.tail)
        else:
            marker = b"\n[output truncated]\n"
            if self.limit <= len(marker) + 2:
                rendered = bytes(self.head) + bytes(self.tail)
            else:
                available = self.limit - len(marker)
                head_size = min(len(self.head), max(1, available // 8))
                tail_size = available - head_size
                rendered = (
                    bytes(self.head[:head_size]) + marker
                    + (bytes(self.tail[-tail_size:]) if tail_size else b"")
                )
        text = rendered.decode("utf-8", errors="replace")
        return text.encode("utf-8")[: self.limit].decode("utf-8", errors="ignore")


def post_update(request: dict[str, Any], state: dict[str, Any]) -> tuple[bool, int | None]:
    url = request["brain_url"].rstrip("/") + f"/v1/command-jobs/{request['job_id']}/updates"
    body = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode()
    http_request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {request['job_token']}",
            "Content-Type": "application/json",
            "Connection": "close",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(http_request, timeout=5) as response:
            payload = json.load(response)
        if not isinstance(payload, dict):
            raise ValueError("Brain returned invalid job control")
        stop = payload.get("stop_requested") is True
        maximum = payload.get("max_runtime_seconds", request["max_runtime_seconds"])
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            raise ValueError("Brain returned invalid job lease")
        return stop, maximum
    except (OSError, TimeoutError, urllib.error.URLError, ValueError, json.JSONDecodeError):
        return False, None


def run(request_path: Path, status_path: Path, client_mode: bool) -> int:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    command = request["command"]
    argv = [command["program"], *command["arguments"]]
    cwd = request["cwd"]
    output = OutputBuffer(request["max_output_bytes"])
    started = time.monotonic()
    parent_pid = request.get("parent_pid") if client_mode else None
    parent_started = (
        parent_start_time(parent_pid) if isinstance(parent_pid, int) else None
    )
    stop_reason = ""
    stop_event = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stop_event, stop_reason
        stop_event = True
        stop_reason = "terminal client exited" if client_mode else "stop requested"

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    env = request.get("environment")
    if not isinstance(env, dict):
        env = {
            "PATH": os.environ.get("RUNNER_PATH", os.defpath),
            "HOME": os.environ.get("RUNNER_HOME", cwd),
            "PWD": cwd,
            "USER": os.environ.get("RUNNER_USER", ""),
            "LOGNAME": os.environ.get("RUNNER_USER", ""),
            "LANG": "C.UTF-8",
            "TERM": "dumb",
        }
    if command["program"] == "cd":
        try:
            if len(command["arguments"]) != 1:
                raise ValueError("cd requires one path")
            target = (Path(cwd) / command["arguments"][0]).resolve(strict=True)
            if not target.is_dir() or not os.access(target, os.X_OK):
                raise OSError("target is not an accessible directory")
            cwd = str(target)
            output.add(cwd.encode())
            result = {"sequence": 1, "state": "completed", "output": output.text(),
                      "total_bytes": output.total, "truncated": output.truncated,
                      "exit_code": 0, "cwd": cwd}
        except (OSError, ValueError) as error:
            output.add(f"cd failed: {error}".encode())
            result = {"sequence": 1, "state": "completed", "output": output.text(),
                      "total_bytes": output.total, "truncated": output.truncated,
                      "exit_code": 1, "cwd": cwd}
        atomic_json(status_path, result)
        post_update(request, result)
        return result["exit_code"]
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError) as error:
        output.add(str(error).encode())
        final = {
            "sequence": 1, "state": "completed", "output": output.text(),
            "total_bytes": output.total, "truncated": output.truncated,
            "exit_code": 127, "cwd": cwd,
        }
        atomic_json(status_path, final)
        post_update(request, final)
        return 127

    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    sequence = 0
    last_sent = 0.0
    last_output = ""
    last_total = 0
    stop_sent_at: float | None = None
    max_runtime = request["max_runtime_seconds"]
    final_state = "completed"

    def publish(state: str, exit_code: int | None = None) -> None:
        nonlocal sequence, last_sent, last_output, last_total, max_runtime
        nonlocal stop_event, stop_reason
        snapshot = output.text()
        changed = snapshot != last_output or output.total != last_total
        if not changed and state == "running" and time.monotonic() - last_sent < HEARTBEAT_SECONDS:
            return
        sequence += 1
        payload: dict[str, Any] = {
            "sequence": sequence,
            "state": state,
            "output": snapshot,
            "total_bytes": output.total,
            "truncated": output.truncated,
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        payload["cwd"] = cwd
        atomic_json(status_path, payload)
        stop, new_max_runtime = post_update(request, payload)
        if new_max_runtime is not None:
            max_runtime = new_max_runtime
        if stop:
            stop_event = True
            stop_reason = "stop requested"
        last_sent = time.monotonic()
        last_output = snapshot
        last_total = output.total

    publish("running")
    while True:
        now = time.monotonic()
        if client_mode and parent_pid is not None:
            current_parent_start = parent_start_time(parent_pid)
            if current_parent_start is None or current_parent_start != parent_started:
                stop_event = True
                stop_reason = "terminal client exited"
        if now - started >= max_runtime and not stop_event:
            stop_event = True
            stop_reason = "runtime lease expired"
            final_state = "timed_out"
        if stop_event:
            if stop_sent_at is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                stop_sent_at = now
            elif now - stop_sent_at >= STOP_GRACE_SECONDS:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for key, _mask in selector.select(timeout=0.25):
            try:
                block = os.read(key.fd, 65536)
            except OSError:
                block = b""
            if block:
                output.add(block)
            else:
                selector.unregister(key.fileobj)
        if process.poll() is not None and not selector.get_map():
            break
        if output.text() != last_output:
            publish("running")
        elif now - last_sent >= HEARTBEAT_SECONDS:
            publish("running")

    selector.close()
    return_code = process.wait()
    if final_state != "timed_out" and stop_sent_at is not None:
        final_state = "stopped"
    if return_code < 0:
        exit_code = 128 - return_code
    else:
        exit_code = return_code
    if final_state == "timed_out":
        exit_code = 124
    publish(final_state, exit_code)
    if stop_reason:
        current = json.loads(status_path.read_text(encoding="utf-8"))
        current["reason"] = stop_reason
        atomic_json(status_path, current)
    return exit_code


def inspect(state_dir: Path) -> int:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = state_dir / "worker.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "running"}))
            return 0
        status_path = state_dir / "status.json"
        if status_path.exists():
            snapshot = json.loads(status_path.read_text(encoding="utf-8"))
            if snapshot.get("state") in {"completed", "stopped", "timed_out"}:
                snapshot["status"] = snapshot["state"]
            else:
                snapshot["status"] = "outcome_unknown"
            print(json.dumps(snapshot))
        else:
            print(json.dumps({"status": "outcome_unknown"}))
        return 0
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "inspect"))
    parser.add_argument("--request", type=Path)
    parser.add_argument("--state-dir", required=True, type=Path)
    parser.add_argument("--client-mode", action="store_true")
    args = parser.parse_args()
    if args.mode == "inspect":
        return inspect(args.state_dir)
    if args.request is None:
        parser.error("run mode requires --request")
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = args.state_dir / "worker.lock"
    with lock_path.open("a+b") as lock:
        import fcntl
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        launched_path = args.state_dir / "launched"
        if launched_path.exists() or (args.state_dir / "status.json").exists():
            return 0
        descriptor = os.open(launched_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        return run(args.request, args.state_dir / "status.json", args.client_mode)


if __name__ == "__main__":
    raise SystemExit(main())
