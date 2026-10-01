from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_brain import brain, config, FakeHTTPResponse, FakeLLM

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("file_tool", ROOT / "client" / "file_tool.py")
assert SPEC and SPEC.loader
file_tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = file_tool
SPEC.loader.exec_module(file_tool)


def request(identifier, cwd, path, operation, old, new):
    return {"action": "apply", "request_id": identifier, "cwd": str(cwd),
            "path": path, "operation": operation, "old_text": old,
            "new_text": new, "reason": "test"}


class FileToolTests(unittest.TestCase):
    def test_create_edit_delete_and_restore_with_diff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            path = root / "file with spaces.py"
            created = file_tool.perform(state, request("a" * 32, root, path.name, "create", "", "one\ntwo\n"))
            self.assertEqual(path.read_text(), "one\ntwo\n")
            self.assertIn("+one", created["edit"]["diff"])
            self.assertEqual(file_tool.perform(state, {"action": "read", "edit_id": "a" * 32})["content"], "one\ntwo\n")
            changed = file_tool.perform(state, {"action": "save", "edit_id": "a" * 32,
                "request_id": "b" * 32, "expected_hash": created["edit"]["after_hash"],
                "content": "one\nthree\n"})
            self.assertTrue(changed["edit"]["manually_edited"])
            self.assertIn("+three", changed["edit"]["diff"])
            self.assertEqual(path.read_text(), "one\nthree\n")
            self.assertEqual(file_tool.perform(state, {"action": "save", "edit_id": "a" * 32,
                "request_id": "b" * 32, "expected_hash": created["edit"]["after_hash"],
                "content": "one\nthree\n"})["edit"], changed["edit"])
            restored = file_tool.perform(state, {"action": "restore", "edit_id": "a" * 32,
                "request_id": "c" * 32, "expected_hash": changed["edit"]["after_hash"]})
            self.assertFalse(path.exists())
            self.assertEqual(restored["edit"]["status"], "restored")
            self.assertIsNone(restored["edit"]["after_hash"])

            path.write_text("before\n", encoding="utf-8")
            path.chmod(0o640)
            edited = file_tool.perform(state, request("d" * 32, root, path.name, "replace", "before", "after"))
            self.assertEqual(path.read_text(), "after\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertIn("-before", edited["edit"]["diff"])
            self.assertIn("+after", edited["edit"]["diff"])
            file_tool.perform(state, {"action": "restore", "edit_id": "d" * 32,
                "request_id": "e" * 32, "expected_hash": edited["edit"]["after_hash"]})
            self.assertEqual(path.read_text(), "before\n")
            deleted = file_tool.perform(state, request("f" * 32, root, path.name, "delete", "before\n", ""))
            self.assertFalse(path.exists())
            self.assertIn("-before", deleted["edit"]["diff"])
            file_tool.perform(state, {"action": "restore", "edit_id": "f" * 32,
                "request_id": "g" * 32, "expected_hash": None})
            self.assertEqual(path.read_text(), "before\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_compare_reports_net_change_and_stale_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            path = root / "app.py"
            path.write_text("value = 1\n", encoding="utf-8")
            first = file_tool.perform(state, request("a" * 32, root, path.name,
                "replace", "value = 1", "value = 2"))
            second = file_tool.perform(state, request("b" * 32, root, path.name,
                "replace", "value = 2", "value = 3"))
            total = file_tool.perform(state, {"action": "compare", "edit_id": "a" * 32,
                "expected_hash": second["edit"]["after_hash"]})
            self.assertFalse(total["stale"])
            self.assertEqual((total["added"], total["removed"]), (1, 1))
            self.assertIn("-value = 1", total["diff"])
            self.assertIn("+value = 3", total["diff"])
            self.assertNotIn("value = 2", total["diff"])
            third = file_tool.perform(state, request("c" * 32, root, path.name,
                "replace", "value = 3", "value = 1"))
            unchanged = file_tool.perform(state, {"action": "compare", "edit_id": "a" * 32,
                "expected_hash": third["edit"]["after_hash"]})
            self.assertEqual((unchanged["diff"], unchanged["added"], unchanged["removed"]),
                ("", 0, 0))
            path.write_text("external\n", encoding="utf-8")
            stale = file_tool.perform(state, {"action": "compare", "edit_id": "a" * 32,
                "expected_hash": third["edit"]["after_hash"]})
            self.assertTrue(stale["stale"])
            self.assertNotIn("diff", stale)

    def test_conflicts_and_non_text_are_left_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            path = root / "main.txt"
            path.write_text("repeat repeat\n", encoding="utf-8")
            with self.assertRaisesRegex(file_tool.FileError, "exactly once"):
                file_tool.perform(state, request("h" * 32, root, path.name, "replace", "repeat", "new"))
            self.assertEqual(path.read_text(), "repeat repeat\n")
            edited = file_tool.perform(state, request("i" * 32, root, path.name, "replace", "repeat repeat", "new"))
            path.write_text("external\n", encoding="utf-8")
            with self.assertRaisesRegex(file_tool.FileError, "changed since review"):
                file_tool.perform(state, {"action": "restore", "edit_id": "i" * 32,
                    "request_id": "j" * 32, "expected_hash": edited["edit"]["after_hash"]})
            self.assertEqual(path.read_text(), "external\n")
            link = root / "link.txt"
            link.symlink_to(path)
            with self.assertRaises(file_tool.FileError):
                file_tool.perform(state, request("k" * 32, root, link.name, "replace", "external", "new"))
            binary = root / "binary.dat"
            binary.write_bytes(b"x\0y")
            with self.assertRaisesRegex(file_tool.FileError, "UTF-8"):
                file_tool.perform(state, request("l" * 32, root, binary.name, "replace", "x", "z"))


class FileInspectTests(unittest.TestCase):
    def test_find_search_and_read_without_edit_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / ".git").mkdir()
            (root / "src" / "app.py").write_text("alpha\nBeta value\nlast\n", encoding="utf-8")
            (root / "src" / "other.py").write_text("none\n", encoding="utf-8")
            (root / ".git" / "secret.py").write_text("Beta value\n", encoding="utf-8")
            (root / "link.py").symlink_to(root / "src" / "app.py")
            state = root / "state"
            found = file_tool.perform(state, {"action": "find_files", "cwd": str(root), "glob": "*.py"})
            self.assertEqual(found["matches"], [str(root / "src" / "app.py"),
                                                 str(root / "src" / "other.py")])
            matches = file_tool.perform(state, {"action": "search_text", "cwd": str(root),
                "pattern": r"beta\s+value", "file_glob": "*.py", "case_sensitive": False})
            self.assertEqual(matches["matches"], [{"path": str(root / "src" / "app.py"),
                "line": 2, "text": "Beta value"}])
            read = file_tool.perform(state, {"action": "read_file", "cwd": str(root),
                "path": "src/app.py", "start_line": 2, "max_lines": 1})
            self.assertEqual((read["content"], read["end_line"], read["total_lines"], read["truncated"]),
                             ("Beta value\n", 2, 3, True))
            self.assertFalse(state.exists())

    def test_bounds_and_binary_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "big.txt").write_text("match\n" * 210, encoding="utf-8")
            (root / "binary.txt").write_bytes(b"bad\0text")
            state = root / "state"
            matches = file_tool.perform(state, {"action": "search_text", "cwd": str(root),
                "pattern": "match", "max_results": 2})
            self.assertEqual(len(matches["matches"]), 2)
            self.assertTrue(matches["truncated"])
            with self.assertRaisesRegex(file_tool.FileError, "UTF-8|binary"):
                file_tool.perform(state, {"action": "read_file", "cwd": str(root), "path": "binary.txt"})
            with self.assertRaises(file_tool.FileError):
                file_tool.perform(state, {"action": "read_file", "cwd": str(root), "path": "missing.txt"})


