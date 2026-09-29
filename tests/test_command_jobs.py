from __future__ import annotations

import importlib.util
import io
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tests.test_brain import brain, config

WORKER = Path(__file__).resolve().parents[1] / "client" / "command_worker.py"


def decision(action, wait=None):
    return {"usual": "yes", "action": action, "reason": "Expected build time",
            "wait_seconds": wait}


class Reviews:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.contexts = []

    def review_command(self, context):
        self.contexts.append(context)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)


class CommandJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = brain.BrainService(
            replace(config(self.root), command_worker_path=WORKER), "system"
        )
        self.session = self.service.store.create()

    def job(self, *, call_id="call_1", command=None, lease=3600, attach=False):
        command = command or {
            "program": sys.executable, "arguments": ["-c", "print('ok')"],
            "reason": "test", "trust_prefix": [sys.executable],
        }
        token = secrets.token_urlsafe(48)
        job_id = secrets.token_urlsafe(24)
        assistant_id = secrets.token_urlsafe(24)
        call = {"id": call_id, "type": "function", "function": {
            "name": "run_command", "arguments": json.dumps(command)}}
        if attach:
            messages = [*self.session["messages"], {
                "role": "assistant", "content": None, "tool_calls": [call],
                "ui": {"command_message_id": assistant_id,
                       "command_jobs": {call_id: job_id}},
            }]
            self.service.store.save(
                self.session["session_id"], messages, "awaiting_tool_results",
                [call], 1,
            )
        item = self.service.store.create_command_job(
            job_id=job_id, session_id=self.session["session_id"],
            branch_id=self.session["active_branch_id"],
            tool_call_id=call_id, assistant_message_id=assistant_id,
            executor="terminal", runner_id=None, client_id=None,
            cwd=str(self.root), command=command,
            approval={"decision": "allowed_once", "prefix": []},
            job_token=token, review_after_seconds=30,
            initial_lease_seconds=lease,
        )
        return item, token, call

    def wait(self, job_id, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.service.store.get_command_job(job_id)
            if predicate(item):
                return item
            time.sleep(0.05)
        self.fail("command job did not reach expected state")

    def test_review_threshold_decisions_and_lease(self):
        item, _, _ = self.job()
        self.assertAlmostEqual(item["next_review_at"] - time.time(), 30, delta=2)
        self.service.store.update_command_job(
            item["job_id"], sequence=1, state="running", output="partial",
            total_bytes=7, truncated=False, exit_code=None, cwd=item["cwd"],
        )
        reviews = Reviews(decision("wait", 30), decision("wait", 60),
                          decision("background"), decision("stop"))
        self.service.llm = reviews
        self.service.review_command_job(item["job_id"])
        first = self.service.store.get_command_job(item["job_id"])
        self.assertFalse(first["background"])
        self.assertAlmostEqual(first["next_review_at"] - time.time(), 30, delta=2)
        self.service.review_command_job(item["job_id"])
        self.assertAlmostEqual(
            self.service.store.get_command_job(item["job_id"])["next_review_at"]
            - time.time(), 60, delta=2,
        )
        self.service.review_command_job(item["job_id"])
        self.assertTrue(self.service.store.get_command_job(item["job_id"])["background"])
        self.service.review_command_job(item["job_id"])
        self.assertTrue(self.service.store.get_command_job(item["job_id"])["stop_requested"])
        self.assertEqual(reviews.contexts[0]["output"], "partial")

        renewal, _, _ = self.job(call_id="call_2")
        old = (datetime.now(timezone.utc) - timedelta(seconds=3550)).isoformat()
        with self.service.store.connect() as connection, connection:
            connection.execute("UPDATE command_jobs SET started_at=? WHERE id=?",
                               (old, renewal["job_id"]))
        self.service.llm = Reviews(decision("wait", 600))
        self.service.review_command_job(renewal["job_id"])
        renewed = self.service.store.get_command_job(renewal["job_id"])
        self.assertEqual(renewed["max_runtime_seconds"], 7200)
        self.assertAlmostEqual(renewed["next_review_at"] - time.time(), 600, delta=2)
        older = (datetime.now(timezone.utc) - timedelta(seconds=7150)).isoformat()
        with self.service.store.connect() as connection, connection:
            connection.execute("UPDATE command_jobs SET started_at=? WHERE id=?",
                               (older, renewal["job_id"]))
        self.service.llm = Reviews(decision("background"))
        self.service.review_command_job(renewal["job_id"])
        self.assertEqual(
            self.service.store.get_command_job(renewal["job_id"])["max_runtime_seconds"],
            10800,
        )

    def test_review_failure_background_and_failed_renewal(self):
        item, _, _ = self.job()
        self.service.llm = Reviews(RuntimeError("review offline"))
        self.service.review_command_job(item["job_id"])
        failed = self.service.store.get_command_job(item["job_id"])
        self.assertTrue(failed["background"])
        self.assertIn("review offline", failed["review_error"])
        old = (datetime.now(timezone.utc) - timedelta(seconds=3550)).isoformat()
        with self.service.store.connect() as connection, connection:
            connection.execute("UPDATE command_jobs SET started_at=? WHERE id=?",
                               (old, item["job_id"]))
        self.service.llm = Reviews(RuntimeError("renewal offline"))
        self.service.review_command_job(item["job_id"])
        failed = self.service.store.get_command_job(item["job_id"])
        self.assertEqual(failed["max_runtime_seconds"], 3600)
        self.assertGreater(failed["next_review_at"], time.time())
        self.assertIn("Review failed", self.service.command_job_model_content(failed))
        self.service.store.set_command_job_outcome_unknown(
            item["job_id"], "last output before contact loss"
        )
        unknown = self.service.store.get_command_job(item["job_id"])
        self.assertIn("outcome unknown",
                      self.service.command_job_model_content(unknown))

    def test_runner_retry_keeps_job_id_and_worker_token_after_restart(self):
        runner_id = "r" * 32
        self.service.store.register_client(runner_id, "runner", "192.0.2.20")
        enrollment = self.service.store.create_runner_enrollment(runner_id)
        self.service.store.complete_runner_enrollment(
            enrollment["token"], "192.0.2.20", runner_id, 8766,
            "192.0.2.10", str(self.root),
        )
        self.service.store.record_runner_probe(
            runner_id, success=True, home=str(self.root),
            runner_version=brain.RUNNER_VERSION,
        )
        session = self.service.store.create(runner_id)
        command = {"program": "printf", "arguments": ["ok"],
                   "reason": "test", "trust_prefix": ["printf"]}
        call = {"id": "retry_call", "type": "function", "function": {
            "name": "run_command", "arguments": json.dumps(command)}}
        assistant = {"role": "assistant", "content": None, "tool_calls": [call],
                     "ui": {"command_message_id": secrets.token_urlsafe(24)}}
        approval = {"decision": "allowed_once", "prefix": []}
        first, token = self.service.ensure_runner_command_job(
            session, call, approval, assistant,
        )
        self.assertIsNotNone(token)
        with self.service.store.connect() as connection, connection:
            connection.execute(
                "UPDATE command_jobs SET reviewing=1 WHERE id=?", (first["job_id"],)
            )
        restarted = brain.BrainService(
            replace(config(self.root), command_worker_path=WORKER), "system"
        )
        self.assertFalse(restarted.store.get_command_job(first["job_id"])["reviewing"])
        second, retry_token = restarted.ensure_runner_command_job(
            session, call, approval, assistant,
        )
        self.assertEqual(second["job_id"], first["job_id"])
        self.assertEqual(retry_token, token)
        self.assertEqual(len(restarted.store.list_command_jobs(session["session_id"])), 1)
        restarted.store.set_command_job_unreachable(first["job_id"])
        third, retry_token = restarted.ensure_runner_command_job(
            session, call, approval, assistant,
        )
        self.assertEqual(third["job_id"], first["job_id"])
        self.assertEqual(retry_token, token)

        saved = restarted.store.get(session["session_id"])
        messages = [*saved["messages"], {"role": "user", "content": "Start build"},
                    assistant, {"role": "tool", "tool_call_id": call["id"],
                                "content": "provisional",
                                "ui": {"approval": approval,
                                       "command_job_id": first["job_id"]}}]
        restarted.store.save(session["session_id"], messages, "ready", [], 0)
        self.assertEqual(len(restarted.conversation_detail(
            restarted.store.get(session["session_id"]))["command_jobs"]), 1)
        nested = self.root / "nested"
        nested.mkdir()
        restarted.accept_command_job_update(first["job_id"], token, {
            "sequence": 1, "state": "running", "output": "still running",
            "total_bytes": 13, "truncated": False, "cwd": str(nested),
        })
        self.assertEqual(restarted.store.get(session["session_id"])["cwd"],
                         str(nested))
        restarted.store.set_session_runner(session["session_id"], None)
        self.assertEqual(restarted.store.get_command_job(first["job_id"])["runner_id"],
                         runner_id)
        restarted.accept_command_job_update(first["job_id"], token, {
            "sequence": 2, "state": "running", "output": "still running",
            "total_bytes": 13, "truncated": False, "cwd": first["cwd"],
        })
        self.assertIsNone(restarted.store.get(session["session_id"])["cwd"])
        restarted.store.set_command_job_unreachable(first["job_id"])

        class CachedResult(io.BytesIO):
            status = 200
        cached = {
            "job_id": first["job_id"], "status": "completed",
            "sequence": 3, "output": "finished while Brain was offline",
            "total_bytes": 32, "truncated": False, "exit_code": 0,
            "cwd": first["cwd"],
        }
        with patch.object(brain, "urlopen",
                          return_value=CachedResult(json.dumps(cached).encode())) as start:
            restarted.refresh_unreachable_runner_jobs(
                restarted.store.get(session["session_id"]), messages,
            )
        self.assertEqual(start.call_count, 1)
        self.assertEqual(restarted.store.get_command_job(first["job_id"])["state"],
                         "completed")
        self.assertIsNone(restarted.store.get(session["session_id"])["cwd"])
        public = restarted.public_messages(restarted.store.get(session["session_id"]))
        source_index = next(index for index, message in enumerate(public)
                            if message.get("content") == "Start build")
        restarted.store.create_branch(session["session_id"], source_index,
                                      "Different request")
        self.assertEqual(restarted.conversation_detail(
            restarted.store.get(session["session_id"]))["command_jobs"], [])

    def test_callback_token_reconnect_and_delete_guard(self):
        item, token, _ = self.job()
        server = brain.BrainHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        url = f"http://127.0.0.1:{server.server_port}/v1/command-jobs/{item['job_id']}/updates"
        snapshot = {"sequence": 1, "state": "running", "output": "partial",
                    "total_bytes": 7, "truncated": False, "cwd": item["cwd"]}
        def request(bearer):
            return Request(url, data=json.dumps(snapshot).encode(), method="POST",
                           headers={"Content-Type": "application/json",
                                    "Authorization": f"Bearer {bearer}"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request("wrong"))
        self.assertEqual(error.exception.code, 401)
        with urlopen(request(token)) as response:
            self.assertEqual(json.load(response)["max_runtime_seconds"], 3600)
        self.service.store.set_command_job_unreachable(item["job_id"])
        restarted = brain.BrainService(
            replace(config(self.root), command_worker_path=WORKER), "system"
        )
        server.service = restarted
        snapshot.update(sequence=2, output="more", total_bytes=11)
        with urlopen(request(token)):
            pass
        self.assertEqual(restarted.store.get_command_job(item["job_id"])["state"],
                         "running")
        with self.assertRaises(brain.BrainError):
            restarted.store.delete(self.session["session_id"])
        restarted.store.set_archived(self.session["session_id"], True)
        self.assertTrue(restarted.store.get(self.session["session_id"])["archived"])

    def test_worker_stop_partial_output_and_process_group(self):
        script = ("import subprocess,sys,time;"
                  "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
                  "print(child.pid,flush=True);time.sleep(60)")
        command = {"program": sys.executable, "arguments": ["-c", script],
                   "reason": "tree", "trust_prefix": [sys.executable]}
        item, token, _ = self.job(command=command)
        server = brain.BrainHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        state_dir = self.root / item["job_id"]
        state_dir.mkdir()
        request_file = state_dir / "request.json"
        request_file.write_text(json.dumps({
            "job_id": item["job_id"], "job_token": token,
            "brain_url": f"http://127.0.0.1:{server.server_port}",
            "cwd": item["cwd"], "command": command,
            "max_output_bytes": 65536, "max_runtime_seconds": 3600,
            "environment": {"PATH": os.defpath},
        }))
        worker = subprocess.Popen(
            [sys.executable, str(WORKER), "run", "--request", str(request_file),
             "--state-dir", str(state_dir)], stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: (worker.kill() if worker.poll() is None else None,
                                 worker.wait(timeout=5), worker.stderr.close()))
        running = self.wait(item["job_id"], lambda job: job["output"].strip().isdigit())
        child_pid = int(running["output"].strip())
        self.service.store.request_command_job_stop(item["job_id"])
        worker.wait(timeout=12)
        stopped = self.service.store.get_command_job(item["job_id"])
        self.assertEqual(stopped["state"], "stopped")
        self.assertEqual(stopped["output"].strip(), str(child_pid))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text().split(") ", 1)[1][0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            time.sleep(0.05)
        else:
            self.fail("child still running")

    def test_terminal_parent_exit_stops_worker(self):
        command = {"program": sys.executable,
                   "arguments": ["-c", "import time;print('started',flush=True);time.sleep(60)"],
                   "reason": "parent exit", "trust_prefix": [sys.executable]}
        item, token, _ = self.job(command=command)
        server = brain.BrainHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        state_dir = self.root / item["job_id"]
        state_dir.mkdir()
        parent = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
        self.addCleanup(lambda: (parent.kill() if parent.poll() is None else None,
                                 parent.wait(timeout=5)))
        request_file = state_dir / "request.json"
        request_file.write_text(json.dumps({
            "job_id": item["job_id"], "job_token": token,
            "brain_url": f"http://127.0.0.1:{server.server_port}",
            "cwd": item["cwd"], "command": command,
            "max_output_bytes": 65536, "max_runtime_seconds": 3600,
            "environment": {"PATH": os.defpath}, "parent_pid": parent.pid,
        }))
        worker = subprocess.Popen(
            [sys.executable, str(WORKER), "run", "--client-mode",
             "--request", str(request_file), "--state-dir", str(state_dir)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: (worker.kill() if worker.poll() is None else None,
                                 worker.wait(timeout=5), worker.stderr.close()))
        self.wait(item["job_id"], lambda job: job["output"] == "started\n")
        parent.terminate()
        parent.wait(timeout=5)
        worker.wait(timeout=12)
        result = self.service.store.get_command_job(item["job_id"])
        self.assertEqual(result["state"], "stopped")
        self.assertEqual(result["output"], "started\n")

    def test_output_head_tail_stays_inside_limit(self):
        spec = importlib.util.spec_from_file_location("worker", WORKER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = module.OutputBuffer(65536)
        output.add(b"head" + b"x" * 100000 + b"tail")
        text = output.text()
        self.assertTrue(text.startswith("head"))
        self.assertTrue(text.endswith("tail"))
        self.assertLessEqual(len(text.encode()), 65536)

    def test_job_completion_resets_stream_and_restarts_answer(self):
        item, token, call = self.job(attach=True)
        self.service.store.update_command_job(
            item["job_id"], sequence=1, state="running", output="partial",
            total_bytes=7, truncated=False, exit_code=None, cwd=item["cwd"],
        )
        self.service.store.apply_command_job_decision(
            item["job_id"], usual="yes", reason="long", action="background",
        )
        saved = self.service.store.get(self.session["session_id"])
        saved["messages"].append({
            "role": "tool", "tool_call_id": call["id"],
            "content": "provisional", "ui": {
                "approval": item["approval"], "command_job_id": item["job_id"]},
        })
        self.service.store.save(saved["session_id"], saved["messages"], "ready", [], 0)
        service = self.service
        class CompletingModel:
            def __init__(self):
                self.calls = 0
                self.contexts = []
            def complete(self, messages, emit, *, include_tools, cancellation):
                self.calls += 1
                self.contexts.append(messages)
                if self.calls == 1:
                    emit("content", {"delta": "stale"})
                    service.accept_command_job_update(item["job_id"], token, {
                        "sequence": 2, "state": "completed", "output": "final",
                        "total_bytes": 5, "truncated": False,
                        "exit_code": 0, "cwd": item["cwd"],
                    })
                    cancellation.raise_if_cancelled()
                emit("content", {"delta": "fresh"})
                return {"role": "assistant", "content": "fresh"}, []
        model = CompletingModel()
        service.llm = model
        events = []
        service.run_turn(saved["session_id"], {"type": "web_user", "content": "continue"},
                         lambda kind, _data: events.append(kind))
        final = service.store.get(saved["session_id"])
        self.assertEqual(model.calls, 2)
        self.assertIn("reset", events)
        self.assertEqual(final["messages"][-1]["content"], "fresh")
        self.assertTrue(any(
            message.get("role") == "tool" and "final" in message.get("content", "")
            for message in model.contexts[-1]
        ))
        self.assertEqual(
            next(message["content"] for message in final["messages"]
                 if message.get("tool_call_id") == call["id"]),
            "provisional",
        )


if __name__ == "__main__":
    unittest.main()
