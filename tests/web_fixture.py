"""Isolated browser fixture. LLM responses and runner execution are simulated."""
from dataclasses import replace
from pathlib import Path
import json
import secrets
import tempfile
import time

from test_brain import brain, config, tool_call
from test_file_tool import file_tool


class PreviewLLM:
    def context_info(self):
        return {"max_tokens": 32768, "discovery": "ready"}

    def complete(
        self, messages, emit, *, include_tools=True, model_name=None,
        cancellation=None,
    ):
        last = next(message for message in reversed(messages) if message.get("role") != "system")
        if last.get("role") == "user" and last.get("content") == "request command" and include_tools:
            call = tool_call()
            return {"role": "assistant", "content": None, "tool_calls": [call]}, [call]
        if last.get("role") == "user" and last.get("content") == "request two commands" and include_tools:
            calls = [tool_call(f"fresh_call_{index}") for index in (1, 2)]
            return {"role": "assistant", "content": None, "tool_calls": calls}, calls
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
    def update_runner(ident):
        service.store.record_runner_probe(
            ident, success=True, runner_version=brain.RUNNER_VERSION
        )
        return service.public_runner(ident)

    service.update_runner = update_runner
    service.ensure_runner_command_job = lambda session, call, approval, assistant: (
        {"job_id": "j" * 32}, None
    )
    service.run_runner_command_job = lambda session, call, approval, job, token: {
        "tool_call_id": call["id"], "content": "exit_code=0\nSimulated command output",
        "approval": approval, "command_job_id": job["job_id"],
    }
    sessions = {}
    for key, title in [("main", "Production health overview"), ("other", "Plan next maintenance window"), ("pending", "Review deployment command"), ("multi", "Review command queue"), ("first_multi", "New command queue"), ("failed", "Runner connection interrupted"), ("resume", "Continue server investigation"), ("archived", "Previous maintenance notes")]:
        session = service.store.create(runner_id if key in {"pending", "multi", "first_multi", "failed"} else None)
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
    job_session = service.store.create(runner_id)
    sessions["job"] = job_session["session_id"]
    job_call = tool_call("job_call")
    job_id = secrets.token_urlsafe(24)
    job_token = secrets.token_urlsafe(48)
    job_assistant_id = secrets.token_urlsafe(24)
    job_messages = job_session["messages"] + [
        {"role": "user", "content": "Start build"},
        {"role": "assistant", "content": None, "tool_calls": [job_call],
         "ui": {"command_message_id": job_assistant_id,
                "command_jobs": {job_call["id"]: job_id}}},
        {"role": "tool", "tool_call_id": job_call["id"],
         "content": "Command still running; provisional output",
         "ui": {"approval": {"decision": "allowed_once", "prefix": []},
                "command_job_id": job_id}},
        {"role": "assistant", "content": "Build started. You can keep chatting."},
    ]
    service.store.save(job_session["session_id"], job_messages, "ready", [], 0)
    service.store.set_metadata(job_session["session_id"], {"title": "Live build job"})
    job = service.store.create_command_job(
        job_id=job_id, session_id=job_session["session_id"],
        branch_id=job_session["active_branch_id"],
        tool_call_id=job_call["id"], assistant_message_id=job_assistant_id,
        executor="runner", runner_id=runner_id, client_id=runner_id,
        cwd="/home/deploy", command=brain.parse_command_call(job_call),
        approval={"decision": "allowed_once", "prefix": []},
        job_token=job_token, review_after_seconds=30,
        initial_lease_seconds=3600,
    )
    service.store.update_command_job(
        job_id, sequence=1, state="running", output="building 50%",
        total_bytes=12, truncated=False, exit_code=None, cwd=job["cwd"],
    )
    service.store.apply_command_job_decision(
        job_id, usual="yes", reason="Build normally takes minutes.",
        action="background",
    )

    # Real file helper backs simulated runner actions for diff/editor browser checks.
    file_state = Path(directory) / "file-state"
    file_path = Path(directory) / "sample.py"
    file_path.write_text("value = 1\n", encoding="utf-8")
    file_session = service.store.create(runner_id)
    sessions["files"] = file_session["session_id"]
    file_call = {"id": "file_call", "type": "function", "function": {
        "name": "edit_file", "arguments": json.dumps({"path": str(file_path), "operation": "replace",
            "old_text": "value = 1", "new_text": "value = 2", "reason": "Update sample"})}}
    file_result = file_tool.perform(file_state, {"action": "apply",
        "request_id": brain.file_request_id(file_session["session_id"], file_call["id"]),
        "cwd": str(Path(directory)), "path": str(file_path), "operation": "replace",
        "old_text": "value = 1", "new_text": "value = 2", "reason": "Update sample"})
    file_edit = {**file_result["edit"], "runner_id": runner_id}
    file_messages = file_session["messages"] + [
        {"role": "user", "content": "Update sample"},
        {"role": "assistant", "content": None, "tool_calls": [file_call]},
        {"role": "tool", "tool_call_id": file_call["id"],
         "content": brain.file_edit_summary(file_edit),
         "ui": {"file_edit": file_edit, "approval": {"decision": "automatic", "prefix": []}}},
        {"role": "assistant", "content": "Updated sample.py."},
    ]
    service.store.save(file_session["session_id"], file_messages, "ready", [], 0)
    service.store.set_metadata(file_session["session_id"], {"title": "File edit review"})
    created_path = Path(directory) / "created.py"
    created_session = service.store.create(runner_id)
    sessions["created_file"] = created_session["session_id"]
    created_call = {"id": "created_call", "type": "function", "function": {
        "name": "edit_file", "arguments": json.dumps({"path": str(created_path), "operation": "create",
            "old_text": "", "new_text": "print('created')\n", "reason": "Create sample"})}}
    created_result = file_tool.perform(file_state, {"action": "apply",
        "request_id": brain.file_request_id(created_session["session_id"], created_call["id"]),
        "cwd": str(Path(directory)), "path": str(created_path), "operation": "create",
        "old_text": "", "new_text": "print('created')\n", "reason": "Create sample"})
    created_edit = {**created_result["edit"], "runner_id": runner_id}
    created_messages = created_session["messages"] + [
        {"role": "user", "content": "Create sample"},
        {"role": "assistant", "content": None, "tool_calls": [created_call]},
        {"role": "tool", "tool_call_id": created_call["id"],
         "content": brain.file_edit_summary(created_edit),
         "ui": {"file_edit": created_edit, "approval": {"decision": "automatic", "prefix": []}}},
        {"role": "assistant", "content": "Created created.py."},
    ]
    service.store.save(created_session["session_id"], created_messages, "ready", [], 0)
    service.store.set_metadata(created_session["session_id"], {"title": "Created file review"})
    def runner_file_request(_runner_id, payload):
        try:
            return file_tool.perform(file_state, payload)
        except file_tool.FileError as error:
            raise brain.FileToolError(str(error), error.status) from error
    service.runner_file_request = runner_file_request

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
    pending_research = service.store.create()
    sessions["research_pending"] = pending_research["session_id"]
    service.store.save(pending_research["session_id"], pending_research["messages"] + [
        {"role": "user", "content": "Investigate updates"}], "continuation_pending", [], 0)
    service.store.set_metadata(pending_research["session_id"], {"title": "Pending research example"})
    service.store.set_archived(pending_research["session_id"], True)
    service.live_turns.start(pending_research["session_id"], [])

    class FixtureHandler(brain.BrainHandler):
        def do_GET(self):
            if self.path == "/fixture/job-update":
                service.accept_command_job_update(job_id, job_token, {
                    "sequence": 2, "state": "running", "output": "building 75%",
                    "total_bytes": 12, "truncated": False, "cwd": job["cwd"],
                })
                self.send_json(brain.HTTPStatus.OK, {"ok": True})
                return
            if self.path == "/fixture/job-finish":
                service.accept_command_job_update(job_id, job_token, {
                    "sequence": 3, "state": "stopped",
                    "output": "building 75% before stop",
                    "total_bytes": 24, "truncated": False,
                    "exit_code": 143, "cwd": job["cwd"],
                })
                self.send_json(brain.HTTPStatus.OK, {"ok": True})
                return
            if self.path == "/fixture/research-start":
                service.live_turns.append(pending_research["session_id"], "research",
                    {"call_id": "pending_research_call", **{**research_trace, "status": "running"}})
                self.send_json(brain.HTTPStatus.OK, {"ok": True})
                return
            if self.path == "/fixture/research-update":
                updated = {**research_trace, "status": "running",
                    "steps": [*research_trace["steps"],
                        {"kind": "search_searxng", "target": "change log", "status": "running"}],
                    "messages": [*research_trace["messages"], *[
                        {"role": "tool", "name": "load_web_page", "content": "Page extract " * 80}
                        for _ in range(12)]]}
                service.live_turns.append(pending_research["session_id"], "research",
                    {"call_id": "pending_research_call", **updated})
                self.send_json(brain.HTTPStatus.OK, {"ok": True})
                return
            super().do_GET()

    server = brain.BrainHTTPServer(("127.0.0.1", 0), service, web=True)
    server.RequestHandlerClass = FixtureHandler
    print(json.dumps({"base": f"http://127.0.0.1:{server.server_port}", "sessions": sessions}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