class BrainFileInspectTests(unittest.TestCase):
    def test_model_sees_dedicated_file_tools_before_command(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            lines = ['data: {"choices":[{"delta":{"content":"Ready"}}]}\n',
                     'data: [DONE]\n']
            with patch.object(brain, "urlopen", return_value=FakeHTTPResponse(lines)) as upstream:
                client.complete([{"role": "user", "content": "inspect"}], lambda *_: None,
                    include_memory_tools=False, include_web_tools=False)
            payload = json.loads(upstream.call_args.args[0].data)
            names = [tool["function"]["name"] for tool in payload["tools"]]
            self.assertEqual(names[:3], ["find_files", "search_text", "read_file"])
            self.assertIn("update_runner", names)
            self.assertEqual(names[-1], "run_command")

    def test_web_turn_runs_read_tools_on_selected_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(enrollment["token"], "192.0.2.20",
                runner_id, 8766, "192.0.2.10", str(root))
            service.store.record_runner_probe(runner_id, success=True, runner_version=brain.RUNNER_VERSION)
            session = service.store.create(runner_id)
            (root / "app.py").write_text("hello\n", encoding="utf-8")
            names = ("find_files", "search_text", "read_file")
            args = ({"glob": "*.py"}, {"pattern": "hello"}, {"path": "app.py"})
            calls = [{"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments)}}
                for name, arguments in zip(names, args)]
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": calls}, calls),
                ({"role": "assistant", "content": "Found it."}, []),
            ])
            with patch.object(service, "runner_file_request",
                    side_effect=lambda _runner, payload: file_tool.perform(root / "state", payload)) as runner:
                service.run_turn(session["session_id"],
                    {"type": "web_user", "content": "Inspect files"}, lambda *_: None)
            self.assertEqual(runner.call_count, 3)
            saved = service.store.get(session["session_id"])
            self.assertEqual(saved["status"], "ready")
            self.assertEqual(saved["pending_tool_calls"], [])
            results = [m for m in saved["messages"] if m.get("role") == "tool"]
            self.assertEqual(len(results), 3)
            self.assertTrue(all(m["ui"]["approval"]["decision"] == "automatic" for m in results))
            self.assertEqual(json.loads(results[2]["content"])["content"], "hello\n")

    def test_v4_runner_remains_usable_but_inspection_needs_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(enrollment["token"], "192.0.2.20",
                runner_id, 8766, "192.0.2.10", str(root))
            service.store.record_runner_probe(runner_id, success=True, runner_version=4)
            self.assertNotEqual(service.public_runner(runner_id)["status"], "upgrade_required")
            with self.assertRaisesRegex(brain.FileToolError, "Update selected runner"):
                service.runner_file_request(runner_id,
                    {"action": "read_file", "cwd": str(root), "path": "app.py"})

    def test_terminal_inspection_result_accepts_automatic_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            session = service.store.create()
            call = {"id": "inspect", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "app.py"})}}
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                ({"role": "assistant", "content": "Done."}, []),
            ])
            service.run_turn(session["session_id"], {"type": "user", "content": "Read app"}, lambda *_: None)
            self.assertEqual(service.store.get(session["session_id"])["status"], "awaiting_tool_results")
            service.run_turn(session["session_id"], {"type": "tool_results", "results": [{
                "tool_call_id": "inspect", "content": '{"ok":true,"content":"hello"}',
                "approval": {"decision": "automatic", "prefix": []},
            }]}, lambda *_: None)
            self.assertEqual(service.store.get(session["session_id"])["status"], "ready")



