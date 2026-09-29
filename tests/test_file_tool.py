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
from test_brain import brain, config, FakeLLM

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
