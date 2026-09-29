"""Focused checks for Brain-hosted search, page loading, and research tools."""

import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_brain import brain, config, FakeHTTPResponse, FakeLLM, tool_call
import web_tools


def web_call(name, arguments, ident="web_1"):
    return {"id": ident, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }}


class WebToolsTests(unittest.TestCase):
    def make_service(self, root):
        service = brain.BrainService(config(root), "system")
        service.store.save_web_tools_config({
            "searxng_url": "http://search.example:8888", "default_results": 8,
            "research_server_id": None, "research_model": None,
        })
        return service

    def test_config_persists_and_model_falls_back_after_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = service.store.save_ai_server(
                None, "research", "http://model.example/v1/chat/completions", "", ["fast"])
            service.store.select_ai_model(server["server_id"], "fast")
            saved = service.store.save_web_tools_config({
                "searxng_url": "http://search.example:8888", "default_results": 12,
                "research_server_id": server["server_id"], "research_model": "fast",
            })
            self.assertEqual(saved["effective_research_model"], "fast")
            self.assertEqual(service.store.get_web_tools_config()["default_results"], 12)
            service.store.delete_ai_server(server["server_id"])
            self.assertTrue(service.store.get_web_tools_config()["research_model_fallback"])
            with self.assertRaisesRegex(brain.BrainError, "between 1 and 20"):
                service.store.save_web_tools_config({
                    "searxng_url": "http://search.example", "default_results": 21,
                    "research_server_id": None, "research_model": None,
                })

    def test_advertises_web_tools_in_chat_without_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = service.store.save_ai_server(
                None, "main", "http://model.example/v1/chat/completions", "", ["main"])
            service.store.select_ai_model(server["server_id"], "main")
            names = {item["function"]["name"] for item in service.llm.current_client().web_tools}
            self.assertEqual(names, {"search_searxng", "load_web_page", "deep_research"})
            call = web_call("search_searxng", {"query": "test"})
            session = service.store.create()
            service.store.set_metadata(session["session_id"], {"title": "Web search test"})
            service.llm = FakeLLM([
                ({"role": "assistant", "content": None, "tool_calls": [call]}, [call]),
                ({"role": "assistant", "content": "Found source"}, []),
            ])
            with patch.object(brain, "search_searxng", return_value={"query": "test", "results": [], "count": 0}):
                service.run_turn(session["session_id"], {"type": "web_user", "content": "find it"}, lambda *_: None)
            loaded = service.store.get(session["session_id"])
            self.assertEqual(loaded["status"], "ready")
            self.assertFalse(service.llm.seen_include_tools[0])
            self.assertEqual(loaded["messages"][-2]["tool_call_id"], call["id"])
            self.assertEqual(loaded["messages"][-1]["content"], "Found source")

    def test_chat_only_upstream_payload_exposes_web_functions(self):
        with tempfile.TemporaryDirectory() as directory:
            client = brain.LLMClient(config(Path(directory)))
            client.web_tools = brain.WEB_TOOLS
            lines = ['data: {"choices":[{"delta":{"content":"Ready"}}]}\n',
                     'data: [DONE]\n']
            with patch.object(brain, "urlopen", return_value=FakeHTTPResponse(lines)) as upstream:
                client.complete([{"role": "user", "content": "search"}], lambda *_: None,
                    include_tools=False, include_memory_tools=False)
            payload = json.loads(upstream.call_args.args[0].data)
            names = {tool["function"]["name"] for tool in payload["tools"]}
            self.assertEqual(names, {"search_searxng", "load_web_page", "deep_research"})
            self.assertNotIn("run_command", names)

    def test_stop_retains_completed_internal_results(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            session = service.store.create()
            first = web_call("search_searxng", {"query": "first"}, "first")
            second = web_call("load_web_page", {"url": "https://example.org"}, "second")
            cancellation = brain.TurnCancellation()
            completed = {"role": "tool", "tool_call_id": "first", "content": "ok"}
            def execute(call, _cancellation, **_kwargs):
                if call["id"] == "first":
                    return completed
                cancellation.cancel()
                cancellation.raise_if_cancelled()
            with patch.object(service, "execute_web_call", side_effect=execute):
                with self.assertRaises(brain.TurnCancelled) as stopped:
                    service.split_tool_calls(session, [first, second],
                        cancellation=cancellation, assistant={"role": "assistant", "tool_calls": [first, second]})
            self.assertEqual(stopped.exception.completed_results, [completed])

    def test_mixed_cli_command_and_web_call_only_command_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            session = service.store.create()
            service.store.set_metadata(session["session_id"], {"title": "Mixed tools test"})
            search = web_call("search_searxng", {"query": "test"}, "web_1")
            command = tool_call("command_1")
            service.llm = FakeLLM([({"role": "assistant", "content": None,
                "tool_calls": [search, command]}, [search, command])])
            with patch.object(brain, "search_searxng", return_value={"query": "test", "results": [], "count": 0}):
                service.run_turn(session["session_id"], {"type": "user", "content": "check"}, lambda *_: None)
            loaded = service.store.get(session["session_id"])
            self.assertEqual(loaded["status"], "awaiting_tool_results")
            self.assertEqual([call["id"] for call in loaded["pending_tool_calls"]], ["command_1"])
            self.assertEqual(loaded["messages"][-1]["tool_call_id"], "web_1")

    def test_stop_and_restart_keep_research_trace_and_close_tool_calls(self):
        call = web_call("deep_research", {"question": "why"}, "research_1")
        assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
        trace = {"question": "why", "status": "running", "steps": [
            {"kind": "search_searxng", "target": "why", "status": "completed"}],
            "messages": [], "sources": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = self.make_service(root)
            session = service.store.create()
            sid = session["session_id"]
            messages = [*session["messages"], {"role": "user", "content": "why"}]
            service.store.save(sid, messages, "continuation_pending", [], 0)
            service.store.save_research_progress(sid, assistant, call["id"], trace)
            restarted = brain.BrainService(config(root), "system")
            loaded = restarted.store.get(sid)
            self.assertEqual(loaded["status"], "ready")
            self.assertEqual(loaded["messages"][-2]["tool_calls"][0]["id"], call["id"])
            self.assertEqual(loaded["messages"][-1]["tool_call_id"], call["id"])
            self.assertEqual(loaded["messages"][-1]["ui"]["research"]["status"], "interrupted")
            sid2 = restarted.store.create()["session_id"]
            restarted.live_turns.start(sid2, [])
            restarted.live_turns.append(sid2, "research", {"call_id": call["id"], **trace})
            events = []
            restarted.save_stopped_turn(sid2, [assistant], lambda event, data: events.append((event, data)))
            stopped = restarted.store.get(sid2)
            self.assertEqual(stopped["messages"][-1]["ui"]["research"]["status"], "stopped")
            self.assertEqual(events[-1][1], {"stopped": True})

    def test_search_paginates_to_requested_limit_and_explains_json_403(self):
        pages = [
            {"results": [{"url": f"https://example.org/{i}", "title": f"Result {i}", "content": "snippet"}
                         for i in range(start, start + 10)]}
            for start in (0, 10)
        ]
        with patch.object(web_tools, "fetch_http", side_effect=[
            ("http://search/search", "application/json", json.dumps(page).encode()) for page in pages
        ]) as fetch:
            result = web_tools.search_searxng("query", 15, "http://search", brain_url="http://brain.test:8080", brain_ports={8080, 8081})
        self.assertEqual(result["count"], 15)
        self.assertIn("pageno=2", fetch.call_args_list[1].args[0])
        with patch.object(web_tools, "fetch_http", side_effect=web_tools.WebToolError("HTTP 403")):
            with self.assertRaisesRegex(web_tools.WebToolError, "enable json"):
                web_tools.search_searxng("query", 2, "http://search", brain_url="http://brain.test:8080", brain_ports={8080, 8081})

    def test_real_http_fetch_redirect_guard_and_stop(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                if self.path.startswith("/search"):
                    body = json.dumps({"results": [{"url": "https://example.org/a", "title": "A", "content": "Snippet"}]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:8080/private")
                    self.end_headers()
                elif self.path == "/redirect-ok":
                    self.send_response(302)
                    self.send_header("Location", "/search")
                    self.end_headers()
                elif self.path == "/large":
                    body = b"x" * (2 * 1024 * 1024 + 1)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", "10000")
                    self.end_headers()
                    self.wfile.flush()
                    time.sleep(1)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        options = {"brain_url": "http://127.0.0.1:8080", "brain_ports": {8080, 8081}}
        try:
            found = web_tools.search_searxng("test", 1, base, **options)
            self.assertEqual(found["count"], 1)
            final_url, _, body = web_tools.fetch_http(base + "/redirect-ok", allow_loopback=True, **options)
            self.assertEqual(final_url, base + "/search")
            self.assertIn(b"Snippet", body)
            with self.assertRaisesRegex(web_tools.WebToolError, "Brain service address"):
                web_tools.fetch_http(base + "/redirect", allow_loopback=True, **options)
            with self.assertRaisesRegex(web_tools.WebToolError, "2 MiB"):
                web_tools.fetch_http(base + "/large", allow_loopback=True, **options)
            cancellation = brain.TurnCancellation()
            errors = []
            worker = threading.Thread(target=lambda: self._capture_fetch(
                base + "/slow", options, cancellation, errors), daemon=True)
            worker.start()
            time.sleep(.1)
            cancellation.cancel()
            worker.join(2)
            self.assertFalse(worker.is_alive(), "Stop must interrupt slow response")
            self.assertTrue(errors and isinstance(errors[0], brain.TurnCancelled), errors)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    @staticmethod
    def _capture_fetch(url, options, cancellation, errors):
        try:
            web_tools.fetch_http(url, allow_loopback=True, cancellation=cancellation, **options)
            errors.append("completed")
        except Exception as error:
            errors.append(error)

    def test_search_skips_credential_urls(self):
        payload = {"results": [{"url": "http://user:secret@example.org/private", "title": "Bad"},
                               {"url": "https://example.org/public", "title": "Good"}]}
        with patch.object(web_tools, "fetch_http", return_value=(
            "http://search/search", "application/json", json.dumps(payload).encode())):
            result = web_tools.search_searxng("query", 1, "http://search",
                brain_url="http://brain.test:8080", brain_ports={8080, 8081})
        self.assertEqual([item["title"] for item in result["results"]], ["Good"])

    def test_page_address_guards_allow_intranet_but_block_local_services(self):
        options = {"brain_url": "http://127.0.0.1:8080", "brain_ports": {8080, 8081}}
        with self.assertRaisesRegex(web_tools.WebToolError, "localhost"):
            web_tools.fetch_http("http://localhost:8888/", **options)
        with self.assertRaisesRegex(web_tools.WebToolError, "blocked"):
            web_tools.fetch_http("http://169.254.169.254/latest", **options)
        with patch.object(web_tools.socket, "getaddrinfo", return_value=[
            (2, 1, 6, "", ("192.168.1.20", 8000))
        ]):
            infos = web_tools._addresses("intranet.test", 8000, allow_loopback=False, **options)
        self.assertEqual(infos[0][4][0], "192.168.1.20")

    def test_deep_research_uses_only_web_tools_and_cites_read_page(self):
        class ResearchModel:
            def __init__(self, *_args, **_kwargs):
                self.web_tools = []
                self.calls = 0
            def complete(self, messages, emit, **kwargs):
                assert kwargs["include_tools"] is False
                assert kwargs["include_memory_tools"] is False
                assert {t["function"]["name"] for t in self.web_tools} == {"search_searxng", "load_web_page"}
                self.calls += 1
                if self.calls == 1:
                    call = web_call("search_searxng", {"query": "question"}, "search")
                    return {"role": "assistant", "content": None, "tool_calls": [call]}, [call]
                if self.calls == 2:
                    call = web_call("load_web_page", {"url": "https://example.org/source"}, "page")
                    return {"role": "assistant", "content": None, "tool_calls": [call]}, [call]
                emit("content", {"delta": "Clear answer [1]."})
                return {"role": "assistant", "content": "Clear answer [1]."}, []
        with tempfile.TemporaryDirectory() as directory:
            service = self.make_service(Path(directory))
            server = service.store.save_ai_server(None, "research", "http://model.example/v1/chat/completions", "", ["r"])
            service.store.save_web_tools_config({
                "searxng_url": "http://search.example:8888", "default_results": 8,
                "research_server_id": server["server_id"], "research_model": "r",
            })
            def execute(call, _cancellation, **_kwargs):
                if call["function"]["name"] == "search_searxng":
                    payload = {"ok": True, "results": [{"url": "https://example.org/source"}]}
                else:
                    payload = {"ok": True, "title": "Source", "url": "https://example.org/source", "text": "Evidence"}
                return {"role": "tool", "tool_call_id": call["id"], "content": json.dumps(payload)}
            with patch.object(brain, "LLMClient", ResearchModel), patch.object(service, "execute_web_call", side_effect=execute):
                result, trace = service.run_deep_research("question", None, None)
            self.assertIn("[1] Source — https://example.org/source", result["answer"])
            self.assertEqual(trace["status"], "completed")
            self.assertEqual(len(trace["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