class BrainFileEditTests(unittest.TestCase):
    def test_web_turn_keeps_diff_and_allows_edit_then_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(enrollment["token"], "192.0.2.20",
                runner_id, 8766, "192.0.2.10", str(root))
            service.store.record_runner_probe(runner_id, success=True, runner_version=brain.RUNNER_VERSION)
            session = service.store.create(runner_id)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            call = {"id": "edit_call", "type": "function", "function": {
                "name": "edit_file", "arguments": json.dumps({"path": "app.py", "operation": "replace",
                    "old_text": "value = 1", "new_text": "value = 2", "reason": "update value"})}}
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                ({"role": "assistant", "content": "Changed value."}, []),
            ])
            def runner_file_request(_runner_id, payload):
                try:
                    return file_tool.perform(root / "state", payload)
                except file_tool.FileError as error:
                    raise brain.FileToolError(str(error), error.status) from error

            with patch.object(service, "runner_file_request", side_effect=runner_file_request):
                service.run_turn(session["session_id"], {"type": "web_user", "content": "Edit app"}, lambda *_: None)
                self.assertEqual(target.read_text(), "value = 2\n")
                saved = service.store.get(session["session_id"])
                result = next(m for m in saved["messages"] if m.get("tool_call_id") == call["id"])
                self.assertIn("+value = 2", result["ui"]["file_edit"]["diff"])
                self.assertNotIn("ui", service.llm.seen_messages[-1][-1])
                opened = service.file_edit_action(session["session_id"], call["id"], {"action": "read"})
                self.assertEqual(opened["content"], "value = 2\n")
                changed = service.file_edit_action(session["session_id"], call["id"], {
                    "action": "save", "expected_hash": opened["hash"], "content": "value = 3\n"})
                self.assertEqual(target.read_text(), "value = 3\n")
                restored = service.file_edit_action(session["session_id"], call["id"], {
                    "action": "restore", "expected_hash": changed["edit"]["after_hash"]})
                self.assertEqual(target.read_text(), "value = 1\n")
                self.assertEqual(restored["edit"]["status"], "restored")
                target.write_text("external\n", encoding="utf-8")
                with self.assertRaisesRegex(brain.FileToolError, "changed since review"):
                    service.file_edit_action(session["session_id"], call["id"], {
                        "action": "save", "expected_hash": restored["edit"]["after_hash"],
                        "content": "value = 4\n"})

    def test_total_diff_checks_branch_order_runner_and_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(enrollment["token"], "192.0.2.20",
                runner_id, 8766, "192.0.2.10", str(root))
            service.store.record_runner_probe(runner_id, success=True, runner_version=6)
            session = service.store.create(runner_id)
            target = root / "app.py"
            target.write_text("value = 1\n", encoding="utf-8")
            messages = session["messages"] + [{"role": "user", "content": "Edit app twice"}]
            for number, call_id in ((2, "first"), (3, "latest")):
                call = {"id": call_id, "type": "function", "function": {
                    "name": "edit_file", "arguments": json.dumps({"path": str(target),
                        "operation": "replace", "old_text": f"value = {number - 1}",
                        "new_text": f"value = {number}", "reason": "test"})}}
                result = file_tool.perform(root / "state", request(
                    brain.file_request_id(session["session_id"], call_id), root,
                    target.name, "replace", f"value = {number - 1}", f"value = {number}"))
                edit = {**result["edit"], "runner_id": runner_id}
                messages.extend([
                    {"role": "assistant", "content": None, "tool_calls": [call]},
                    {"role": "tool", "tool_call_id": call_id,
                     "content": brain.file_edit_summary(edit), "ui": {"file_edit": edit}},
                ])
            messages.append({"role": "assistant", "content": "Done."})
            service.store.save(session["session_id"], messages, "ready", [], 0)
            first_edit = next(item["ui"]["file_edit"] for item in messages
                              if item.get("tool_call_id") == "first")
            latest_edit = next(item["ui"]["file_edit"] for item in messages
                               if item.get("tool_call_id") == "latest")
            with self.assertRaisesRegex(brain.FileToolError, "Update selected runner"):
                service.runner_file_request(runner_id, {"action": "compare",
                    "edit_id": first_edit["id"], "expected_hash": latest_edit["after_hash"]})
            service.store.record_runner_probe(runner_id, success=True,
                                              runner_version=brain.RUNNER_VERSION)
            def runner_file_request(_runner_id, payload):
                try:
                    return file_tool.perform(root / "state", payload)
                except file_tool.FileError as error:
                    raise brain.FileToolError(str(error), error.status) from error
            with patch.object(service, "runner_file_request", side_effect=runner_file_request):
                total = service.file_edit_total(session["session_id"], "first", "latest")
                self.assertIn("+value = 3", total["diff"])
                self.assertNotIn("value = 2", total["diff"])
                with self.assertRaisesRegex(brain.FileToolError, "history not found"):
                    service.file_edit_total(session["session_id"], "latest", "first")
                with self.assertRaisesRegex(brain.FileToolError, "history not found"):
                    service.file_edit_total(session["session_id"], "first", "missing")
                other_session = service.store.create(runner_id)
                with self.assertRaisesRegex(brain.FileToolError, "history not found"):
                    service.file_edit_total(other_session["session_id"], "first", "latest")
                latest_edit["runner_id"] = "s" * 32
                service.store.save(session["session_id"], messages, "ready", [], 0)
                with self.assertRaisesRegex(brain.FileToolError, "different runners"):
                    service.file_edit_total(session["session_id"], "first", "latest")

    def test_terminal_file_result_is_saved_for_web_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            client_id = "c" * 32
            service.store.register_client(client_id, "user@terminal", "192.0.2.20")
            session = service.store.create()
            service.store.bind_client(session["session_id"], client_id, str(root))
            call = {"id": "terminal_edit", "type": "function", "function": {
                "name": "edit_file", "arguments": json.dumps({"path": "local.py", "operation": "create",
                    "old_text": "", "new_text": "print(1)\n", "reason": "create"})}}
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                ({"role": "assistant", "content": "Done."}, []),
            ])
            service.run_turn(session["session_id"], {"type": "user", "content": "Create local.py"}, lambda *_: None)
            self.assertEqual(service.store.get(session["session_id"])["status"], "awaiting_tool_results")
            edit_id = brain.file_request_id(session["session_id"], call["id"])
            local = file_tool.perform(root / "state", request(edit_id, root, "local.py", "create", "", "print(1)\n"))
            with self.assertRaisesRegex(brain.BrainError, "file edit result and approval"):
                service.run_turn(session["session_id"], {"type": "tool_results", "results": [{
                    "tool_call_id": call["id"], "content": "edited",
                    "approval": {"decision": "automatic", "prefix": []},
                }]}, lambda *_: None)
            service.run_turn(session["session_id"], {"type": "tool_results", "results": [{
                "tool_call_id": call["id"], "content": "edited", "approval": {"decision": "automatic", "prefix": []},
                "file_edit": local["edit"],
            }]}, lambda *_: None)
            result = next(m for m in service.store.get(session["session_id"])["messages"]
                          if m.get("tool_call_id") == call["id"])
            self.assertEqual(result["ui"]["file_edit"]["runner_id"], client_id)
            self.assertIn("+print(1)", result["ui"]["file_edit"]["diff"])
            self.assertEqual((root / "local.py").read_text(), "print(1)\n")


if __name__ == "__main__":
    unittest.main()
