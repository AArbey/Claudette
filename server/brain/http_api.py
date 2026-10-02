import json
import os
import re
import secrets
import shlex
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from server.brain.client import normalize_ip, read_bash_script, render_client_script, server_setup_command
from server.brain.errors import BrainError
from utils import log_event
from .service import BrainService
from .http_routes import *

WEB_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/api.js": ("api.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/vendor/marked.js": ("vendor/marked.js", "text/javascript; charset=utf-8"),
    "/vendor/purify.js": ("vendor/purify.js", "text/javascript; charset=utf-8"),
}


class BrainHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], service: BrainService, *, web: bool = False
    ):
        super().__init__(address, BrainHandler)
        self.service = service
        self.web = web



class BrainHandler(BaseHTTPRequestHandler):
    server: BrainHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "AIHelperBrain/1"

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        if (
            self.command == "GET"
            and self.path in {
                "/healthz", "/livez", "/readyz", "/v1/conversations",
                "/v1/servers", "/v1/runners", "/v1/ai/config",
            }
            and int(code) < 400
        ):
            return
        path = self.path
        if RUNNER_INSTALL_RE.fullmatch(path) or RUNNER_ENROLL_RE.fullmatch(path):
            path = path.rsplit("/", 1)[0] + "/[redacted]"
        log_event(
            "http_request",
            method=self.command,
            path=path,
            status=int(code),
            response_bytes=size,
            source_ip=self.client_address[0],
        )

    def log_error(self, format: str, *args: Any) -> None:
        path = getattr(self, "path", "")
        if RUNNER_INSTALL_RE.fullmatch(path) or RUNNER_ENROLL_RE.fullmatch(path):
            path = path.rsplit("/", 1)[0] + "/[redacted]"
        log_event(
            "http_error",
            method=getattr(self, "command", ""),
            path=path,
            source_ip=self.client_address[0],
            error=format % args,
        )

    def do_GET(self) -> None:
        health_route = self.path in {"/healthz", "/livez", "/readyz"}
        runner_install_match = RUNNER_INSTALL_RE.fullmatch(self.path)
        shared_runner_route = self.path in {"/runner.sh", "/file-tool.py", "/command-worker.py"} or runner_install_match is not None
        web_route = (
            self.path in WEB_ASSETS
            or self.path == "/v1/conversations"
            or self.path == "/v1/servers"
            or self.path == "/v1/runners"
            or self.path == "/v1/memories"
            or self.path == "/v1/ai/config"
            or self.path == "/v1/web-tools/config"
            or CONVERSATION_ATTACHMENTS_RE.fullmatch(self.path) is not None
            or ATTACHMENT_RE.fullmatch(self.path) is not None
            or self.path == "/v1/server-setup"
            or CONVERSATION_PATH_RE.fullmatch(self.path) is not None
            or CONVERSATION_EVENTS_RE.fullmatch(self.path) is not None
            or CONVERSATION_FILE_EDIT_RE.fullmatch(self.path) is not None
            or CONVERSATION_FILE_TOTAL_RE.fullmatch(self.path) is not None
        )
        if not health_route and not shared_runner_route and web_route != self.server.web:
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        asset = WEB_ASSETS.get(self.path)
        if asset is not None:
            filename, content_type = asset
            try:
                payload = (self.server.service.config.web_dir / filename).read_bytes()
            except OSError as error:
                self.log_error("cannot read web asset %s: %s", filename, error)
                self.send_error_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR, "web interface unavailable"
                )
                return
            self.send_bytes(
                HTTPStatus.OK,
                payload,
                content_type,
                cache_control="no-cache",
            )
            return
        if self.path == "/livez":
            self.send_json(HTTPStatus.OK, {"status": "alive"})
            return
        if self.path in {"/healthz", "/readyz"}:
            try:
                self.server.service.store.check_health()
            except BrainError as error:
                self.send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"status": "not_ready", "error": str(error)},
                    cache_control="no-store",
                )
                return
            self.send_json(
                HTTPStatus.OK, {"status": "ready"}, cache_control="no-store"
            )
            return
        if self.path == "/v1/server-setup":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            self.send_json(
                HTTPStatus.OK,
                {"command": server_setup_command(self.server.service.config)},
                cache_control="no-store",
            )
            return
        if self.path == "/client.sh":
            try:
                payload = render_client_script(self.server.service.config)
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_bytes(
                HTTPStatus.OK,
                payload,
                "text/x-shellscript; charset=utf-8",
                cache_control="no-store",
            )
            return
        if self.path == "/runner.sh":
            try:
                payload = read_bash_script(
                    self.server.service.config.runner_script_path, "runner script"
                ).encode()
            except BrainError as error:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
                return
            self.send_bytes(
                HTTPStatus.OK, payload, "text/x-shellscript; charset=utf-8",
                cache_control="no-store",
            )
            return
        if self.path == "/file-tool.py":
            try:
                payload = self.server.service.config.file_tool_path.read_bytes()
                if not payload.startswith(b"#!/usr/bin/env python3\n"):
                    raise BrainError("invalid file editor script")
            except (BrainError, OSError) as error:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
                return
            self.send_bytes(HTTPStatus.OK, payload, "text/x-python; charset=utf-8", cache_control="no-store")
            return
        if self.path == "/command-worker.py":
            try:
                payload = self.server.service.config.command_worker_path.read_bytes()
                if not payload.startswith(b"#!/usr/bin/env python3\n"):
                    raise BrainError("invalid command worker script")
            except (BrainError, OSError) as error:
                self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
                return
            self.send_bytes(HTTPStatus.OK, payload, "text/x-python; charset=utf-8", cache_control="no-store")
            return
        if runner_install_match:
            token = runner_install_match.group(1)
            try:
                enrollment = self.server.service.store.get_runner_enrollment(
                    token, normalize_ip(self.client_address[0])
                )
                payload = render_runner_installer(
                    self.server.service.config,
                    token, enrollment["client_id"],
                )
            except (BrainError, KeyError):
                self.send_error_json(
                    HTTPStatus.NOT_FOUND, "enrollment expired, used, or invalid"
                )
                return
            self.send_bytes(
                HTTPStatus.OK, payload, "text/x-shellscript; charset=utf-8",
                cache_control="no-store",
            )
            return
        client_match = CLIENT_PATH_RE.fullmatch(self.path)
        if client_match:
            try:
                client = self.server.service.store.get_client(client_match.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "client not found")
                return
            self.send_json(HTTPStatus.OK, client, cache_control="no-store")
            return
        if self.path == "/v1/conversations":
            self.send_json(
                HTTPStatus.OK,
                {
                    "conversations":
                        self.server.service.list_conversation_summaries()
                },
                cache_control="no-store",
            )
            return
        attachment_match = CONVERSATION_ATTACHMENTS_RE.fullmatch(self.path)
        if attachment_match:
            try:
                items = self.server.service.store.list_attachments(attachment_match.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found"); return
            self.send_json(HTTPStatus.OK, {"attachments": items}, cache_control="no-store"); return
        attachment_item = ATTACHMENT_RE.fullmatch(self.path)
        if attachment_item:
            try:
                item = self.server.service.store.get_attachment(attachment_item.group(2), attachment_item.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "attachment not found"); return
            self.send_json(HTTPStatus.OK, {"attachment": {k:item[k] for k in ("id","filename","mime_type","size_bytes","extracted_text")}}, cache_control="no-store"); return
        if self.path == "/v1/servers":
            self.send_json(
                HTTPStatus.OK,
                {"servers": self.server.service.store.list_servers()},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/runners":
            self.send_json(
                HTTPStatus.OK,
                {"runners": self.server.service.list_public_runners()},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/memories":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            self.send_json(
                HTTPStatus.OK,
                {
                    "memories": self.server.service.list_public_memories(),
                    "runners": self.server.service.list_public_runners(),
                },
                cache_control="no-store",
            )
            return
        if self.path == "/v1/web-tools/config":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            self.send_json(HTTPStatus.OK,
                self.server.service.store.get_web_tools_config(), cache_control="no-store")
            return
        if self.path == "/v1/ai/config":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            servers = self.server.service.store.list_ai_servers()
            active = next((item for item in servers if item["active"]), None)
            self.send_json(
                HTTPStatus.OK,
                {"servers": servers, "active": active},
                cache_control="no-store",
            )
            return
        file_total_match = CONVERSATION_FILE_TOTAL_RE.fullmatch(self.path)
        if file_total_match:
            try:
                result = self.server.service.file_edit_total(
                    file_total_match.group(1), unquote(file_total_match.group(2)),
                    unquote(file_total_match.group(3)))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found")
                return
            except FileToolError as error:
                self.send_error_json(HTTPStatus(error.status), str(error))
                return
            self.send_json(HTTPStatus.OK, result, cache_control="no-store")
            return
        file_edit_match = CONVERSATION_FILE_EDIT_RE.fullmatch(self.path)
        if file_edit_match:
            try:
                result = self.server.service.file_edit_action(
                    file_edit_match.group(1), unquote(file_edit_match.group(2)), {"action": "read"})
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found")
                return
            except FileToolError as error:
                self.send_error_json(HTTPStatus(error.status), str(error))
                return
            self.send_json(HTTPStatus.OK, result, cache_control="no-store")
            return
        events_match = CONVERSATION_EVENTS_RE.fullmatch(self.path)
        if events_match:
            self.stream_conversation(events_match.group(1))
            return
        conversation_match = CONVERSATION_PATH_RE.fullmatch(self.path)
        if conversation_match:
            try:
                session = self.server.service.store.get(
                    conversation_match.group(1)
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            self.send_json(
                HTTPStatus.OK,
                self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        jobs_match = SESSION_COMMAND_JOBS_RE.fullmatch(self.path)
        if jobs_match:
            try:
                self.server.service.store.get(jobs_match.group(1))
                jobs = self.server.service.store.list_command_jobs(jobs_match.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            self.send_json(HTTPStatus.OK, {
                "jobs": [self.server.service.public_command_job(job) for job in jobs]
            }, cache_control="no-store")
            return
        job_match = SESSION_COMMAND_JOB_RE.fullmatch(self.path)
        if job_match:
            try:
                job = self.server.service.store.get_command_job(job_match.group(2))
                if job["session_id"] != job_match.group(1):
                    raise KeyError(job_match.group(2))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "command job not found")
                return
            self.send_json(HTTPStatus.OK, {
                "job": self.server.service.public_command_job(job)
            }, cache_control="no-store")
            return
        match = SESSION_PATH_RE.fullmatch(self.path)
        if match:
            try:
                session = self.server.service.store.get(match.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            self.send_json(HTTPStatus.OK, self.server.service.public_session(session))
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "not found")

    def stream_conversation(self, session_id: str) -> None:
        service = self.server.service
        try:
            service.store.get(session_id)
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
            return
        self.connection.settimeout(15)
        self.start_event_stream()
        previous = None
        revision = -1
        live = service.live_turns

        try:
            while True:
                # Condition notifications wake viewers without polling the database.
                # Every connection starts with a snapshot, including reconnects.
                changed, revision = live.wait_for_change(
                    session_id, revision, timeout=10
                )
                if changed:
                    try:
                        detail = service.conversation_detail(
                            service.store.get(session_id)
                        )
                    except KeyError:
                        detail = None
                if changed and detail is None:
                    self.send_event("deleted", {})
                    return
                if not changed:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                current_live = detail["live"]
                old_live = previous["live"] if previous else None
                same_turn = (
                    previous is not None
                    and current_live is not None
                    and old_live is not None
                    and detail["messages"] == previous["messages"]
                    and detail["client"] == previous["client"]
                    and detail["title"] == previous["title"]
                    and detail["pinned"] == previous["pinned"]
                    and detail["archived"] == previous["archived"]
                    and current_live["started_at"] == old_live["started_at"]
                    and current_live["transient_messages"] == old_live["transient_messages"]
                    and all(
                        current_live[key].startswith(old_live[key])
                        for key in ("content", "reasoning")
                    )
                )
                if same_turn:
                    for key in ("reasoning", "content"):
                        delta = current_live[key][len(old_live[key]):]
                        if delta:
                            self.send_event(key, {"delta": delta})
                    if current_live.get("activity") != old_live.get("activity"):
                        self.send_event("activity", current_live["activity"])
                    if current_live.get("research") != old_live.get("research"):
                        self.send_event("research", current_live.get("research"))
                    if detail["command_jobs"] != previous["command_jobs"]:
                        self.send_event("command", {"jobs": detail["command_jobs"]})
                    if detail["context_usage"] != previous["context_usage"]:
                        self.send_event("context", detail["context_usage"])
                elif previous is not None:
                    old_without_context = dict(previous)
                    new_without_context = dict(detail)
                    old_without_context.pop("context_usage", None)
                    new_without_context.pop("context_usage", None)
                    old_without_context.pop("command_jobs", None)
                    new_without_context.pop("command_jobs", None)
                    if old_without_context == new_without_context:
                        if detail["command_jobs"] != previous["command_jobs"]:
                            self.send_event("command", {"jobs": detail["command_jobs"]})
                        if detail["context_usage"] != previous["context_usage"]:
                            self.send_event("context", detail["context_usage"])
                    elif detail != previous:
                        self.send_event("snapshot", detail)
                elif detail != previous:
                    self.send_event("snapshot", detail)
                previous = detail
        except (OSError, TimeoutError):
            pass  # Browser closed or stopped consuming this connection.
        finally:
            self.close_connection = True

    def do_POST(self) -> None:
        if not self.server.web and self.headers.get("Origin") is not None:
            self.close_connection = True
            self.send_error_json(HTTPStatus.FORBIDDEN, "browser writes must use the web port")
            return
        worker_match = COMMAND_JOB_UPDATE_RE.fullmatch(self.path)
        if worker_match:
            token = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not token or self.headers.get("Authorization") != f"Bearer {token}":
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "worker token required")
                return
            try:
                body = self.read_json_body()
                control = self.server.service.accept_command_job_update(
                    worker_match.group(1), token, body
                )
            except PermissionError:
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "invalid worker token")
                return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "command job not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(HTTPStatus.OK, control, cache_control="no-store")
            return
        terminal_action = SESSION_COMMAND_ACTION_RE.fullmatch(self.path)
        if terminal_action:
            if self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                body = self.read_json_body()
                session = self.server.service.store.get(terminal_action.group(1))
                client = session.get("client") or {}
                if (not client or client["client_id"] != body.get("client_id")
                    or client["server_ip"] != normalize_ip(self.client_address[0])):
                    raise PermissionError("client does not own session")
                if set(body) != {"client_id", "decision"} or not isinstance(body["decision"], str):
                    raise BrainError("command action requires client_id and decision")
            except PermissionError:
                self.send_error_json(HTTPStatus.FORBIDDEN, "client does not own session")
                return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.stream_turn_response(terminal_action.group(1), {},
                remote_action=(unquote(terminal_action.group(2)), body["decision"]))
            return
        terminal_start = SESSION_COMMAND_JOBS_RE.fullmatch(self.path)
        if terminal_start:
            if self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            lock = self.server.service.locks.acquire(terminal_start.group(1))
            if lock is None:
                self.send_error_json(HTTPStatus.CONFLICT, "session turn is running")
                return
            try:
                body = self.read_json_body()
                job = self.server.service.register_terminal_command_job(
                    terminal_start.group(1), body,
                    normalize_ip(self.client_address[0]),
                )
            except PermissionError:
                self.send_error_json(HTTPStatus.FORBIDDEN, "client does not own session")
                return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            finally:
                self.server.service.locks.release(terminal_start.group(1), lock)
            self.send_json(HTTPStatus.ACCEPTED, {
                "job": self.server.service.public_command_job(job)
            }, cache_control="no-store")
            return
        terminal_stop = SESSION_COMMAND_JOB_STOP_RE.fullmatch(self.path)
        web_stop = CONVERSATION_COMMAND_JOB_STOP_RE.fullmatch(self.path)
        if terminal_stop or web_stop:
            if bool(self.server.web) != bool(web_stop):
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if web_stop and not self.dashboard_write_allowed(require_json=True):
                return
            match = web_stop or terminal_stop
            assert match is not None
            try:
                self.read_json_body(allow_empty=True)
                job = self.server.service.store.get_command_job(match.group(2))
                if job["session_id"] != match.group(1):
                    raise KeyError(match.group(2))
                if terminal_stop:
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    self.server.service.get_command_job_for_token(job["job_id"], token)
                updated = self.server.service.store.request_command_job_stop(
                    job["job_id"]
                )
                self.server.service.notify_command_job_change(updated)
            except PermissionError:
                self.send_error_json(HTTPStatus.UNAUTHORIZED, "invalid command job token")
                return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "command job not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(HTTPStatus.ACCEPTED, {
                "job": self.server.service.public_command_job(updated)
            }, cache_control="no-store")
            return
        ai_server_match = AI_SERVER_PATH_RE.fullmatch(self.path)
        ai_models_match = AI_SERVER_MODELS_RE.fullmatch(self.path)
        if self.path in {"/v1/web-tools/config", "/v1/web-tools/test"}:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                if self.path.endswith("/test"):
                    url = body.get("searxng_url")
                    if not isinstance(url, str) or not url.strip():
                        raise BrainError("SearXNG URL required")
                    result = search_searxng("test", 1, url.strip().rstrip("/"),
                        brain_url=self.server.service.config.brain_url,
                        brain_ports={self.server.service.config.port, self.server.service.config.web_port})
                    self.send_json(HTTPStatus.OK, {"ok": True, "count": result["count"]})
                else:
                    self.send_json(HTTPStatus.OK,
                        self.server.service.store.save_web_tools_config(body),
                        cache_control="no-store")
            except (BrainError, WebToolError) as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        if self.path == "/v1/ai/servers" or ai_server_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                server_id = ai_server_match.group(1) if ai_server_match else None
                existing = (
                    self.server.service.store.get_ai_server(server_id)
                    if server_id else None
                )
                name = body.get("name")
                endpoint_url = normalize_llm_endpoint(body.get("endpoint_url"))
                api_key = body.get("api_key", None if existing else "")
                if (
                    not isinstance(name, str) or not name.strip()
                    or len(name.strip()) > 100
                    or any(ord(char) < 32 for char in name)
                ):
                    raise BrainError("AI server name requires 1-100 characters without controls")
                if api_key is not None and (
                    not isinstance(api_key, str) or len(api_key) > 8192
                    or any(character in api_key for character in "\r\n\0")
                ):
                    raise BrainError("invalid AI API key")
                discovery_key = (
                    existing["api_key"] if existing and api_key is None else api_key or ""
                )
                models = discover_llm_models(
                    endpoint_url, discovery_key,
                    self.server.service.config.llm_timeout_seconds,
                )
                saved = self.server.service.store.save_ai_server(
                    server_id, name.strip(), endpoint_url, api_key, models
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.OK if server_id else HTTPStatus.CREATED,
                {"server": {key: value for key, value in saved.items() if key != "api_key"}},
                cache_control="no-store",
            )
            return
        if ai_models_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                self.read_json_body(allow_empty=True)
                server = self.server.service.store.get_ai_server(ai_models_match.group(1))
                models = discover_llm_models(
                    server["endpoint_url"], server["api_key"],
                    self.server.service.config.llm_timeout_seconds,
                )
                saved = self.server.service.store.refresh_ai_models(
                    server["server_id"], models
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_GATEWAY, str(error))
                return
            self.send_json(
                HTTPStatus.OK,
                {"server": {key: value for key, value in saved.items() if key != "api_key"}},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/ai/selection":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                server_id, model = body.get("server_id"), body.get("model")
                if not isinstance(server_id, str) or not isinstance(model, str):
                    raise BrainError("selection requires server_id and model")
                server = self.server.service.store.get_ai_server(server_id)
                models = discover_llm_models(
                    server["endpoint_url"], server["api_key"],
                    self.server.service.config.llm_timeout_seconds,
                )
                self.server.service.store.refresh_ai_models(server_id, models)
                selected = self.server.service.store.select_ai_model(server_id, model)
                # Re-resolve model-scoped context and wake every open chat meter.
                if hasattr(self.server.service.llm, "current_client"):
                    self.server.service.llm.current_client()
                self.server.service.context_changed()
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.OK,
                {"server": {key: value for key, value in selected.items() if key != "api_key"}},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/ai/support-selection":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                server_id, model = body.get("server_id"), body.get("model")
                if not isinstance(server_id, str) or not isinstance(model, str):
                    raise BrainError("support selection requires server_id and model")
                server = self.server.service.store.get_ai_server(server_id)
                models = discover_llm_models(
                    server["endpoint_url"], server["api_key"],
                    self.server.service.config.llm_timeout_seconds,
                )
                self.server.service.store.refresh_ai_models(server_id, models)
                selected = self.server.service.store.select_ai_support_model(
                    server_id, model
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.OK,
                {"server": {key: value for key, value in selected.items() if key != "api_key"}},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/ai/support-settings":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                server_id = body.get("server_id")
                wait_for_main = body.get("wait_for_main")
                if (
                    not isinstance(server_id, str)
                    or not isinstance(wait_for_main, bool)
                ):
                    raise BrainError(
                        "support settings require server_id and boolean wait_for_main"
                    )
                saved = self.server.service.store.set_ai_support_wait(
                    server_id, wait_for_main
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.OK,
                {"server": {key: value for key, value in saved.items() if key != "api_key"}},
                cache_control="no-store",
            )
            return
        attachment_match = CONVERSATION_ATTACHMENTS_RE.fullmatch(self.path)
        if attachment_match:
            if not self.server.web or not self.dashboard_write_allowed(require_json=True): return
            try:
                body = self.read_json_body()
                filename, mime_type, encoded = body.get("filename"), body.get("mime_type", "application/octet-stream"), body.get("data")
                if not isinstance(filename, str) or not filename.strip() or not isinstance(encoded, str): raise BrainError("filename and base64 data required")
                data = base64.b64decode(encoded, validate=True)
                extracted = extract_attachment(filename, str(mime_type), data)
                item = self.server.service.store.save_attachment(attachment_match.group(1), filename.strip(), str(mime_type), data, extracted)
            except (ValueError, BrainError) as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error)); return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found"); return
            self.send_json(HTTPStatus.CREATED, {"attachment": {k:item[k] for k in ("id","filename","mime_type","size_bytes","extracted_text")}}, cache_control="no-store"); return
        enrollment_complete = RUNNER_ENROLL_RE.fullmatch(self.path)
        if enrollment_complete:
            try:
                body = self.read_json_body()
                client_id = body.get("client_id")
                port = body.get("port")
                home = body.get("home")
                if not isinstance(client_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{32}", client_id
                ):
                    raise BrainError("invalid client ID")
                if (
                    not isinstance(port, int) or isinstance(port, bool)
                    or not self.server.service.config.runner_port_start <= port
                    <= self.server.service.config.runner_port_end
                ):
                    raise BrainError("invalid runner port")
                if not isinstance(body.get("trusted_brain_ip"), str):
                    raise BrainError("invalid trusted Brain IP")
                trusted_brain_ip = normalize_ip(body["trusted_brain_ip"])
                if (
                    not isinstance(home, str) or not home.startswith("/")
                    or any(character in home for character in "\r\n\0")
                ):
                    raise BrainError("invalid runner home")
                result = self.server.service.store.complete_runner_enrollment(
                    enrollment_complete.group(1),
                    normalize_ip(self.client_address[0]), client_id, port,
                    trusted_brain_ip, home,
                )
                for session in self.server.service.store.list():
                    if session["runner_id"] == client_id:
                        self.server.service.live_turns.changed(session["session_id"])
            except KeyError:
                self.send_error_json(
                    HTTPStatus.NOT_FOUND, "enrollment expired, used, or invalid"
                )
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(HTTPStatus.CREATED, result, cache_control="no-store")
            return
        server_trust_match = SERVER_TRUST_PATH_RE.fullmatch(self.path)
        if server_trust_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                server_ip = normalize_ip(unquote(server_trust_match.group(1)))
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.change_server_trust(server_ip)
            return
        server_name_match = SERVER_NAME_PATH_RE.fullmatch(self.path)
        if server_name_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            try:
                server_ip = normalize_ip(unquote(server_name_match.group(1)))
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.change_server_name(server_ip)
            return
        stop_match = CONVERSATION_STOP_RE.fullmatch(self.path)
        if stop_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                self.read_json_body(allow_empty=True)
                stopped = self.server.service.stop_generation(stop_match.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            if not stopped:
                self.send_error_json(
                    HTTPStatus.CONFLICT, "conversation is not generating"
                )
                return
            log_event(
                "generation_stop_requested",
                session_id=stop_match.group(1),
                source_ip=normalize_ip(self.client_address[0]),
            )
            self.send_json(
                HTTPStatus.ACCEPTED, {"stopping": True}, cache_control="no-store"
            )
            return
        metadata_match = CONVERSATION_METADATA_RE.fullmatch(self.path)
        if metadata_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            session_id = metadata_match.group(1)
            try:
                changes = self.read_json_body()
                with self.server.service.live_turns._guard:
                    self.server.service.store.set_metadata(session_id, changes)
                    session = self.server.service.store.get(session_id)
                    self.server.service.live_turns.changed(session_id)
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            self.send_json(
                HTTPStatus.OK, self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        archive_match = CONVERSATION_ARCHIVE_RE.fullmatch(self.path)
        if archive_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            session_id = archive_match.group(1)
            try:
                body = self.read_json_body()
                if not isinstance(body.get("archived"), bool):
                    raise BrainError("archive request requires archived boolean")
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            lock = self.server.service.locks.acquire(session_id)
            if lock is None:
                self.send_error_json(
                    HTTPStatus.CONFLICT, "session turn is running"
                )
                return
            try:
                self.server.service.store.set_archived(
                    session_id, body["archived"]
                )
                session = self.server.service.store.get(session_id)
                self.server.service.live_turns.changed(session_id)
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            finally:
                self.server.service.locks.release(session_id, lock)
            log_event(
                "conversation_archive_changed",
                session_id=session_id,
                archived=body["archived"],
                source_ip=normalize_ip(self.client_address[0]),
            )
            self.send_json(
                HTTPStatus.OK,
                self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        runner_match = CONVERSATION_RUNNER_RE.fullmatch(self.path)
        if runner_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                if "runner_id" not in body or (
                    body["runner_id"] is not None
                    and (
                        not isinstance(body["runner_id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{32}", body["runner_id"])
                    )
                ):
                    raise BrainError("runner change requires runner_id or null")
                runner_id = body["runner_id"]
                if runner_id is not None:
                    public = self.server.service.public_runner(runner_id)
                    if public is None:
                        raise KeyError(runner_id)
                    if public["status"] not in {"online", "busy"}:
                        raise BrainError("runner is not active")
                session_id = runner_match.group(1)
                current = self.server.service.store.get(session_id)
                change_lock = self.server.service.locks.acquire(session_id)
                queued = change_lock is None or any(
                        call.get("ui", {}).get("remote")
                        for call in current["pending_tool_calls"]
                    )
                try:
                    self.server.service.store.set_session_runner(
                        session_id, runner_id, queued=queued
                    )
                finally:
                    if change_lock is not None:
                        self.server.service.locks.release(session_id, change_lock)
                self.server.service.live_turns.changed(session_id)
                session = self.server.service.store.get(session_id)
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session or runner not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.CONFLICT, str(error))
                return
            self.send_json(
                HTTPStatus.ACCEPTED if queued else HTTPStatus.OK,
                self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        file_edit_match = CONVERSATION_FILE_EDIT_RE.fullmatch(self.path)
        if file_edit_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                result = self.server.service.file_edit_action(
                    file_edit_match.group(1), unquote(file_edit_match.group(2)), body)
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "conversation not found")
                return
            except FileToolError as error:
                self.send_error_json(HTTPStatus(error.status), str(error))
                return
            self.send_json(HTTPStatus.OK, result, cache_control="no-store")
            return
        command_match = CONVERSATION_COMMAND_RE.fullmatch(self.path)
        if command_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                if not isinstance(body.get("decision"), str):
                    raise BrainError("command action requires decision")
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.stream_turn_response(
                command_match.group(1), {},
                remote_action=(unquote(command_match.group(2)), body["decision"]),
            )
            return
        branch_match = CONVERSATION_BRANCH_RE.fullmatch(self.path)
        if branch_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                self.read_json_body(allow_empty=True)
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            session_id, branch_id = branch_match.groups()
            lock = self.server.service.locks.acquire(session_id)
            if lock is None:
                self.send_error_json(
                    HTTPStatus.CONFLICT, "session turn is running"
                )
                return
            try:
                session = self.server.service.store.switch_branch(
                    session_id, branch_id
                )
                self.server.service.live_turns.changed(session_id)
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "branch not found")
                return
            finally:
                self.server.service.locks.release(session_id, lock)
            self.send_json(
                HTTPStatus.OK,
                self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        if self.path == "/v1/runner-enrollments":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                if not isinstance(body.get("client_id"), str
                ) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", body["client_id"]):
                    raise BrainError("enrollment requires client_id")
                enrollment = self.server.service.store.create_runner_enrollment(
                    body["client_id"]
                )
                installer_url = shlex.quote(
                    self.server.service.config.brain_url + "/runner/install/" + enrollment["token"]
                )
                command = (
                    f"(install_file=$(mktemp) && curl -fsS {installer_url} "
                    "-o \"$install_file\" && { if [ \"$(id -u)\" -eq 0 ]; "
                    "then bash \"$install_file\"; else sudo bash \"$install_file\"; "
                    "fi; }; status=$?; rm -f -- \"$install_file\"; exit \"$status\")"
                )
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "client not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.CREATED,
                {"command": command, "expires_at": enrollment["expires_at"]},
                cache_control="no-store",
            )
            return
        runner_update = RUNNER_UPDATE_RE.fullmatch(self.path)
        if runner_update:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                self.read_json_body(allow_empty=True)
                runner = self.server.service.update_runner(runner_update.group(1))
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "runner not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.CONFLICT, str(error))
                return
            self.send_json(HTTPStatus.OK, {"runner": runner}, cache_control="no-store")
            return
        runner_check = RUNNER_CHECK_RE.fullmatch(self.path)
        if runner_check:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                runner_id = runner_check.group(1)
                self.server.service.store.get_runner(runner_id)
                self.server.service.probe_runner(runner_id)
                runner = self.server.service.public_runner(runner_id)
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "runner not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.OK, {"runner": runner}, cache_control="no-store"
            )
            return
        if self.path == "/v1/memories":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                runner_id = body.get("runner_id")
                if runner_id is not None and (not isinstance(runner_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", runner_id)):
                    raise BrainError("memory requires valid runner_id or null")
                memory_id = body.get("memory_id")
                if memory_id is not None and (
                    not isinstance(memory_id, str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{32}", memory_id)
                ):
                    raise BrainError("invalid memory_id")
                if memory_id:
                    memory = self.server.service.store.update_memory(
                        memory_id, runner_id, body.get("key"), body.get("value")
                    )
                    status = HTTPStatus.OK
                else:
                    memory = self.server.service.store.save_memory(
                        runner_id, body.get("key"), body.get("value")
                    )
                    status = HTTPStatus.CREATED
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "runner or memory not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                status,
                {"memory": self.server.service.public_memory(memory)},
                cache_control="no-store",
            )
            return
        if self.path == "/v1/conversations":
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                if "runner_id" not in body or (
                    body["runner_id"] is not None
                    and not isinstance(body["runner_id"], str)
                ):
                    raise BrainError("conversation creation requires runner_id or null")
                if body["runner_id"] is not None:
                    runner = self.server.service.public_runner(body["runner_id"])
                    if runner is None:
                        raise KeyError(body["runner_id"])
                    if runner["status"] not in {"online", "busy"}:
                        raise BrainError("runner is not active")
                session = self.server.service.store.create(body["runner_id"])
            except KeyError:
                self.send_error_json(HTTPStatus.NOT_FOUND, "runner not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.CONFLICT, str(error))
                return
            self.send_json(
                HTTPStatus.CREATED,
                self.server.service.conversation_detail(session),
                cache_control="no-store",
            )
            return
        conversation_turn_match = CONVERSATION_TURN_RE.fullmatch(self.path)
        if conversation_turn_match:
            if not self.server.web:
                self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
                return
            if not self.dashboard_write_allowed(require_json=True):
                return
            try:
                body = self.read_json_body()
                turn_body = web_turn_body(body)
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.stream_turn_response(
                conversation_turn_match.group(1),
                turn_body,
            )
            return
        if self.server.web:
            self.close_connection = True
            self.send_error_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "only dashboard management routes are writable on the web port",
            )
            return
        if self.path == "/v1/commands/check":
            try:
                body = self.read_json_body()
                if "argv" not in body:
                    raise BrainError("command check requires argv")
                validate_argv(body["argv"])
                result = self.server.service.store.check_command(
                    normalize_ip(self.client_address[0]), body["argv"]
                )
            except KeyError:
                self.send_error_json(HTTPStatus.CONFLICT, "server must register first")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(HTTPStatus.OK, result, cache_control="no-store")
            return
        if self.path == "/v1/trust":
            try:
                body = self.read_json_body()
                if "prefix" not in body:
                    raise BrainError("trust request requires prefix")
                validate_trusted_prefixes([body["prefix"]])
                result = self.server.service.store.change_server_trust(
                    normalize_ip(self.client_address[0]), "add", body["prefix"]
                )
            except KeyError:
                self.send_error_json(HTTPStatus.CONFLICT, "server must register first")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            log_event(
                "trust_changed",
                server_ip=result["server_ip"],
                action="add",
                prefix=body["prefix"],
                source="client",
            )
            self.send_json(HTTPStatus.OK, result, cache_control="no-store")
            return
        binding_match = SESSION_CLIENT_PATH_RE.fullmatch(self.path)
        if self.path == "/v1/clients" or binding_match:
            service = self.server.service
            try:
                body = self.read_json_body()
                client_id = body.get("client_id")
                if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32}", client_id):
                    raise BrainError("invalid client ID")
                if binding_match:
                    with service.live_turns._guard:
                        cwd = body.get("cwd")
                        if cwd is not None and (
                            not isinstance(cwd, str) or not cwd.startswith("/")
                            or any(character in cwd for character in "\r\n\0")
                        ):
                            raise BrainError("invalid session cwd")
                        service.store.bind_client(binding_match.group(1), client_id, cwd)
                        client = service.store.get_client(client_id)
                        service.live_turns.changed(binding_match.group(1))
                else:
                    name = body.get("name")
                    if not isinstance(name, str) or not name.strip() or len(name) > 128:
                        raise BrainError("client name must contain 1-128 characters")
                    client = service.store.register_client(
                        client_id,
                        name.strip(),
                        normalize_ip(self.client_address[0]),
                    )
            except (KeyError, sqlite3.IntegrityError):
                self.send_error_json(HTTPStatus.NOT_FOUND, "client or session not found")
                return
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(HTTPStatus.OK, client, cache_control="no-store")
            return
        if self.path == "/v1/sessions":
            try:
                body = self.read_json_body(allow_empty=True)
                session = self.server.service.store.create()
            except BrainError as error:
                self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
                return
            self.send_json(
                HTTPStatus.CREATED, self.server.service.public_session(session)
            )
            return

        match = TURN_PATH_RE.fullmatch(self.path)
        if not match:
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        self.stream_turn_response(match.group(1), body=None)

    def stream_turn_response(
        self, session_id: str, body: dict[str, Any] | None,
        *, remote_action: tuple[str, str] | None = None,
    ) -> None:
        try:
            if body is None:
                body = self.read_json_body()
            self.server.service.store.get(session_id)
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
            return
        except BrainError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return

        self.start_event_stream()

        terminal_sent = False

        def emit(event: str, data: dict[str, Any]) -> None:
            nonlocal terminal_sent
            self.send_event(event, data)
            if event in {"tool_calls", "done", "error"}:
                terminal_sent = True

        started = time.monotonic()
        try:
            if remote_action is None:
                self.server.service.run_turn(session_id, body, emit)
            else:
                self.server.service.resolve_remote_command(
                    session_id, remote_action[0], remote_action[1], emit
                )
            log_event(
                "turn_finished",
                session_id=session_id,
                request_type=body.get("type") if remote_action is None else "remote_command",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except KeyError:
            log_event(
                "turn_failed",
                session_id=session_id,
                error="session not found",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            if not terminal_sent:
                emit("error", {"message": "session not found"})
        except BrainError as error:
            log_event(
                "turn_failed",
                session_id=session_id,
                error=str(error),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            if not terminal_sent:
                emit("error", {"message": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # Keep unexpected failures inside the SSE protocol.
            self.log_error("unexpected turn failure: %r", error)
            if not terminal_sent:
                try:
                    emit("error", {"message": "internal server error"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
        finally:
            self.close_connection = True

    def change_server_trust(self, server_ip: str) -> None:
        if not self.dashboard_write_allowed(require_json=True):
            return
        try:
            body = self.read_json_body()
            action = body.get("action")
            if action not in ("add", "remove"):
                raise BrainError("action must be add or remove")
            if "command" in body:
                if not isinstance(body["command"], str):
                    raise BrainError("prefix must be command text")
                try:
                    prefix = shlex.split(body["command"])
                except ValueError as error:
                    raise BrainError(f"invalid prefix: {error}") from error
            else:
                prefix = body["prefix"]
            validate_trusted_prefixes([prefix])
            server = self.server.service.store.change_server_trust(
                server_ip, action, prefix
            )
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "server not found")
            return
        except BrainError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        log_event(
            "trust_changed",
            server_ip=server_ip,
            action=action,
            prefix=prefix,
            source="dashboard",
        )
        self.send_json(HTTPStatus.OK, server, cache_control="no-store")

    def change_server_name(self, server_ip: str) -> None:
        if not self.dashboard_write_allowed(require_json=True):
            return
        try:
            body = self.read_json_body()
            if not isinstance(body.get("name"), str):
                raise BrainError("server name request requires name")
            raw_name = body["name"]
            if len(raw_name) > 128 or any(
                character in raw_name for character in "\r\n\0"
            ):
                raise BrainError(
                    "server name must contain at most 128 characters without line breaks"
                )
            name = raw_name.strip()
            server = self.server.service.store.set_server_name(server_ip, name)
        except KeyError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "server not found")
            return
        except BrainError as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
            return
        log_event(
            "server_name_changed",
            server_ip=server_ip,
            name=name,
            source="dashboard",
        )
        self.send_json(HTTPStatus.OK, server, cache_control="no-store")

    def dashboard_write_allowed(self, *, require_json: bool) -> bool:
        if require_json and (
            self.headers.get("Content-Type", "").split(";", 1)[0]
            != "application/json"
        ):
            self.close_connection = True
            self.send_error_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "dashboard writes require application/json",
            )
            return False
        origin = self.headers.get("Origin", "")
        host = self.headers.get("Host", "")
        if (
            self.headers.get("X-Brain-UI") != "1"
            or origin not in (f"http://{host}", f"https://{host}")
        ):
            self.close_connection = True
            self.send_error_json(
                HTTPStatus.FORBIDDEN,
                "changes must come from this dashboard",
            )
            return False
        return True

    def do_DELETE(self) -> None:
        if self.server.web:
            ai_server_match = AI_SERVER_PATH_RE.fullmatch(self.path)
            if ai_server_match:
                if not self.dashboard_write_allowed(require_json=False):
                    return
                if not self.server.service.store.delete_ai_server(ai_server_match.group(1)):
                    self.send_error_json(HTTPStatus.NOT_FOUND, "AI server not found")
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            attachment_match = ATTACHMENT_RE.fullmatch(self.path)
            if attachment_match:
                if not self.dashboard_write_allowed(require_json=False): return
                if not self.server.service.store.delete_attachment(attachment_match.group(2), attachment_match.group(1)):
                    self.send_error_json(HTTPStatus.NOT_FOUND, "attachment not found"); return
                self.send_response(HTTPStatus.NO_CONTENT); self.send_header("Content-Length", "0"); self.end_headers(); return
            memory_match = MEMORY_PATH_RE.fullmatch(self.path)
            if memory_match:
                if not self.dashboard_write_allowed(require_json=False):
                    return
                if not self.server.service.store.delete_memory(memory_match.group(1)):
                    self.send_error_json(HTTPStatus.NOT_FOUND, "memory not found")
                    return
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            match = CONVERSATION_PATH_RE.fullmatch(self.path)
            if match and not self.dashboard_write_allowed(require_json=False):
                return
        else:
            match = SESSION_PATH_RE.fullmatch(self.path)
        if not match:
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        session_id = match.group(1)
        lock = self.server.service.locks.acquire(session_id)
        if lock is None:
            self.send_error_json(HTTPStatus.CONFLICT, "session turn is running")
            return
        try:
            try:
                deleted = self.server.service.store.delete(session_id)
            except BrainError as error:
                self.send_error_json(HTTPStatus.CONFLICT, str(error))
                return
            if not deleted:
                self.send_error_json(HTTPStatus.NOT_FOUND, "session not found")
                return
            self.server.service.live_turns.changed(session_id)
        finally:
            self.server.service.locks.release(session_id, lock)
        log_event(
            "conversation_deleted",
            session_id=session_id,
            source="dashboard" if self.server.web else "client",
        )
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def read_json_body(self, *, allow_empty: bool = False) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if allow_empty:
                return {}
            raise BrainError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise BrainError("invalid Content-Length") from error
        if length < 0 or length > self.server.service.config.max_request_bytes:
            raise BrainError("request body is too large")
        if length == 0 and allow_empty:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeError) as error:
            raise BrainError("request body must be valid JSON") from error
        if not isinstance(body, dict):
            raise BrainError("request body must be a JSON object")
        return body

    def start_event_stream(self) -> None:
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()

    def send_event(self, event: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        self.wfile.write(f"event: {event}\ndata: {payload}\n\n".encode())
        self.wfile.flush()

    def send_json(
        self,
        status: HTTPStatus,
        body: dict[str, Any],
        *,
        cache_control: str | None = None,
    ) -> None:
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_bytes(
            status,
            payload,
            "application/json; charset=utf-8",
            cache_control=cache_control,
        )

    def send_bytes(
        self,
        status: HTTPStatus,
        payload: bytes,
        content_type: str,
        *,
        cache_control: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type.startswith("text/html"):
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
        if cache_control is not None:
            self.send_header("Cache-Control", cache_control)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json(status, {"error": message})

