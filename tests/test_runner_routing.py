from __future__ import annotations

import json
import threading
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from test_brain import brain, config
from test_file_tool import file_tool
from test_brain import FakeLLM


def call(name="run_command", ident="command", runner=None, **args):
    if name == "run_command":
        args = {"command": "printf ok", "reason": "Inspect host", **args}
    if runner is not None:
        args["runner_id"] = runner
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}


def response(calls):
    return ({"role": "assistant", "content": None, "tool_calls": calls}, calls)


class RunnerRoutingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.service = brain.BrainService(config(self.root), "Base")
        self.a, self.b = "a" * 32, "b" * 32
        self.homes = {}
        for ident, ip, name in [(self.a, "192.0.2.21", "ops@alpha"),
                                (self.b, "192.0.2.22", "ops@beta")]:
            home = self.root / name
            home.mkdir()
            self.homes[ident] = home
            self.service.store.register_client(ident, name, ip)
            token = self.service.store.create_runner_enrollment(ident)["token"]
            self.service.store.complete_runner_enrollment(token, ip, ident, 8766,
                                                         "127.0.0.1", str(home))
            self.service.store.record_runner_probe(ident, success=True,
                                                   runner_version=brain.RUNNER_VERSION)
        self.session = self.service.store.create(self.a)
        self.sid = self.session["session_id"]
        self.local = "l" * 32
        self.service.store.register_client(self.local, "ops@terminal", "127.0.0.1")

    def llm(self, *responses):
        self.service.llm = FakeLLM([*responses, ({"role": "assistant", "content": "Done"}, [])])

    def turn(self, kind="web_user", **fields):
        self.service.run_turn(self.sid, {"type": kind, "content": "Inspect runners", **fields}, lambda *_: None)
        return self.service.store.get(self.sid)

    def finish_job(self, session, call, approval, job, token):
        self.service.store.update_command_job(job["job_id"], sequence=1,
            state="completed", output=job["runner_id"], total_bytes=32,
            truncated=False, exit_code=0, cwd=job["cwd"])
        return {"tool_call_id": call["id"], "content": "exit_code=0\n" + job["runner_id"],
                "approval": approval, "command_job_id": job["job_id"]}

    def test_inventory_and_routing_arguments_without_credentials(self):
        text = json.dumps(self.service.model_messages(self.session["messages"], self.a))
        self.assertIn(self.a, text)
        self.assertIn("ops@beta", text)
        self.assertNotIn(self.service.store.get_runner(self.b)["token"], text)
        self.assertNotIn("Durable memory is scoped", text)
        modern = call(runner=self.b)
        brain.normalize_model_command_calls([modern])
        self.assertEqual(json.loads(modern["function"]["arguments"])["runner_id"], self.b)
        self.assertNotIn("runner_id", brain.parse_command_call(modern))
        modern["ui"] = {"target": {"runner_id": self.b}}
        upstream = brain.prepare_upstream_messages([{"role": "assistant", "content": None,
                                                      "tool_calls": [modern]}])[0]["tool_calls"][0]
        self.assertNotIn("ui", upstream)
        self.assertEqual(json.loads(upstream["function"]["arguments"])["runner_id"], self.b)
        for tool in [*brain.COMMAND_TOOLS, *brain.FILE_TOOLS, *brain.FILE_INSPECT_TOOLS, *brain.RUNNER_TOOLS]:
            self.assertIn("runner_id", tool["function"]["parameters"]["properties"])
        for bad in [None, "", 3, {}, []]:
            with self.assertRaises(brain.BrainError):
                brain.routing_arguments({"runner_id": bad})

    def test_parallel_trusted_commands_use_own_target_and_cwd(self):
        self.service.store.update_session_cwd(self.sid, "/srv/alpha")
        for ip in ["192.0.2.21", "192.0.2.22"]:
            self.service.store.change_server_trust(ip, "add", ["printf"])
        calls = [call(ident="default"), call(ident="beta", runner=self.b)]
        self.llm(response(calls))
        barrier = threading.Barrier(2)
        def execute(*args):
            barrier.wait(timeout=3)
            return self.finish_job(*args)
        with patch.object(self.service, "run_runner_command_job", side_effect=execute):
            saved = self.turn()
        jobs = self.service.store.list_command_jobs(self.sid)
        self.assertEqual({j["runner_id"]: j["cwd"] for j in jobs},
                         {self.a: "/srv/alpha", self.b: str(self.homes[self.b])})
        self.assertEqual(saved["runner_id"], self.a)
        self.assertEqual(saved["cwd"], "/srv/alpha")
        tools = [m for m in saved["messages"] if m["role"] == "tool"]
        self.assertEqual({m["ui"]["target"]["runner_id"] for m in tools}, {self.a, self.b})

    def test_target_trust_isolation_and_restart_with_changed_default(self):
        self.service.store.change_server_trust("192.0.2.21", "add", ["printf"])
        self.llm(response([call(runner=self.b)]))
        saved = self.turn()
        pending = saved["pending_tool_calls"][0]
        self.assertEqual(pending["ui"]["state"], "approval")
        self.assertEqual(pending["ui"]["target"]["server_ip"], "192.0.2.22")
        self.service.store.set_session_runner(self.sid, None)
        self.service = brain.BrainService(config(self.root), "Base")
        self.llm()
        with patch.object(self.service, "run_runner_command_job", side_effect=self.finish_job):
            self.service.resolve_remote_command(self.sid, pending["id"], "trust", lambda *_: None)
        self.assertEqual(self.service.store.list_command_jobs(self.sid)[0]["runner_id"], self.b)
        self.assertTrue(self.service.store.check_command("192.0.2.22", ["printf", "ok"])["allowed"])
        saved = self.service.store.get(self.sid)
        self.assertIsNone(saved["runner_id"])
        self.assertFalse(self.service.llm.seen_include_tools[-1])

    def test_failed_command_retries_same_job_and_target(self):
        self.llm(response([call(runner=self.b)]))
        self.turn()
        with patch.object(self.service, "run_runner_command_job", side_effect=brain.BrainError("Unavailable")):
            with self.assertRaises(brain.BrainError):
                self.service.resolve_remote_command(self.sid, "command", "allow_once", lambda *_: None)
        first = self.service.store.list_command_jobs(self.sid)[0]
        self.service.store.set_session_runner(self.sid, self.b)
        self.service = brain.BrainService(config(self.root), "Base")
        self.llm()
        with patch.object(self.service, "run_runner_command_job", side_effect=self.finish_job):
            self.service.resolve_remote_command(self.sid, "command", "retry", lambda *_: None)
        jobs = self.service.store.list_command_jobs(self.sid)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_id"], first["job_id"])
        self.assertEqual(jobs[0]["cwd"], str(self.homes[self.b]))

    def test_mixed_cli_results_leave_remote_approvals_and_local_continuation(self):
        self.service.store.bind_client(self.sid, self.local, "/local/project")
        calls = [call(ident="local"), call(ident="remote", runner=self.b)]
        self.llm(response(calls), response([call(ident="next-local")]))
        saved = self.turn("user", cwd="/local/project")
        local = next(c for c in saved["pending_tool_calls"] if c["id"] == "local")
        self.assertEqual(local["ui"]["target"]["executor"], "terminal")
        events = []
        self.service.run_turn(self.sid, {"type": "tool_results", "results": [{
            "tool_call_id": "local", "content": "exit_code=0\nlocal",
            "approval": {"decision": "allowed_once", "prefix": []}}]},
            lambda *e: events.append(e))
        self.assertEqual(events[-1][0], "tool_calls")
        self.assertEqual(len(self.service.llm.seen_messages), 1)
        self.service.resolve_remote_command(self.sid, "remote", "deny", lambda *_: None)
        saved = self.service.store.get(self.sid)
        self.assertEqual(saved["pending_tool_calls"][0]["id"], "next-local")
        self.assertEqual(saved["pending_tool_calls"][0]["ui"]["target"]["executor"], "terminal")
        self.assertEqual(saved["cwd"], "/local/project")

    def test_chat_only_and_unknown_ids_never_dispatch(self):
        chat = self.service.store.create()
        self.sid = chat["session_id"]
        self.llm(response([call(runner=self.b)]))
        with patch.object(self.service, "run_runner_command_job") as execute:
            saved = self.turn()
        execute.assert_not_called()
        self.assertFalse(self.service.llm.seen_include_tools[0])
        self.assertIn("Chat only", next(m["content"] for m in saved["messages"] if m["role"] == "tool"))
        self.sid = self.session["session_id"]
        self.llm(response([call(runner="unknown")]))
        with patch.object(self.service, "run_runner_command_job") as execute:
            saved = self.turn()
        execute.assert_not_called()
        self.assertIn("Unknown runner_id", next(m["content"] for m in saved["messages"] if m["role"] == "tool"))

    def test_file_tools_and_restore_use_original_runner(self):
        for home in self.homes.values():
            (home / "same.txt").write_text("before\n")
        calls = [call("find_files", "find", self.b, glob="*.txt"),
                 call("search_text", "search", self.b, pattern="before"),
                 call("read_file", "read", self.b, path="same.txt"),
                 call("edit_file", "edit", self.b, path="same.txt", operation="replace",
                      old_text="before", new_text="after", reason="Change beta")]
        self.llm(response(calls))
        seen = []
        def files(ident, payload):
            self.assertNotIn("runner_id", payload)
            seen.append(ident)
            return file_tool.perform(self.homes[ident] / "state", payload)
        with patch.object(self.service, "runner_file_request", side_effect=files):
            saved = self.turn()
            self.service.store.set_session_runner(self.sid, self.a)
            edit = next(m["ui"]["file_edit"] for m in saved["messages"] if m.get("ui", {}).get("file_edit"))
            self.service.file_edit_action(self.sid, "edit", {"action": "restore", "expected_hash": edit["after_hash"]})
        self.assertEqual(seen, [self.b] * 5)
        self.assertEqual((self.homes[self.a] / "same.txt").read_text(), "before\n")
        self.assertEqual((self.homes[self.b] / "same.txt").read_text(), "before\n")

    def test_targeted_runner_update_uses_explicit_runner(self):
        self.llm(response([call("update_runner", runner=self.b)]))
        with patch.object(self.service, "update_runner", return_value={"runner_version": brain.RUNNER_VERSION}) as update:
            self.turn()
        update.assert_called_once_with(self.b)

    def test_cli_file_default_stays_local_with_installed_runner(self):
        self.service.store.bind_client(self.sid, self.local, "/local/project")
        self.llm(response([call("read_file", path="local.txt")]))
        with patch.object(self.service, "runner_file_request") as files:
            saved = self.turn("user")
        files.assert_not_called()
        self.assertEqual(saved["pending_tool_calls"][0]["ui"]["target"]["executor"], "terminal")

    def test_incompatible_runner_returns_failure_without_switching(self):
        self.service.store.record_runner_probe(self.b, success=True, runner_version=0)
        self.service.store.change_server_trust("192.0.2.22", "add", ["printf"])
        self.llm(response([call(runner=self.b)]))
        with patch.object(self.service, "run_runner_command_job") as execute:
            saved = self.turn()
        execute.assert_not_called()
        pending = saved["pending_tool_calls"][0]
        self.assertEqual(pending["ui"]["target"]["runner_id"], self.b)
        self.assertEqual(pending["ui"]["state"], "failed")
        self.assertEqual(saved["runner_id"], self.a)

    def test_remote_new_instructions_cancel_before_user_message(self):
        self.service.store.bind_client(self.sid, self.local)
        self.llm(response([call(runner=self.b)]))
        self.turn("user")
        self.service.run_turn(self.sid, {"type": "tool_results", "results": [],
                                       "instruction": "Different task"}, lambda *_: None)
        saved = self.service.store.get(self.sid)
        instruction_index = next(i for i, m in enumerate(saved["messages"]) if m.get("content") == "Different task")
        self.assertEqual(saved["messages"][instruction_index - 1]["role"], "tool")
        self.assertEqual(saved["messages"][instruction_index - 1]["ui"]["approval"]["decision"], "cancelled")
        self.assertEqual(saved["pending_tool_calls"], [])

    def test_cli_decision_endpoint_checks_owner_and_source_ip(self):
        self.service.store.bind_client(self.sid, self.local, "/local/project")
        self.llm(response([call(runner=self.b)]))
        self.turn("user")
        server = brain.BrainHTTPServer(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/v1/sessions/{self.sid}/commands/command"
        for client_id in [self.a, self.b]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(url, data=json.dumps({"client_id": client_id, "decision": "deny"}).encode(),
                                headers={"Content-Type": "application/json"}))
            self.assertEqual(error.exception.code, 403)
        self.service.store.register_client(self.local, "ops@terminal", "192.0.2.99")
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(url, data=json.dumps({"client_id": self.local, "decision": "deny"}).encode(),
                            headers={"Content-Type": "application/json"}))
        self.assertEqual(error.exception.code, 403)
        self.service.store.register_client(self.local, "ops@terminal", "127.0.0.1")
        with urlopen(Request(url, data=json.dumps({"client_id": self.local, "decision": "deny"}).encode(),
                             headers={"Content-Type": "application/json"})) as result:
            self.assertIn(b"event: done", result.read())
        self.assertEqual(self.service.store.get(self.sid)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
