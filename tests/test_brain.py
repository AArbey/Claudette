from __future__ import annotations

import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("brain", ROOT / "server" / "brain.py")
assert SPEC and SPEC.loader
brain = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = brain
SPEC.loader.exec_module(brain)


def config(
    root: Path, *, max_tool_rounds: int = 8, support_model_name: str = ""
):
    return brain.Config(
        llm_endpoint_url="http://127.0.0.1:1/v1/chat/completions",
        llm_api_key="",
        model_name="test-model",
        brain_url="http://brain.test:8080",
        bind_host="127.0.0.1",
        port=8080,
        web_port=8081,
        database_path=root / "brain.sqlite3",
        system_prompt_path=root / "prompt.txt",
        knowledge_dir=root / "knowledge",
        client_script_path=root / "client.sh",
        web_dir=root / "web",
        max_tool_rounds=max_tool_rounds,
        max_knowledge_bytes=65536,
        max_request_bytes=1024 * 1024,
        llm_timeout_seconds=5,
        client_command_timeout_seconds=30,
        client_max_tool_output_bytes=65536,
        client_brain_connect_timeout_seconds=10,
        client_brain_request_timeout_seconds=30,
        support_model_name=support_model_name,
        runner_script_path=ROOT / "client" / "runner.sh",
        runner_installer_path=ROOT / "client" / "install-runner.sh",
    )


def tool_call(call_id: str = "call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "run_command",
            "arguments": json.dumps(
                {
                    "program": "printf",
                    "arguments": ["ok"],
                    "reason": "test",
                    "trust_prefix": ["printf"],
                }
            ),
        },
    }


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.seen_messages = []
        self.seen_include_tools = []

    def complete(
        self, messages, emit, *, include_tools=True, model_name=None
    ):
        self.seen_messages.append(json.loads(json.dumps(messages)))
        self.seen_include_tools.append(include_tools)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        assistant, calls = response
        if assistant.get("content"):
            emit("content", {"delta": assistant["content"]})
        return assistant, calls


class BlockingLLM:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, messages, emit, *, include_tools=True):
        emit("reasoning", {"delta": "checking"})
        emit("content", {"delta": "answer"})
        self.started.set()
        if not self.release.wait(timeout=2):
            raise brain.BrainError("test release timed out")
        return {"role": "assistant", "content": "answer"}, []


class PromptTests(unittest.TestCase):
    def test_estimates_context_tokens(self):
        self.assertEqual(
            brain.estimate_message_tokens([{"role": "user", "content": "x" * 40}]),
            13,
        )

    def test_web_turn_keeps_attachment_references(self):
        references = [{"type": "attachment", "id": "a" * 32, "label": "notes.txt"}]
        result = brain.web_turn_body({"content": "Read it", "references": references})
        self.assertEqual(result["references"], references)

    def test_rejects_oversized_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            knowledge = root / "knowledge"
            knowledge.mkdir()
            prompt.write_text("Base", encoding="utf-8")
            (knowledge / "large.md").write_text("12345", encoding="utf-8")
            with self.assertRaisesRegex(brain.BrainError, "MAX_KNOWLEDGE_BYTES"):
                brain.load_system_prompt(prompt, knowledge, 4)


class ClientScriptTests(unittest.TestCase):
    def test_runner_version_matches_brain(self):
        source = (ROOT / "client" / "runner.sh").read_text(encoding="utf-8")
        self.assertIn(f"readonly RUNNER_VERSION={brain.RUNNER_VERSION}\n", source)

    def test_renders_configuration_and_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("client.sh").write_text(
                "#!/usr/bin/env bash\nprintf 'client started\\n'\n", encoding="utf-8"
            )

            rendered = brain.render_client_script(config(root)).decode()

            self.assertTrue(rendered.startswith("#!/usr/bin/env bash\n"))
            self.assertTrue(rendered.endswith('\nmain "$@"\n'))
            self.assertIn("BRAIN_URL=http://brain.test:8080\n", rendered)
            self.assertIn("COMMAND_TIMEOUT_SECONDS=30\n", rendered)
            self.assertIn("BRAIN_REQUEST_TIMEOUT_SECONDS=30\n", rendered)
            self.assertIn("printf 'client started\\n'", rendered)

class ToolDeltaTests(unittest.TestCase):
    def test_argv_allows_empty_arguments_but_not_empty_program(self):
        brain.validate_argv(["printf", ""])
        with self.assertRaises(brain.BrainError):
            brain.validate_argv(["", "value"])

    def test_merges_fragmented_parallel_calls(self):
        current = []
        brain.merge_tool_call_deltas(
            current,
            [
                {
                    "index": 0,
                    "id": "one",
                    "type": "function",
                    "function": {"name": "run_", "arguments": "{\"a\":"},
                },
                {
                    "index": 1,
                    "id": "two",
                    "type": "function",
                    "function": {"name": "run_command", "arguments": "{}"},
                },
            ],
        )
        brain.merge_tool_call_deltas(
            current,
            [{"index": 0, "function": {"name": "command", "arguments": "1}"}}],
        )
        self.assertEqual(current[0]["function"]["name"], "run_command")
        self.assertEqual(current[0]["function"]["arguments"], '{"a":1}')
        self.assertEqual(current[1]["id"], "two")

    def test_rejects_invalid_index_and_duplicate_ids(self):
        with self.assertRaises(brain.BrainError):
            brain.merge_tool_call_deltas([], [{"index": 64, "function": {}}])
        with self.assertRaisesRegex(brain.BrainError, "duplicate tool call IDs"):
            brain.validate_tool_calls([tool_call(), tool_call()])
        with self.assertRaisesRegex(brain.BrainError, "prefix must match"):
            brain.validate_approval({"decision": "trusted", "prefix": ["echo"]}, tool_call())
        with self.assertRaisesRegex(brain.BrainError, "only trusted"):
            brain.validate_approval({"decision": "allowed_once", "prefix": ["printf"]}, tool_call())


