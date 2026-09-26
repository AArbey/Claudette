"""Isolated browser fixture. LLM responses and runner execution are simulated."""
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import time

from test_brain import brain, config, tool_call


class PreviewLLM:
    def context_info(self):
        return {"max_tokens": 32768, "discovery": "ready"}

    def complete(
        self, messages, emit, *, include_tools=True, model_name=None,
        cancellation=None,
    ):
        last = messages[-1]
        if last.get("role") == "user" and last.get("content") == "request command" and include_tools:
            call = tool_call()
            return {"role": "assistant", "content": None, "tool_calls": [call]}, [call]
        content = "## Check complete\n\nEverything looks healthy.\n\n- Configuration loaded\n- Services responding\n\n```bash\ndocker ps\n```"
        emit("reasoning", {"delta": "Checking the current request."})
        for _ in range(20):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            time.sleep(.05)
        for chunk in [content[:25], content[25:]]:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            emit("content", {"delta": chunk})
            time.sleep(.3)
        return {"role": "assistant", "content": content, "reasoning": "Checking the current request."}, []


with tempfile.TemporaryDirectory(prefix="brain-web-fixture-") as directory:
    service = brain.BrainService(replace(config(Path(directory)), web_dir=Path(__file__).resolve().parents[1] / "server/web"), "PRIVATE_SYSTEM_PROMPT")
    service.llm = PreviewLLM()
    brain.discover_llm_models = lambda *_args, **_kwargs: ["test-model", "alternate-model"]
    runner_id = "r" * 32
    for ident, name, ip in [(runner_id, "deploy@production", "127.0.0.1"), ("s" * 32, "admin@staging", "192.0.2.25")]:
        service.store.register_client(ident, name, ip)
        service.store.set_server_name(ip, "Production" if ident == runner_id else "Staging")
        token = service.store.create_runner_enrollment(ident)["token"]
        service.store.complete_runner_enrollment(token, ip, ident, 8766, "127.0.0.1", "/home/deploy")
    service.store.record_runner_probe(
        runner_id, success=True, runner_version=brain.RUNNER_VERSION
    )
    service.store.record_runner_probe("s" * 32, success=False, error="offline: Connection refused. Check the service and firewall.")
    service.store.save_memory(None, "workspace.owner", "operations")
    service.store.save_memory(runner_id, "workspace.root", "/srv/production")

    def probe(ident):
        service.store.record_runner_probe(
            ident, success=True, runner_version=brain.RUNNER_VERSION
        )
        return True

    service.probe_runner = probe
    service.execute_runner = lambda session, call, approval: {
        "tool_call_id": call["id"], "content": "exit_code=0\nSimulated command output", "approval": approval,
    }
    sessions = {}
    for key, title in [("main", "Production health overview"), ("other", "Plan next maintenance window"), ("pending", "Review deployment command"), ("multi", "Review command queue"), ("failed", "Runner connection interrupted"), ("resume", "Continue server investigation"), ("archived", "Previous maintenance notes")]:
        session = service.store.create(runner_id if key in {"pending", "multi", "failed"} else None)
        sessions[key] = session["session_id"]
        messages = session["messages"] + [{"role": "user", "content": "Check server health and suggest next steps."}]
        status, pending = "ready", []
        if key in {"pending", "multi", "failed"}:
            pending = [tool_call(f"call_{index}") for index in range(1, 4)] \
                if key == "multi" else [tool_call()]
            for call in pending:
                call["ui"] = {
                    "remote": True,
                    "state": "failed" if key == "failed" else "approval",
                    "request_id": f"fixture-{call['id']}",
                }
                if key == "failed":
                    call["ui"]["error"] = "Runner unavailable. Check connection, then retry."
            messages.append({"role": "assistant", "content": None, "tool_calls": pending, "ui": {"reasoning": "Inspect current state before changing anything."}})
            status = "awaiting_tool_results"
        else:
            text = "## Everything is running smoothly\n\nYour services are healthy. Here is the current overview:\n\n| Service | Status |\n| --- | --- |\n| Brain | Ready |\n| Runner | Connected |\n\n### Suggested next steps\n\n1. Review pending updates.\n2. Confirm backup schedule.\n\n```bash\ndocker compose ps\n```\n\n[Read documentation](https://example.com)"
            if key == "main":
                text += '\n\n<script>window.pwned=1</script><img src=x onerror="window.pwned=1">\n\n[Bad](javascript:alert(1))\n\n![Remote image](https://example.com/tracker.png)'
            messages.append({"role": "assistant", "content": text, "ui": {"reasoning": "Checked service availability and recent status. " * 12}})
        if key == "resume": status = "continuation_pending"
        service.store.save(session["session_id"], messages, status, pending, 0)
        service.store.set_metadata(session["session_id"], {"title": title, "pinned": key == "main"})
        if key == "archived": service.store.set_archived(session["session_id"], True)
    research_session = service.store.create()
    sessions["research"] = research_session["session_id"]
    research_call = {"id": "research_call", "type": "function", "function": {
        "name": "deep_research", "arguments": json.dumps({"question": "What changed?"})}}
    research_trace = {"question": "What changed?", "status": "completed", "model": "test-model",
        "steps": [{"kind": "search_searxng", "target": "release notes", "status": "completed"},
                  {"kind": "load_web_page", "target": "https://example.org/release", "status": "completed"}],
        "messages": [{"role": "user", "content": "What changed?"},
                     {"role": "assistant", "content": "Read release notes [1].", "tool_calls": []}],
        "sources": [{"title": "Release notes", "url": "https://example.org/release"}]}
    research_messages = research_session["messages"] + [
        {"role": "user", "content": "What changed?"},
        {"role": "assistant", "content": None, "tool_calls": [research_call]},
        {"role": "tool", "tool_call_id": research_call["id"],
         "content": json.dumps({"ok": True, "answer": "New feature [1].", "sources": research_trace["sources"]}),
         "ui": {"web_tool": "deep_research", "research": research_trace}},
        {"role": "assistant", "content": "New feature [1]."},
    ]
    service.store.save(research_session["session_id"], research_messages, "ready", [], 0)
    service.store.set_metadata(research_session["session_id"], {"title": "Research example"})
    service.store.set_archived(research_session["session_id"], True)
    live_research = service.store.create()
    sessions["research_live"] = live_research["session_id"]
    service.store.save(live_research["session_id"], live_research["messages"] + [
        {"role": "user", "content": "Investigate release"}], "continuation_pending", [], 0)
    service.store.set_metadata(live_research["session_id"], {"title": "Live research example"})
    service.store.set_archived(live_research["session_id"], True)
    service.live_turns.start(live_research["session_id"], [])
    service.live_turns.append(live_research["session_id"], "research",
        {"call_id": "live_research_call", **{**research_trace, "status": "running"}})
    server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
    print(json.dumps({"base": f"http://127.0.0.1:{server.server_port}", "sessions": sessions}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