class FakeHTTPResponse:
    status = 200

    def __init__(self, lines):
        self.lines = [line.encode() for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter(self.lines)

    def read(self, _limit=-1):
        return b"".join(self.lines)


class LLMStreamTests(unittest.TestCase):
    def test_discovers_models_from_base_url(self):
        response = FakeHTTPResponse([
            '{"data":[{"id":"model-b"},{"id":"model-a"},{"id":"model-b"}]}'
        ])
        with patch.object(brain, "urlopen", return_value=response) as upstream:
            models = brain.discover_llm_models(
                "http://llm.test:8080/v1", "secret", 30
            )
        self.assertEqual(models, ["model-b", "model-a"])
        request = upstream.call_args.args[0]
        self.assertEqual(request.full_url, "http://llm.test:8080/v1/models")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    def test_detects_and_caches_runtime_context_window(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            responses = [
                FakeHTTPResponse([
                    '{"running":[{"model":"test-model","state":"ready"}]}'
                ]),
                FakeHTTPResponse([
                    '{"default_generation_settings":{"n_ctx":200192}}'
                ]),
            ]
            with patch.object(brain, "urlopen", side_effect=responses) as upstream:
                first = client.context_window()
                second = client.context_window()
            self.assertEqual(first, {
                "tokens": 200192, "source": "runtime", "stale": False,
            })
            self.assertEqual(second, first)
            self.assertEqual(upstream.call_count, 2)
            request = upstream.call_args_list[1].args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:1/props?model=test-model")

    def test_uses_model_listing_when_runtime_props_has_no_context(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            responses = [
                FakeHTTPResponse(["{\"running\":[]}"]),
                FakeHTTPResponse([
                    '{"data":[{"id":"test-model","context_length":131072}]}'
                ]),
            ]
            with patch.object(brain, "urlopen", side_effect=responses) as upstream:
                result = client.context_window()
            self.assertEqual(result, {
                "tokens": 131072, "source": "models", "stale": False,
            })
            self.assertEqual(
                upstream.call_args_list[1].args[0].full_url,
                "http://127.0.0.1:1/v1/models",
            )

    def test_keeps_configured_context_when_metadata_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            with patch.object(brain, "urlopen", side_effect=brain.URLError("offline")):
                result = client.context_window()
            self.assertEqual(result, {
                "tokens": 32768, "source": "configured", "stale": True,
            })

    def test_folds_target_notices_into_leading_system_message(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            lines = [
                'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                "data: [DONE]\n",
            ]
            messages = [
                {"role": "system", "content": "Base instructions"},
                {"role": "user", "content": "first"},
                {
                    "role": "system",
                    "content": (
                        "The commands you run will run on the host host "
                        "at IP 192.0.2.20."
                    ),
                    "ui": {"notice": True},
                },
                {"role": "user", "content": "second"},
            ]
            with patch.object(
                brain, "urlopen", return_value=FakeHTTPResponse(lines)
            ) as upstream:
                client.complete(messages, lambda *_: None)
            payload = json.loads(upstream.call_args.args[0].data)
            self.assertEqual(
                [message["role"] for message in payload["messages"]],
                ["system", "user", "user"],
            )
            self.assertEqual(
                payload["messages"][0]["content"],
                "Base instructions\n\nThe commands you run will run on the host "
                "host at IP 192.0.2.20.",
            )
            self.assertTrue(
                all("ui" not in message for message in payload["messages"])
            )

    def test_parses_content_and_fragmented_tool_call(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            lines = [
                'data: {"choices":[{"delta":{"content":"Hi "}}]}\n',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_","arguments":"{\\"program\\":\\"printf\\","}}]}}]}\n',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"command","arguments":"\\"arguments\\":[],\\"reason\\":\\"test\\",\\"trust_prefix\\":[\\"printf\\"]}"}}]}}]}\n',
                "data: [DONE]\n",
            ]
            events = []
            with patch.object(brain, "urlopen", return_value=FakeHTTPResponse(lines)) as upstream:
                assistant, calls = client.complete(
                    [
                        {"role": "user", "content": "test"},
                        {"role": "assistant", "content": "previous", "ui": {"reasoning": "private display"}},
                        {"role": "tool", "content": "ok", "tool_call_id": "old", "ui": {
                            "approval": {"decision": "trusted", "prefix": ["printf"]},
                        }},
                    ],
                    lambda name, data: events.append((name, data)),
                )
            payload = json.loads(upstream.call_args.args[0].data)
            self.assertTrue(all("ui" not in message for message in payload["messages"]))
            self.assertEqual(assistant["content"], "Hi ")
            self.assertEqual(calls[0]["function"]["name"], "run_command")
            self.assertEqual(events[0], ("content", {"delta": "Hi "}))

    def test_rejects_invalid_streams(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            cases = [
                ('data: {"choices":[{"delta":{"content":"partial"}}]}\n', "incomplete SSE"),
                ('data: {"choices":[null]}\n', "invalid choice"),
                ('data: {"choices":[{"delta":null}]}\n', "invalid delta"),
            ]
            for line, error in cases:
                with self.subTest(error=error), patch.object(
                    brain, "urlopen", return_value=FakeHTTPResponse([line])
                ), self.assertRaisesRegex(brain.BrainError, error):
                    client.complete(
                        [{"role": "user", "content": "test"}], lambda *_: None
                    )

    def test_empty_completion_after_tool_is_normal_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            lines = [
                'data: {"choices":[{"delta":{"reasoning_content":"done"}}]}\n',
                "data: [DONE]\n",
            ]
            messages = [
                {"role": "user", "content": "check"},
                {"role": "assistant", "content": None, "tool_calls": [tool_call()]},
                {"role": "tool", "tool_call_id": "call_1", "content": "exit_code=0"},
            ]
            with patch.object(
                brain, "urlopen", return_value=FakeHTTPResponse(lines)
            ):
                assistant, calls = client.complete(messages, lambda *_: None)
            self.assertIsNone(assistant["content"])
            self.assertEqual(calls, [])

            with patch.object(
                brain, "urlopen", return_value=FakeHTTPResponse(lines)
            ), self.assertRaisesRegex(brain.BrainError, "neither content"):
                client.complete(
                    [{"role": "user", "content": "check"}], lambda *_: None
                )


class StoreAndServiceTests(unittest.TestCase):
    def test_ai_servers_are_persisted_and_global_selection_changes_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(
                brain.replace(
                    config(root), llm_endpoint_url="", llm_api_key="", model_name=""
                ),
                "system",
            )
            self.assertEqual(service.store.list_ai_servers(), [])
            with self.assertRaisesRegex(brain.BrainError, "No AI model configured"):
                service.llm.current_client()

            first = service.store.save_ai_server(
                None, "Local", "http://one.test/v1/chat/completions",
                "top-secret", ["one", "two"],
            )
            second = service.store.save_ai_server(
                None, "Remote", "https://two.test/v1/chat/completions",
                "", ["three"],
            )
            public = service.store.list_ai_servers()
            self.assertNotIn("api_key", public[0])
            self.assertTrue(public[0]["has_api_key"])
            self.assertEqual(service.llm.current_client().config.model_name, "one")

            service.store.select_ai_model(second["server_id"], "three")
            selected = service.llm.current_client().config
            self.assertEqual(selected.model_name, "three")
            self.assertEqual(selected.llm_endpoint_url, second["endpoint_url"])
            self.assertNotEqual(first["server_id"], second["server_id"])

    def test_schema_8_adds_ai_servers(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO app_metadata VALUES ('schema_version', '8')"
            )
            brain.SessionStore.migrate_schema(connection)
            self.assertIsNotNone(connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_servers'"
            ).fetchone())
            self.assertEqual(brain.SessionStore.get_schema_version(connection), 9)

    def test_environment_ignores_legacy_ai_configuration(self):
        with patch.dict(os.environ, {
            "BRAIN_URL": "http://brain.test:8080",
            "LLM_ENDPOINT_URL": "http://legacy.test/v1/chat/completions",
            "LLM_API_KEY": "secret",
            "MODEL_NAME": "legacy-model",
            "SUPPORT_MODEL_NAME": "legacy-support",
        }, clear=True):
            loaded = brain.Config.from_environment()
        self.assertEqual(loaded.llm_endpoint_url, "")
        self.assertEqual(loaded.llm_api_key, "")
        self.assertEqual(loaded.model_name, "")
        self.assertEqual(loaded.support_model_name, "")

    def test_uploaded_file_content_is_sent_inline_not_as_host_path(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            service.llm = FakeLLM([({"role": "assistant", "content": "read it"}, [])])
            session = service.store.create()
            attachment = service.store.save_attachment(
                session["session_id"], "notes.txt", "text/plain",
                b"computer-only secret", "computer-only secret",
            )
            service.run_turn(session["session_id"], {
                "type": "web_user", "content": "summarize [notes.txt]",
                "references": [{
                    "type": "attachment", "id": attachment["id"],
                    "label": "notes.txt",
                }],
            }, lambda *_: None)
            user = next(message for message in reversed(service.llm.seen_messages[0]) if message["role"] == "user")
            self.assertIn("computer-only secret", user["content"])
            self.assertIn("Do not search for this file on the attached host", user["content"])
            prepared = brain.prepare_upstream_messages([user])
            self.assertNotIn("references", prepared[0])

    def test_runner_enrollment_target_and_single_use(self):
        with tempfile.TemporaryDirectory() as directory:
            store = brain.SessionStore(Path(directory) / "brain.sqlite3", "system")
            client_id = "r" * 32
            store.register_client(client_id, "user@host", "192.0.2.20")
            session = store.create()
            store.bind_client(session["session_id"], client_id, "/srv/project")
            enrollment = store.create_runner_enrollment(client_id)
            with self.assertRaises(KeyError):
                store.get_runner_enrollment(enrollment["token"], "192.0.2.21")
            result = store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", client_id, 8766,
                "192.0.2.10", "/home/user",
            )
            self.assertEqual(result["runner_id"], client_id)
            loaded = store.get(session["session_id"])
            self.assertEqual(loaded["runner_id"], client_id)
            self.assertEqual(loaded["cwd"], "/srv/project")
            self.assertEqual(
                loaded["messages"][-1]["content"],
                "The commands you run will run on the host host at IP 192.0.2.20. Durable memory is scoped to this runner; do not use memories from other runners.",
            )
            created = store.create(client_id)
            self.assertEqual(
                created["messages"][-1]["content"],
                "The commands you run will run on the host host at IP 192.0.2.20. Durable memory is scoped to this runner; do not use memories from other runners.",
            )
            bound = store.create()
            store.bind_client(bound["session_id"], client_id, "/srv/other")
            bound = store.get(bound["session_id"])
            self.assertEqual(
                bound["messages"][-1]["content"],
                "The commands you run will run on the host host at IP 192.0.2.20. Durable memory is scoped to this runner; do not use memories from other runners.",
            )
            with self.assertRaises(KeyError):
                store.complete_runner_enrollment(
                    enrollment["token"], "192.0.2.20", client_id, 8766,
                    "192.0.2.10", "/home/user",
                )
            store.set_session_runner(session["session_id"], None)
            loaded = store.get(session["session_id"])
            self.assertIsNone(loaded["runner_id"])
            self.assertEqual(
                loaded["messages"][-1]["content"],
                "No runner is selected. You cannot run commands or use durable runner memory.",
            )
            self.assertTrue(loaded["messages"][-1]["ui"]["notice"])

    def test_runner_check_records_current_version(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "r" * 32
            service.store.register_client(client_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(client_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", client_id, 8766,
                "192.0.2.10", "/home/user",
            )
            service.store.record_runner_probe(client_id, success=True)

            class OldRunnerResponse(io.BytesIO):
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            response = OldRunnerResponse(json.dumps({
                "runner_id": client_id,
                "status": "ready",
                "home": "/home/user",
                "runner_version": brain.RUNNER_VERSION,
                "future_field": "ignored",
            }).encode())
            with patch.object(brain, "urlopen", return_value=response):
                self.assertTrue(service.probe_runner(client_id))
            service.store.record_runner_probe(client_id, success=True)
            public = service.public_runner(client_id)
            self.assertEqual(public["status"], "online")
            self.assertEqual(public["last_error"], "")
            self.assertEqual(public["runner_version"], brain.RUNNER_VERSION)
            self.assertEqual(public["latest_runner_version"], brain.RUNNER_VERSION)
            self.assertEqual(public["version_status"], "latest")

    def test_legacy_runner_reports_update_available(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "r" * 32
            service.store.register_client(client_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(client_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", client_id, 8766,
                "192.0.2.10", "/home/user",
            )

            class LegacyRunnerResponse(io.BytesIO):
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            response = LegacyRunnerResponse(json.dumps({
                "runner_id": client_id,
                "status": "ready",
                "home": "/home/user",
            }).encode())
            with patch.object(brain, "urlopen", return_value=response):
                self.assertTrue(service.probe_runner(client_id))
            public = service.public_runner(client_id)
            self.assertEqual(public["status"], "online")
            self.assertEqual(public["runner_version"], 0)
            self.assertEqual(public["version_status"], "outdated")

    def test_runner_http_error_includes_response_message(self):
        error = HTTPError(
            "http://runner/v1/execute", 400, "BadRequest", {},
            io.BytesIO(b'{"error":"invalid execute request"}'),
        )
        self.assertEqual(
            brain.BrainService.runner_http_error(error),
            "HTTP 400 BadRequest: invalid execute request",
        )

    def test_runner_executes_multiple_sessions_concurrently(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", runner_id, 8766,
                "192.0.2.10", "/home/user",
            )
            sessions = [service.store.create(runner_id) for _ in range(2)]
            releases = {
                session["session_id"]: threading.Event() for session in sessions
            }
            both_started = threading.Event()
            started = set()
            started_guard = threading.Lock()

            class RunnerResponse(io.BytesIO):
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            def execute(request, *_args, **_kwargs):
                payload = json.loads(request.data)
                session_id = payload["session_id"]
                with started_guard:
                    started.add(session_id)
                    if len(started) == 2:
                        both_started.set()
                releases[session_id].wait(5)
                return RunnerResponse(json.dumps({
                    "request_id": payload["request_id"],
                    "status": "completed",
                    "exit_code": 0,
                    "output": "ok",
                    "cwd": payload["cwd"],
                    "truncated": False,
                }).encode())

            results = []
            errors = []

            def run(index):
                try:
                    results.append(service.execute_runner(
                        sessions[index], tool_call(f"call_{index}"),
                        {"decision": "allowed_once", "prefix": []},
                    ))
                except Exception as error:
                    errors.append(error)

            threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
            with patch.object(brain, "urlopen", side_effect=execute):
                for thread in threads:
                    thread.start()
                try:
                    self.assertTrue(both_started.wait(2))
                    self.assertEqual(service.public_runner(runner_id)["status"], "busy")
                    releases[sessions[0]["session_id"]].set()
                    threads[0].join(2)
                    self.assertFalse(threads[0].is_alive())
                    self.assertEqual(service.public_runner(runner_id)["status"], "busy")
                finally:
                    for release in releases.values():
                        release.set()
                    for thread in threads:
                        thread.join(2)

            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(service.public_runner(runner_id)["status"], "online")

    def test_web_runner_trusted_command_executes_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "r" * 32
            service.store.register_client(client_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(client_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", client_id, 8766,
                "192.0.2.10", "/home/user",
            )
            service.store.change_server_trust("192.0.2.20", "add", ["printf"])
            session = service.store.create(client_id)
            call = tool_call()
            service.llm = FakeLLM([
                (({"role": "assistant", "content": None, "tool_calls": [call]}), [call]),
                ({"role": "assistant", "content": "finished"}, []),
            ])
            executed = []

            def execute(current, requested, approval):
                executed.append((current["session_id"], requested["id"], approval))
                return {
                    "tool_call_id": requested["id"],
                    "content": "exit_code=0\nok",
                    "approval": approval,
                }

            events = []
            with patch.object(service, "execute_runner", side_effect=execute):
                service.run_turn(
                    session["session_id"],
                    {"type": "web_user", "content": "run it"},
                    lambda event, data: events.append((event, data)),
                )
            loaded = service.store.get(session["session_id"])
            self.assertEqual(loaded["status"], "ready")
            self.assertEqual(executed[0][2], {"decision": "trusted", "prefix": ["printf"]})
            self.assertEqual(loaded["messages"][-1]["content"], "finished")
            self.assertEqual(events[-1][0], "done")
            self.assertEqual(service.llm.seen_include_tools, [True, True])

    def test_web_runner_executes_parallel_trusted_tool_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", runner_id, 8766,
                "192.0.2.10", "/home/user",
            )
            service.store.change_server_trust("192.0.2.20", "add", ["printf"])
            session = service.store.create(runner_id)
            calls = [tool_call("call_1"), tool_call("call_2")]
            service.llm = FakeLLM([
                (
                    {"role": "assistant", "content": None, "tool_calls": calls},
                    calls,
                ),
                ({"role": "assistant", "content": "finished"}, []),
            ])
            barrier = threading.Barrier(2)

            def execute(_session, call, approval):
                barrier.wait(2)
                return {
                    "tool_call_id": call["id"],
                    "content": f"exit_code=0\n{call['id']}",
                    "approval": approval,
                }

            with patch.object(service, "execute_runner", side_effect=execute):
                service.run_turn(
                    session["session_id"],
                    {"type": "web_user", "content": "run both"},
                    lambda *_: None,
                )

            loaded = service.store.get(session["session_id"])
            tool_messages = [
                message for message in loaded["messages"]
                if message.get("role") == "tool"
            ]
            self.assertEqual(
                [message["tool_call_id"] for message in tool_messages],
                ["call_1", "call_2"],
            )
            self.assertNotIn("cancelled", json.dumps(tool_messages).lower())
            self.assertEqual(loaded["status"], "ready")

    def test_web_runner_resolves_multiple_pending_tool_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            runner_id = "r" * 32
            service.store.register_client(runner_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(runner_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", runner_id, 8766,
                "192.0.2.10", "/home/user",
            )
            session = service.store.create(runner_id)
            calls = [tool_call("call_1"), tool_call("call_2")]
            service.llm = FakeLLM([
                (
                    {"role": "assistant", "content": None, "tool_calls": calls},
                    calls,
                ),
                ({"role": "assistant", "content": "finished"}, []),
            ])
            service.run_turn(
                session["session_id"],
                {"type": "web_user", "content": "run both"},
                lambda *_: None,
            )
            waiting = service.store.get(session["session_id"])
            self.assertEqual(
                [call["id"] for call in waiting["pending_tool_calls"]],
                ["call_1", "call_2"],
            )

            first_events = []
            service.resolve_remote_command(
                session["session_id"], "call_1", "deny",
                lambda event, data: first_events.append((event, data)),
            )
            waiting = service.store.get(session["session_id"])
            self.assertEqual(waiting["status"], "awaiting_tool_results")
            self.assertEqual(
                [call["id"] for call in waiting["pending_tool_calls"]], ["call_2"]
            )
            self.assertEqual(first_events[-1][0], "tool_calls")
            self.assertEqual(len(service.llm.seen_messages), 1)

            result = {
                "tool_call_id": "call_2", "content": "exit_code=0\nok",
                "approval": {"decision": "allowed_once", "prefix": []},
            }
            with patch.object(service, "execute_runner", return_value=result):
                service.resolve_remote_command(
                    session["session_id"], "call_2", "allow_once", lambda *_: None
                )
            loaded = service.store.get(session["session_id"])
            self.assertEqual(loaded["status"], "ready")
            self.assertEqual(loaded["pending_tool_calls"], [])
            self.assertEqual(loaded["messages"][-1]["content"], "finished")

    def test_web_runner_unknown_command_waits_for_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "r" * 32
            service.store.register_client(client_id, "user@host", "192.0.2.20")
            enrollment = service.store.create_runner_enrollment(client_id)
            service.store.complete_runner_enrollment(
                enrollment["token"], "192.0.2.20", client_id, 8766,
                "192.0.2.10", "/home/user",
            )
            session = service.store.create(client_id)
            call = tool_call()
            service.llm = FakeLLM([
                (({"role": "assistant", "content": None, "tool_calls": [call]}), [call]),
                ({"role": "assistant", "content": "approved"}, []),
            ])
            service.run_turn(
                session["session_id"],
                {"type": "web_user", "content": "run it"},
                lambda *_: None,
            )
            waiting = service.store.get(session["session_id"])
            self.assertEqual(waiting["status"], "awaiting_tool_results")
            self.assertTrue(waiting["pending_tool_calls"][0]["ui"]["remote"])
            detail = service.conversation_detail(waiting)
            self.assertEqual(
                detail["pending_tool_calls"][0]["id"], call["id"]
            )
            result = {
                "tool_call_id": call["id"], "content": "exit_code=0\nok",
                "approval": {"decision": "allowed_once", "prefix": []},
            }
            with patch.object(service, "execute_runner", return_value=result):
                service.resolve_remote_command(
                    session["session_id"], call["id"], "allow_once", lambda *_: None
                )
            self.assertEqual(service.store.get(session["session_id"])["status"], "ready")

    def test_sqlite_survives_store_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = brain.SessionStore(root / "brain.sqlite3", "system")
            session = store.create()
            call = tool_call()
            store.save(
                session["session_id"],
                session["messages"],
                "awaiting_tool_results",
                [call],
                1,
            )

            reopened = brain.SessionStore(root / "brain.sqlite3", "new system")
            loaded = reopened.get(session["session_id"])
            self.assertEqual(loaded["messages"][0]["content"], "system")
            self.assertEqual(loaded["status"], "awaiting_tool_results")
            self.assertEqual(loaded["pending_tool_calls"], [call])
            self.assertFalse(loaded["archived"])

    def test_schema_4_adds_runner_version_and_branches(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO app_metadata VALUES ('schema_version', '4')"
            )
            connection.execute("CREATE TABLE runners (id TEXT PRIMARY KEY)")
            brain.SessionStore.migrate_schema(connection)
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(runners)")
            }
            self.assertIn("runner_version", columns)
            self.assertEqual(brain.SessionStore.get_schema_version(connection), 9)

    def test_message_branches_preserve_and_switch_responses(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            session = service.store.create()
            session_id = session["session_id"]
            original = session["messages"] + [
                {"role": "user", "content": "Explain status"},
                {"role": "assistant", "content": "Original response"},
            ]
            service.store.save(session_id, original, "ready", [], 0)

            alternate = service.store.create_branch(
                session_id, 0, "Explain status"
            )
            alternate.append({"role": "assistant", "content": "New response"})
            service.store.save(session_id, alternate, "ready", [], 0)
            detail = service.conversation_detail(service.store.get(session_id))
            branch = detail["messages"][0]["ui"]["branch"]
            self.assertEqual(branch["current"], 1)
            self.assertEqual(len(branch["choices"]), 2)

            original_branch = branch["choices"][0]["branch_id"]
            service.store.switch_branch(session_id, original_branch)
            loaded = service.store.get(session_id)
            self.assertEqual(loaded["messages"][-1]["content"], "Original response")
            original_detail = service.conversation_detail(loaded)
            self.assertEqual(
                original_detail["messages"][0]["ui"]["branch"]["current"], 0
            )

            edited = service.store.create_branch(session_id, 0, "Explain errors")
            edited.append({"role": "assistant", "content": "Edited response"})
            service.store.save(session_id, edited, "ready", [], 0)
            edited_detail = service.conversation_detail(service.store.get(session_id))
            self.assertEqual(
                len(edited_detail["messages"][0]["ui"]["branch"]["choices"]), 3
            )
            self.assertEqual(edited_detail["messages"][0]["content"], "Explain errors")

    def test_schema_5_backfills_active_branch(self):
        with closing(sqlite3.connect(":memory:")) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO app_metadata VALUES ('schema_version', '5')"
            )
            connection.execute(
                """CREATE TABLE sessions (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, status TEXT NOT NULL,
                    messages_json TEXT NOT NULL,
                    pending_tool_calls_json TEXT NOT NULL,
                    tool_round INTEGER NOT NULL, cwd TEXT)"""
            )
            connection.execute(
                """INSERT INTO sessions VALUES
                    ('session', 'created', 'updated', 'ready',
                     '[{"role":"user","content":"hello"}]', '[]', 0, '/srv')"""
            )
            brain.SessionStore.migrate_schema(connection)
            session = connection.execute(
                "SELECT active_branch_id FROM sessions WHERE id = 'session'"
            ).fetchone()
            branch = connection.execute(
                "SELECT * FROM session_branches WHERE session_id = 'session'"
            ).fetchone()
            self.assertEqual(session["active_branch_id"], branch["id"])
            self.assertEqual(branch["cwd"], "/srv")
            self.assertEqual(brain.SessionStore.get_schema_version(connection), 9)

    def test_summary_archive_and_newer_schema_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "brain.sqlite3"
            store = brain.SessionStore(path, "system")
            session = store.create()
            session_id = session["session_id"]
            store.save(
                session_id,
                session["messages"] + [{"role": "user", "content": "hello"}],
                "ready",
                [],
                0,
            )
            store.set_archived(session_id, True)
            summary = store.list_summaries()[0]
            self.assertEqual(summary["preview"], "hello")
            self.assertTrue(summary["archived"])

            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute(
                    "UPDATE app_metadata SET value = '999' WHERE key = 'schema_version'"
                )
            with self.assertRaisesRegex(brain.BrainError, "schema 999 is newer"):
                brain.SessionStore(path, "system")

    def test_user_turn_survives_upstream_and_client_stream_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            service.llm = FakeLLM([brain.BrainError("upstream failed")])
            failed_id = service.store.create()["session_id"]
            with self.assertRaisesRegex(brain.BrainError, "upstream failed"):
                service.run_turn(
                    failed_id,
                    {"type": "user", "content": "keep me"},
                    lambda *_: None,
                )
            failed = service.store.get(failed_id)
            self.assertEqual(failed["status"], "continuation_pending")
            self.assertEqual(failed["messages"][-1]["content"], "keep me")

            service.llm = FakeLLM([({"role": "assistant", "content": "saved"}, [])])
            detached_id = service.store.create()["session_id"]

            def disconnected(*_args):
                raise BrokenPipeError

            service.run_turn(
                detached_id,
                {"type": "user", "content": "finish anyway"},
                disconnected,
            )
            detached = service.store.get(detached_id)
            self.assertEqual(detached["status"], "ready")
            self.assertEqual(
                [message["role"] for message in detached["messages"]],
                ["system", "user", "assistant"],
            )

    def test_web_turn_cancels_stranded_commands_and_disables_new_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            call = tool_call()
            session = service.store.create()
            service.store.save(
                session["session_id"],
                session["messages"]
                + [
                    {"role": "user", "content": "inspect"},
                    {"role": "assistant", "content": None, "tool_calls": [call]},
                ],
                "awaiting_tool_results",
                [call],
                1,
            )
            service.llm = FakeLLM(
                [({"role": "assistant", "content": "continued on web"}, [])]
            )

            service.run_turn(
                session["session_id"],
                {"type": "web_user", "content": "answer without command"},
                lambda *_: None,
            )

            loaded = service.store.get(session["session_id"])
            self.assertEqual(loaded["status"], "ready")
            self.assertFalse(service.llm.seen_include_tools[0])
            self.assertEqual(
                [message["role"] for message in loaded["messages"][-3:]],
                ["tool", "user", "assistant"],
            )
            self.assertEqual(
                loaded["messages"][-3]["ui"]["approval"]["decision"],
                "cancelled",
            )

    def test_title_uses_final_exchange_and_starts_after_done(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(
                config(root, support_model_name="title-model"), "system"
            )
            session_id = service.store.create()["session_id"]
            messages = [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "inspect server"},
                {"role": "assistant", "content": None, "tool_calls": [tool_call()]},
                {"role": "tool", "tool_call_id": "call_1", "content": "private output"},
                {"role": "assistant", "content": "server looks healthy", "ui": {"reasoning": "private thinking"}},
            ]
            service.store.save(session_id, messages, "ready", [], 0)
            title_llm = FakeLLM([
                ({"role": "assistant", "content": "Server Health Inspection"}, [])
            ])
            service.llm = title_llm
            service.generate_conversation_title(session_id, messages)
            self.assertEqual(
                service.store.get(session_id)["title"], "Server Health Inspection"
            )
            title_input = json.dumps(title_llm.seen_messages)
            self.assertIn("inspect server", title_input)
            self.assertIn("server looks healthy", title_input)
            self.assertNotIn("private output", title_input)
            self.assertNotIn("private thinking", title_input)

            invalid_id = service.store.create()["session_id"]
            service.store.save(invalid_id, messages, "ready", [], 0)
            service.llm = FakeLLM([
                ({"role": "assistant", "content": "Too short"}, [])
            ])
            service.generate_conversation_title(invalid_id, messages)
            self.assertIsNone(service.store.get(invalid_id)["title"])

            failed_id = service.store.create()["session_id"]
            service.store.save(failed_id, messages, "ready", [], 0)
            service.llm = FakeLLM([brain.BrainError("title unavailable")])
            service.generate_conversation_title(failed_id, messages)
            self.assertIsNone(service.store.get(failed_id)["title"])

            order = []
            fresh_id = service.store.create()["session_id"]
            service.schedule_conversation_title = lambda *_: order.append("title")
            service.finish_completion(
                fresh_id,
                [{"role": "system", "content": "system"}, {"role": "user", "content": "hello"}],
                {"role": "assistant", "content": "hello back"},
                [],
                [],
                0,
                lambda event, _data: order.append(event),
            )
            self.assertEqual(order, ["done", "title"])

    def test_lock_entries_do_not_accumulate(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            with self.assertRaises(KeyError):
                service.run_turn(
                    "x" * 32,
                    {"type": "user", "content": "hello"},
                    lambda *_: None,
                )
            self.assertEqual(service.locks._locks, {})

    def test_live_revisions_are_session_scoped(self):
        live = brain.LiveTurns()
        first = "a" * 32
        second = "b" * 32
        revision = live.revision(first)
        live.changed(second)
        changed, current = live.wait_for_change(first, revision, timeout=0.01)
        self.assertFalse(changed)
        self.assertEqual(current, revision)
        live.changed(first)
        changed, current = live.wait_for_change(first, revision, timeout=0.01)
        self.assertTrue(changed)
        self.assertGreater(current, revision)

    def test_failed_continuation_does_not_require_command_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            call = tool_call()
            service.llm = FakeLLM(
                [
                    ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                    brain.BrainError("upstream failed"),
                    ({"role": "assistant", "content": "recovered"}, []),
                ]
            )
            session_id = service.store.create()["session_id"]
            service.run_turn(
                session_id, {"type": "user", "content": "test"}, lambda *_: None
            )
            with self.assertRaisesRegex(brain.BrainError, "upstream failed"):
                service.run_turn(
                    session_id,
                    {
                        "type": "tool_results",
                        "results": [
                            {"tool_call_id": "call_1", "content": "exit_code=0", "approval": {
                                "decision": "allowed_once", "prefix": [],
                            }}
                        ],
                    },
                    lambda *_: None,
                )
            self.assertEqual(
                service.store.get(session_id)["status"], "continuation_pending"
            )
            service.run_turn(
                session_id,
                {"type": "tool_results", "results": []},
                lambda *_: None,
            )
            loaded = service.store.get(session_id)
            tool_messages = [m for m in loaded["messages"] if m["role"] == "tool"]
            self.assertEqual(len(tool_messages), 1)
            self.assertEqual(tool_messages[0]["ui"]["approval"]["decision"], "allowed_once")
            self.assertEqual(loaded["status"], "ready")

    def test_empty_completion_after_command_saves_normal_finish_without_blank_message(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            call = tool_call()
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                ({"role": "assistant", "content": None}, []),
            ])
            session_id = service.store.create()["session_id"]
            service.run_turn(
                session_id, {"type": "user", "content": "run check"}, lambda *_: None
            )
            events = []
            service.run_turn(
                session_id,
                {
                    "type": "tool_results",
                    "results": [{
                        "tool_call_id": "call_1",
                        "content": "exit_code=0\nok",
                        "approval": {"decision": "allowed_once", "prefix": []},
                    }],
                },
                lambda event, data: events.append((event, data)),
            )
            loaded = service.store.get(session_id)
            self.assertEqual(loaded["status"], "ready")
            self.assertEqual(loaded["messages"][-1]["role"], "tool")
            self.assertEqual(events[-1], ("done", {}))

    def test_reasoning_only_completion_after_command_saves_thinking(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            call = tool_call()
            session = service.store.create()
            session_id = session["session_id"]
            service.store.save(
                session_id,
                session["messages"] + [
                    {"role": "user", "content": "run check"},
                    {"role": "assistant", "content": None, "tool_calls": [call]},
                ],
                "awaiting_tool_results",
                [call],
                1,
            )
            lines = [
                'data: {"choices":[{"delta":{"reasoning_content":"all done"}}]}\n',
                "data: [DONE]\n",
            ]
            with patch.object(
                brain, "urlopen", return_value=FakeHTTPResponse(lines)
            ):
                service.run_turn(
                    session_id,
                    {
                        "type": "tool_results",
                        "results": [{
                            "tool_call_id": "call_1",
                            "content": "exit_code=0\nok",
                            "approval": {
                                "decision": "allowed_once", "prefix": [],
                            },
                        }],
                    },
                    lambda *_: None,
                )

            loaded = service.store.get(session_id)
            self.assertEqual(loaded["status"], "ready")
            self.assertEqual(
                loaded["messages"][-1],
                {
                    "role": "assistant",
                    "content": None,
                    "ui": {"reasoning": "all done"},
                },
            )

    def test_reasoning_only_response_can_recover_with_continuation_message(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            service.llm = FakeLLM([
                brain.BrainError(brain.INTERRUPTED_RESPONSE_ERROR),
                ({"role": "assistant", "content": "continued"}, []),
            ])
            session_id = service.store.create()["session_id"]
            with self.assertRaisesRegex(brain.BrainError, "neither content"):
                service.run_turn(
                    session_id,
                    {"type": "user", "content": "original request"},
                    lambda *_: None,
                )
            interrupted = service.store.get(session_id)
            self.assertEqual(interrupted["status"], "continuation_pending")
            self.assertEqual(interrupted["messages"][-1]["content"], "original request")

            service.run_turn(
                session_id,
                {"type": "recovery", "content": brain.INTERRUPTED_CONTINUATION},
                lambda *_: None,
            )
            recovered = service.store.get(session_id)
            self.assertEqual(recovered["status"], "ready")
            self.assertEqual(
                [message.get("content") for message in recovered["messages"][-2:]],
                [brain.INTERRUPTED_CONTINUATION, "continued"],
            )

    def test_reasoning_only_output_is_saved_but_not_sent_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            session_id = service.store.create()["session_id"]
            lines = [
                'data: {"choices":[{"delta":{"reasoning_content":"last thought"}}]}\n',
                "data: [DONE]\n",
            ]
            with patch.object(
                brain, "urlopen", return_value=FakeHTTPResponse(lines)
            ), self.assertRaisesRegex(brain.BrainError, "neither content"):
                service.run_turn(
                    session_id,
                    {"type": "user", "content": "original request"},
                    lambda *_: None,
                )

            interrupted = service.store.get(session_id)
            self.assertEqual(interrupted["status"], "continuation_pending")
            self.assertEqual(
                interrupted["messages"][-1],
                {
                    "role": "assistant",
                    "content": None,
                    "ui": {"reasoning": "last thought"},
                },
            )
            self.assertEqual(
                [message["role"] for message in brain.prepare_upstream_messages(
                    interrupted["messages"]
                )],
                ["system", "user"],
            )

    def test_conversation_view_hides_system_and_shows_live_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "secret system prompt")
            blocking_llm = BlockingLLM()
            service.llm = blocking_llm
            session_id = service.store.create()["session_id"]
            errors = []

            def run_turn():
                try:
                    service.run_turn(
                        session_id,
                        {"type": "user", "content": "visible question"},
                        lambda *_: None,
                    )
                except Exception as error:  # pragma: no cover - assertion aid
                    errors.append(error)

            thread = threading.Thread(target=run_turn)
            thread.start()
            self.assertTrue(blocking_llm.started.wait(timeout=1))

            running = service.conversation_detail(service.store.get(session_id))
            self.assertTrue(running["active"])
            self.assertEqual(
                running["messages"],
                [{"role": "user", "content": "visible question"}],
            )
            self.assertEqual(
                running["live"]["transient_messages"],
                [],
            )
            self.assertEqual(running["live"]["reasoning"], "checking")
            self.assertEqual(running["live"]["content"], "answer")
            self.assertNotIn("secret system prompt", json.dumps(running))

            blocking_llm.release.set()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            complete = service.conversation_detail(service.store.get(session_id))
            self.assertFalse(complete["active"])
            self.assertEqual(
                [message["role"] for message in complete["messages"]],
                ["user", "assistant"],
            )
            reopened = brain.SessionStore(root / "brain.sqlite3", "system")
            self.assertEqual(
                reopened.get(session_id)["messages"][-1]["ui"]["reasoning"], "checking"
            )

class ClientTrustTests(unittest.TestCase):
    def test_first_runner_hostname_names_server_without_overwriting_user_name(self):
        with tempfile.TemporaryDirectory() as directory:
            store = brain.SessionStore(Path(directory) / "brain.sqlite3", "system")
            server_ip = "192.0.2.10"

            def enroll(client_id, client_name, port):
                store.register_client(client_id, client_name, server_ip)
                token = store.create_runner_enrollment(client_id)["token"]
                store.complete_runner_enrollment(
                    token, server_ip, client_id, port, "192.0.2.1", "/home/user"
                )

            enroll("a" * 32, "alice@first-host", 8766)
            self.assertEqual(store.get_server(server_ip)["name"], "first-host")

            store.set_server_name(server_ip, "Build server")
            enroll("b" * 32, "bob@second-host", 8767)
            self.assertEqual(store.get_server(server_ip)["name"], "Build server")

            store.set_server_name(server_ip, "")
            enroll("c" * 32, "carol@third-host", 8768)
            self.assertEqual(store.get_server(server_ip)["name"], "")

    def test_ip_scope_matching_restart_and_client_move(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brain.sqlite3"
            store = brain.SessionStore(path, "system")
            first, second = "a" * 32, "b" * 32
            store.register_client(first, "alice@one", "192.0.2.10")
            store.register_client(second, "bob@two", "192.0.2.20")
            sessions = [store.create()["session_id"] for _ in range(3)]
            store.bind_client(sessions[0], first)
            store.bind_client(sessions[1], first)
            store.bind_client(sessions[2], second)
            store.change_server_trust("192.0.2.10", "add", ["git"])
            store.change_server_trust("192.0.2.10", "add", ["git", "status"])
            self.assertEqual(
                store.check_command("192.0.2.10", ["git", "status", "--short"])["prefix"],
                ["git", "status"],
            )
            self.assertFalse(store.check_command("192.0.2.20", ["git", "status"])["allowed"])
            reopened = brain.SessionStore(path, "system")
            for session in sessions[:2]:
                self.assertEqual(reopened.get(session)["client"]["server_ip"], "192.0.2.10")
            self.assertTrue(reopened.check_command("192.0.2.10", ["git", "log"])["allowed"])
            reopened.register_client(first, "alice@one", "192.0.2.20")
            self.assertEqual(reopened.get_client(first)["server_ip"], "192.0.2.20")
            self.assertFalse(reopened.check_command("192.0.2.20", ["git", "log"])["allowed"])
            old_server = reopened.get_server("192.0.2.10")
            self.assertIn("alice@one", old_server["client_names"])
            self.assertEqual(old_server["name"], "")
            reopened.set_server_name("192.0.2.10", "Build server")
            reopened.register_client(first, "alice@one", "192.0.2.10")
            self.assertEqual(reopened.get_server("192.0.2.10")["name"], "Build server")
            with self.assertRaisesRegex(brain.BrainError, "another client"):
                store.bind_client(sessions[0], second)

    def test_normalizes_ipv4_mapped_ipv6(self):
        self.assertEqual(brain.normalize_ip("::ffff:192.0.2.10"), "192.0.2.10")
        self.assertEqual(brain.normalize_ip("2001:0db8::1"), "2001:db8::1")

    def test_concurrent_edits_preserve_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = brain.SessionStore(Path(directory) / "brain.sqlite3", "system")
            store.register_client("a" * 32, "alice@one", "192.0.2.10")
            errors = []

            def add(prefix):
                try:
                    store.change_server_trust("192.0.2.10", "add", prefix)
                except Exception as error:
                    errors.append(error)

            threads = [threading.Thread(target=add, args=(["tool", str(index)],)) for index in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertCountEqual(
                store.get_server("192.0.2.10")["trusted_prefixes"],
                [["tool", str(index)] for index in range(4)],
            )


class HTTPTests(unittest.TestCase):
    def test_web_ai_configuration_and_global_model_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(
                brain.replace(
                    config(Path(directory)),
                    llm_endpoint_url="", llm_api_key="", model_name="",
                ),
                "system",
            )
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
            client_server = brain.BrainHTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            client_thread = threading.Thread(
                target=client_server.serve_forever, daemon=True
            )
            thread.start()
            client_thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            client_base = f"http://127.0.0.1:{client_server.server_port}"
            headers = {
                "Content-Type": "application/json", "X-Brain-UI": "1",
                "Origin": base,
            }

            def post(path, body):
                request = Request(
                    base + path, data=json.dumps(body).encode(), headers=headers,
                    method="POST",
                )
                with urlopen(request) as response:
                    return response.status, json.load(response)

            try:
                with urlopen(base + "/v1/ai/config") as response:
                    self.assertEqual(json.load(response), {"servers": [], "active": None})
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(
                        client_base + "/v1/ai/selection", data=b"{}",
                        headers={"Content-Type": "application/json"}, method="POST",
                    ))
                self.assertEqual(denied.exception.code, 404)
                with patch.object(
                    brain, "discover_llm_models", return_value=["small", "large"]
                ):
                    status, created = post("/v1/ai/servers", {
                        "name": "Local llama.cpp",
                        "endpoint_url": "http://llm.test:8080/v1",
                        "api_key": "secret",
                    })
                    self.assertEqual(status, 201)
                    self.assertNotIn("api_key", created["server"])
                    self.assertTrue(created["server"]["active"])
                    self.assertEqual(created["server"]["selected_model"], "small")
                    status, selected = post("/v1/ai/selection", {
                        "server_id": created["server"]["server_id"],
                        "model": "large",
                    })
                    self.assertEqual(status, 200)
                    self.assertEqual(selected["server"]["selected_model"], "large")
                self.assertEqual(service.llm.current_client().config.model_name, "large")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                client_server.shutdown()
                client_server.server_close()
                client_thread.join(timeout=2)

    def test_conversation_metadata_validation_and_access(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            session = service.store.create()
            session_id = session["session_id"]
            servers = [brain.BrainHTTPServer(("127.0.0.1", 0), service, web=web) for web in (False, True)]
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
            for thread in threads:
                thread.start()
            api, web = [f"http://127.0.0.1:{server.server_port}" for server in servers]
            path = f"/v1/conversations/{session_id}/metadata"
            headers = {"Content-Type": "application/json", "X-Brain-UI": "1", "Origin": web}
            try:
                for body in [{}, {"title": ""}, {"title": "x" * 121}, {"title": "bad\nname"}, {"title": "bad\x7fname"}, {"title": 42}, {"pinned": 1}, {"pinned": None}, {"extra": True}, {"title": "keep", "pinned": "yes"}]:
                    with self.subTest(body=body), self.assertRaises(HTTPError) as error:
                        urlopen(Request(web + path, data=json.dumps(body).encode(), headers=headers))
                    self.assertEqual(error.exception.code, 400)
                self.assertIsNone(service.store.get(session_id)["title"])
                for base, request_headers, code in [
                    (api, {"Content-Type": "application/json"}, 404),
                    (web, {**headers, "Origin": "http://evil.example"}, 403),
                    (web, {"Content-Type": "application/json"}, 403),
                ]:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base + path, data=b'{"pinned":true}', headers=request_headers))
                    self.assertEqual(error.exception.code, code)
                # Metadata may change during generation without acquiring the turn lock.
                lock = service.locks.acquire(session_id)
                service.live_turns.start(session_id, [])
                revision = service.live_turns.revision(session_id)
                try:
                    with urlopen(Request(web + path, data=b'{"title":"Manual title","pinned":true}', headers=headers)) as response:
                        detail = json.load(response)
                    self.assertTrue(detail["active"])
                    self.assertTrue(detail["pinned"])
                    self.assertEqual(detail["title"], "Manual title")
                    self.assertEqual(detail["updated_at"], session["updated_at"])
                    self.assertGreater(service.live_turns.revision(session_id), revision)
                    self.assertTrue(service.list_conversation_summaries()[0]["pinned"])
                finally:
                    service.live_turns.remove(session_id)
                    service.locks.release(session_id, lock)
                missing = f"/v1/conversations/{'x' * 32}/metadata"
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(web + missing, data=b'{"pinned":true}', headers=headers))
                self.assertEqual(error.exception.code, 404)
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=2)

    def test_runner_enrollment_and_web_conversation_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "r" * 32
            service.store.register_client(client_id, "user@host", "127.0.0.1")
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            headers = {
                "Content-Type": "application/json", "X-Brain-UI": "1", "Origin": base,
            }
            try:
                request = Request(
                    base + "/v1/runner-enrollments",
                    data=json.dumps({"client_id": client_id}).encode(),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request) as response:
                    enrollment = json.load(response)
                token = enrollment["command"].split("/runner/install/", 1)[1].split()[0]
                with urlopen(base + "/runner/install/" + token) as response:
                    installer = response.read().decode()
                self.assertIn(f"EXPECTED_CLIENT_ID={client_id}", installer)
                complete = Request(
                    base + "/v1/runner-enrollments/" + token,
                    data=json.dumps({
                        "client_id": client_id,
                        "port": 8766,
                        "trusted_brain_ip": "127.0.0.1",
                        "home": "/home/user",
                    }).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(complete) as response:
                    runner = json.load(response)
                self.assertEqual(runner["runner_id"], client_id)
                service.store.record_runner_probe(client_id, success=True)
                check = Request(
                    base + f"/v1/runners/{client_id}/check",
                    data=b"{}", headers=headers, method="POST",
                )
                with patch.object(service, "probe_runner", return_value=True) as probe:
                    with urlopen(check) as response:
                        checked = json.load(response)
                probe.assert_called_once_with(client_id)
                self.assertEqual(checked["runner"]["status"], "online")
                create = Request(
                    base + "/v1/conversations",
                    data=json.dumps({"runner_id": client_id}).encode(),
                    headers=headers,
                    method="POST",
                )
                with urlopen(create) as response:
                    conversation = json.load(response)
                self.assertEqual(conversation["runner"]["runner_id"], client_id)
                with self.assertRaises(HTTPError) as duplicate:
                    urlopen(complete)
                self.assertEqual(duplicate.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_web_trust_edits_and_cross_origin_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "system")
            client_id = "c" * 32
            service.store.register_client(client_id, "user@host", "127.0.0.1")
            servers = [brain.BrainHTTPServer(("127.0.0.1", 0), service, web=web) for web in (False, True)]
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
            for thread in threads:
                thread.start()
            api_base, web_base = [f"http://127.0.0.1:{server.server_port}" for server in servers]
            path = "/v1/servers/127.0.0.1/trust"
            headers = {"Content-Type": "application/json", "X-Brain-UI": "1", "Origin": web_base}
            try:
                name_path = "/v1/servers/127.0.0.1/name"
                name_request = Request(
                    web_base + name_path,
                    data=b'{"name":"Build server"}',
                    headers=headers,
                )
                with urlopen(name_request) as response:
                    self.assertEqual(json.load(response)["name"], "Build server")
                self.assertEqual(
                    service.store.get_server("127.0.0.1")["name"], "Build server"
                )
                request = Request(web_base + path, data=json.dumps({"action": "add", "command": 'git -C "/path with spaces" status'}).encode(), headers=headers)
                with urlopen(request) as response:
                    self.assertEqual(json.load(response)["trusted_prefixes"], [["git", "-C", "/path with spaces", "status"]])
                for base, origin in [(web_base, "http://evil.example")]:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(base + path, data=b'{"action":"add","command":"bash"}', headers={**headers, "Origin": origin}))
                    self.assertEqual(error.exception.code, 403)
                for body in ({"action": "add", "command": "'unclosed"}, {"action": "remove", "prefix": []}):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(Request(web_base + path, data=json.dumps(body).encode(), headers=headers))
                    self.assertEqual(error.exception.code, 400)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(
                        web_base + name_path,
                        data=json.dumps({"name": "x" * 129}).encode(),
                        headers=headers,
                    ))
                self.assertEqual(error.exception.code, 400)
                with urlopen(Request(web_base + path, data=json.dumps({"action": "remove", "prefix": ["git", "-C", "/path with spaces", "status"]}).encode(), headers=headers)) as response:
                    self.assertEqual(json.load(response)["trusted_prefixes"], [])
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=2)

    def test_web_sse_snapshot_deltas_reconnect_and_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            service = brain.BrainService(config(Path(directory)), "hidden prompt")
            session = service.store.create()
            session_id = session["session_id"]
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}/v1/conversations/{session_id}/events"

            def receive(response):
                event = response.readline().decode().strip().removeprefix("event: ")
                data = json.loads(response.readline().decode().removeprefix("data: "))
                self.assertEqual(response.readline(), b"\n")
                return event, data

            try:
                with urlopen(url, timeout=2) as response:
                    self.assertIn("text/event-stream", response.headers["Content-Type"])
                    event, data = receive(response)
                    self.assertEqual(event, "snapshot")
                    self.assertEqual(data["messages"], [])
                    self.assertNotIn("hidden prompt", json.dumps(data))
                    service.live_turns.start(session_id, [{"role": "user", "content": "hi"}])
                    self.assertEqual(receive(response)[0], "snapshot")
                    metadata_url = url.removesuffix("events") + "metadata"
                    headers = {
                        "Content-Type": "application/json", "X-Brain-UI": "1",
                        "Origin": f"http://127.0.0.1:{server.server_port}",
                    }
                    with urlopen(Request(metadata_url, data=b'{"pinned":true}', headers=headers)):
                        pass
                    event, data = receive(response)
                    self.assertEqual(event, "snapshot")
                    self.assertTrue(data["pinned"])
                    service.live_turns.append(session_id, "reasoning", {"delta": "think"})
                    self.assertEqual(receive(response), ("reasoning", {"delta": "think"}))
                    service.live_turns.append(session_id, "content", {"delta": "hello\n"})
                    self.assertEqual(receive(response), ("content", {"delta": "hello\n"}))
                with urlopen(url, timeout=2) as response:
                    event, data = receive(response)
                    self.assertEqual(event, "snapshot")
                    self.assertEqual(data["live"]["content"], "hello\n")
                    service.store.save(session_id, session["messages"] + [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello\n"},
                    ], "ready", [], 0)
                    service.live_turns.remove(session_id)
                    event, data = receive(response)
                    self.assertEqual(event, "snapshot")
                    self.assertIsNone(data["live"])
                    self.assertEqual(len(data["messages"]), 2)
                    service.store.delete(session_id)
                    service.live_turns.changed(session_id)
                    self.assertEqual(receive(response), ("deleted", {}))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_health_session_create_get_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = brain.BrainService(config(root), "system")
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/healthz") as response:
                    self.assertEqual(json.load(response), {"status": "ready"})
                request = Request(
                    base + "/v1/sessions",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request) as response:
                    created = json.load(response)
                    self.assertEqual(response.status, 201)
                with urlopen(base + "/v1/sessions/" + created["session_id"]) as response:
                    self.assertEqual(json.load(response)["status"], "ready")
                client_id = "c" * 32
                register = Request(base + "/v1/clients", data=json.dumps({
                    "client_id": client_id, "name": "user@host",
                }).encode(), headers={"X-Forwarded-For": "203.0.113.99"}, method="POST")
                with urlopen(register) as response:
                    self.assertEqual(response.status, 200)
                bind = Request(base + "/v1/sessions/" + created["session_id"] + "/client",
                               data=json.dumps({"client_id": client_id}).encode(), method="POST")
                with urlopen(bind) as response:
                    self.assertEqual(response.status, 200)
                reopened = brain.SessionStore(root / "brain.sqlite3", "system")
                self.assertEqual(reopened.get(created["session_id"])["client"]["server_ip"], "127.0.0.1")
                trust = Request(base + "/v1/trust", data=json.dumps({
                    "prefix": ["docker", "ps"],
                }).encode(), method="POST")
                with urlopen(trust) as response:
                    self.assertEqual(json.load(response)["trusted_prefixes"], [["docker", "ps"]])
                check = Request(base + "/v1/commands/check", data=json.dumps({
                    "argv": ["docker", "ps", "--all"],
                }).encode(), method="POST")
                with urlopen(check) as response:
                    self.assertEqual(json.load(response)["prefix"], ["docker", "ps"])
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(
                        base + "/v1/commands/check",
                        data=b'{"argv":[]}',
                        method="POST",
                    ))
                self.assertEqual(error.exception.code, 400)
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(
                        base + f"/v1/clients/{client_id}/trust",
                        data=b'{"action":"add","prefix":["docker"]}',
                        method="POST",
                    ))
                self.assertEqual(error.exception.code, 404)
                delete = Request(
                    base + "/v1/sessions/" + created["session_id"], method="DELETE"
                )
                with urlopen(delete) as response:
                    self.assertEqual(response.status, 204)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_serves_uncached_rendered_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("client.sh").write_text(
                "#!/usr/bin/env bash\nprintf 'ok\\n'\n", encoding="utf-8"
            )
            service = brain.BrainService(config(root), "system")
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = Request(
                    f"http://127.0.0.1:{server.server_port}/client.sh",
                    headers={"Host": "untrusted.example"},
                )
                with urlopen(request) as response:
                    script = response.read().decode()
                    self.assertEqual(
                        response.headers["Content-Type"],
                        "text/x-shellscript; charset=utf-8",
                    )
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("BRAIN_URL=http://brain.test:8080", script)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_serves_dashboard_and_conversation_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            web = root / "web"
            web.mkdir()
            web.joinpath("index.html").write_text("dashboard", encoding="utf-8")
            service = brain.BrainService(config(root), "hidden system")
            session = service.store.create()
            service.store.save(
                session["session_id"],
                session["messages"] + [{"role": "user", "content": "hello"}],
                "ready",
                [],
                0,
            )
            server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/") as response:
                    self.assertEqual(response.read(), b"dashboard")
                    self.assertEqual(response.headers["Cache-Control"], "no-cache")
                    self.assertIn(
                        "default-src 'self'",
                        response.headers["Content-Security-Policy"],
                    )
                with urlopen(base + "/v1/conversations") as response:
                    listed = json.load(response)["conversations"]
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0]["preview"], "hello")
                with urlopen(
                    base + "/v1/conversations/" + session["session_id"]
                ) as response:
                    detail = json.load(response)
                self.assertEqual(detail["messages"], [{"role": "user", "content": "hello"}])
                self.assertNotIn("hidden system", json.dumps(detail))
                manage_headers = {
                    "Content-Type": "application/json",
                    "X-Brain-UI": "1",
                    "Origin": base,
                }
                service.llm = FakeLLM([
                    ({"role": "assistant", "content": "web answer"}, [])
                ])
                turn = Request(
                    base + f"/v1/conversations/{session['session_id']}/turns",
                    data=b'{"content":"continue from browser"}',
                    headers=manage_headers,
                    method="POST",
                )
                with urlopen(turn) as response:
                    events = response.read().decode()
                    self.assertIn("event: done", events)
                    self.assertEqual(response.headers["Content-Type"],
                                     "text/event-stream; charset=utf-8")
                continued = service.store.get(session["session_id"])
                self.assertEqual(continued["messages"][-2]["content"],
                                 "continue from browser")
                self.assertEqual(continued["messages"][-1]["content"], "web answer")
                self.assertFalse(service.llm.seen_include_tools[0])
                service.llm = FakeLLM([
                    ({"role": "assistant", "content": "edited answer"}, [])
                ])
                branch_turn = Request(
                    base + f"/v1/conversations/{session['session_id']}/turns",
                    data=b'{"content":"hello edited","branch_from":0}',
                    headers=manage_headers,
                    method="POST",
                )
                with urlopen(branch_turn) as response:
                    self.assertIn("event: done", response.read().decode())
                with urlopen(
                    base + "/v1/conversations/" + session["session_id"]
                ) as response:
                    branched = json.load(response)
                self.assertEqual(branched["messages"][0]["content"], "hello edited")
                choices = branched["messages"][0]["ui"]["branch"]["choices"]
                self.assertEqual(len(choices), 2)
                switch = Request(
                    base + f"/v1/conversations/{session['session_id']}/branches/"
                    + choices[0]["branch_id"],
                    data=b"{}",
                    headers=manage_headers,
                    method="POST",
                )
                with urlopen(switch) as response:
                    restored = json.load(response)
                self.assertEqual(restored["messages"][0]["content"], "hello")
                self.assertEqual(restored["messages"][-1]["content"], "web answer")
                archive = Request(
                    base + f"/v1/conversations/{session['session_id']}/archive",
                    data=b'{"archived":true}',
                    headers=manage_headers,
                    method="POST",
                )
                with urlopen(archive) as response:
                    self.assertTrue(json.load(response)["archived"])
                with urlopen(base + "/v1/conversations") as response:
                    self.assertTrue(
                        json.load(response)["conversations"][0]["archived"]
                    )
                delete = Request(
                    base + f"/v1/conversations/{session['session_id']}",
                    headers={"X-Brain-UI": "1", "Origin": base},
                    method="DELETE",
                )
                with urlopen(delete) as response:
                    self.assertEqual(response.status, 204)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

if __name__ == "__main__":
    unittest.main()
