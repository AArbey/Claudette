from copy import deepcopy
import json
from shlex import shlex
import threading
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from server.brain.cancellation import TurnCancellation, cancellable_urlopen
from server.brain.config import Config
from server.brain.errors import BrainError, TurnCancelled, INTERRUPTED_RESPONSE_ERROR
from server.brain.tool_calls import merge_tool_call_deltas, validate_tool_calls
from server.brain.tool_schemas import COMMAND_TOOLS, FILE_INSPECT_TOOLS, FILE_TOOLS, MEMORY_TOOLS, RUNNER_TOOLS
from server.brain.utils import routing_arguments
from .command_calls import normalize_model_command_calls

def llm_models_url(endpoint_url: str) -> str:
    """Build model-list URL beside normalized chat-completions URL."""
    parsed = urlsplit(endpoint_url)
    path = parsed.path.removesuffix("/chat/completions") + "/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

def prepare_upstream_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Strip UI metadata and keep one leading system message for strict templates."""
    system_parts: list[str] = []
    regular: list[dict[str, Any]] = []
    for message in messages:
        cleaned = {
            key: value for key, value in message.items()
            if key not in {"ui", "references"}
        }
        if (
            cleaned.get("role") == "assistant"
            and not cleaned.get("content")
            and not cleaned.get("tool_calls")
            and message.get("ui", {}).get("reasoning")
        ):
            continue
        if cleaned.get("role") == "system":
            content = cleaned.get("content")
            if not isinstance(content, str):
                raise BrainError("system message content must be a string")
            system_parts.append(content)
        else:
            if cleaned.get("role") == "assistant" and cleaned.get("tool_calls"):
                cleaned["tool_calls"] = deepcopy(cleaned["tool_calls"])
                for call in cleaned["tool_calls"]:
                    call.pop("ui", None)
                    function = call.get("function", {})
                    if function.get("name") != "run_command":
                        continue
                    try:
                        command, runner_id = routing_arguments(json.loads(function["arguments"]))
                    except (KeyError, TypeError, ValueError, BrainError):
                        continue
                    if not isinstance(command, dict) or set(command) != {
                        "program", "arguments", "reason", "trust_prefix"
                    } or not isinstance(command["program"], str) or not isinstance(
                        command["arguments"], list
                    ) or any(not isinstance(arg, str) for arg in command["arguments"]) or not isinstance(
                        command["reason"], str
                    ):
                        continue
                    function["arguments"] = json.dumps({
                        "command": shlex.join([command["program"], *command["arguments"]]),
                        "reason": command["reason"],
                        **({"runner_id": runner_id} if runner_id is not None else {}),
                    }, ensure_ascii=False, separators=(",", ":"))
            regular.append(cleaned)
    if system_parts:
        regular.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return regular


def normalize_llm_endpoint(endpoint_url: Any) -> str:
    if not isinstance(endpoint_url, str):
        raise BrainError("AI endpoint URL required")
    value = endpoint_url.strip()
    if len(value) > 2048:
        raise BrainError("AI endpoint URL is too long")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise BrainError("AI endpoint must be an HTTP(S) URL without credentials, query, or fragment")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        completion_path = path
    elif path.endswith("/v1"):
        completion_path = path + "/chat/completions"
    else:
        completion_path = path + "/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, completion_path, "", ""))


def discover_llm_models(
    endpoint_url: str, api_key: str, timeout_seconds: int
) -> list[str]:
    endpoint_url = normalize_llm_endpoint(endpoint_url)
    models_url = llm_models_url(endpoint_url)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(models_url, headers=headers)
    try:
        with urlopen(request, timeout=min(15, timeout_seconds)) as response:
            body = response.read(1024 * 1024 + 1)
    except HTTPError as error:
        raise BrainError(f"model discovery returned HTTP {error.code}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise BrainError(f"cannot query models: {error}") from error
    if len(body) > 1024 * 1024:
        raise BrainError("model discovery response is too large")
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise BrainError("model discovery returned invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise BrainError("model discovery response has no data list")
    models: list[str] = []
    for item in payload["data"]:
        model_id = item.get("id") if isinstance(item, dict) else None
        if (
            isinstance(model_id, str)
            and model_id.strip()
            and len(model_id) <= 300
            and model_id not in models
        ):
            models.append(model_id)
        if len(models) >= 500:
            break
    if not models:
        raise BrainError("AI server returned no models")
    return models


def valid_context_window(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1024:
        return None
    return value


def context_window_from_response(payload: Any) -> int | None:
    """Read llama.cpp context size included in a completion response."""
    if not isinstance(payload, dict):
        return None
    verbose = payload.get("__verbose")
    settings = verbose.get("generation_settings") if isinstance(verbose, dict) else None
    generation_settings = payload.get("generation_settings")
    candidates = (
        payload.get("generation_settings/n_ctx"),
        payload.get("n_ctx"),
        generation_settings.get("n_ctx") if isinstance(generation_settings, dict) else None,
        settings.get("n_ctx") if isinstance(settings, dict) else None,
    )
    return next(
        (value for item in candidates if (value := valid_context_window(item))),
        None,
    )


class LLMClient:
    CONTEXT_LOOKUP_TIMEOUT_SECONDS = 3.0

    def __init__(
        self,
        config: Config,
        context_changed: Callable[[], None] | None = None,
    ):
        self.config = config
        self._context_guard = threading.Lock()
        self._context_tokens: int | None = None
        self._context_state = "unknown"
        self._context_lookup_running = False
        self._context_lookup_unsupported = False
        self._context_changed = context_changed

    def context_window(self) -> int | None:
        """Return context size learned from first real completion."""
        with self._context_guard:
            return self._context_tokens

    def context_info(self) -> dict[str, Any]:
        with self._context_guard:
            return {
                "max_tokens": self._context_tokens,
                "discovery": self._context_state,
            }

    def _notify_context_changed(self) -> None:
        if self._context_changed is not None:
            self._context_changed()

    def _set_context(self, tokens: int) -> None:
        changed = False
        with self._context_guard:
            if self._context_tokens != tokens or self._context_state != "ready":
                self._context_tokens = tokens
                self._context_state = "ready"
                changed = True
        if changed:
            self._notify_context_changed()

    def _props_url(self) -> str:
        parts = urlsplit(self.config.llm_endpoint_url)
        path = parts.path.rstrip("/")
        for suffix in ("/v1/chat/completions", "/chat/completions"):
            if path.endswith(suffix):
                path = path[:-len(suffix)]
                break
        query = urlencode({"model": self.config.model_name})
        return urlunsplit((parts.scheme, parts.netloc, f"{path}/props", query, ""))

    def _start_context_lookup(self) -> None:
        if self._context_changed is None:
            return
        with self._context_guard:
            if (
                self._context_tokens is not None
                or self._context_lookup_running
                or self._context_lookup_unsupported
            ):
                return
            self._context_lookup_running = True
            self._context_state = "loading"
        self._notify_context_changed()

        def lookup() -> None:
            result: dict[str, Any] = {"state": "unknown", "tokens": None}
            finished = threading.Event()
            request = Request(self._props_url(), headers={"Accept": "application/json"})
            if self.config.llm_api_key:
                request.add_header("Authorization", f"Bearer {self.config.llm_api_key}")

            def probe() -> None:
                try:
                    with urlopen(
                        request, timeout=self.CONTEXT_LOOKUP_TIMEOUT_SECONDS
                    ) as response:
                        payload = json.load(response)
                    settings = (
                        payload.get("default_generation_settings")
                        if isinstance(payload, dict)
                        else None
                    )
                    tokens = valid_context_window(
                        settings.get("n_ctx") if isinstance(settings, dict) else None
                    )
                    result.update(
                        state="ready" if tokens else "unavailable", tokens=tokens
                    )
                except HTTPError as error:
                    result["state"] = (
                        "unavailable"
                        if error.code in {400, 404, 405, 501}
                        else "unknown"
                    )
                except (
                    OSError,
                    TimeoutError,
                    URLError,
                    ValueError,
                    json.JSONDecodeError,
                    AttributeError,
                ):
                    result["state"] = "unknown"
                finally:
                    finished.set()

            threading.Thread(
                target=probe, name="context-properties-request", daemon=True
            ).start()
            finished.wait(self.CONTEXT_LOOKUP_TIMEOUT_SECONDS)
            state = result["state"] if finished.is_set() else "unknown"
            tokens = result["tokens"] if finished.is_set() else None
            changed = False
            with self._context_guard:
                self._context_lookup_running = False
                if self._context_tokens is None:
                    self._context_tokens = tokens
                    self._context_state = state
                    self._context_lookup_unsupported = state == "unavailable"
                    changed = True
            if changed:
                self._notify_context_changed()

        threading.Thread(target=lookup, name="context-properties", daemon=True).start()

    def complete(
        self,
        messages: list[dict[str, Any]],
        emit: Callable[[str, dict[str, Any]], None],
        *,
        include_tools: bool = True,
        include_memory_tools: bool = True,
        model_name: str | None = None,
        cancellation: TurnCancellation | None = None,
        emit_activity: bool = False,
        include_web_tools: bool = True,
        _non_thinking_retry: bool = False,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        upstream_messages = prepare_upstream_messages(messages)
        payload: dict[str, Any] = {
            "model": model_name or self.config.model_name,
            # UI notices remain visible in history. Strict chat templates require
            # their model-visible system context to be folded into the first message.
            "messages": upstream_messages,
            "stream": True,
        }
        with self._context_guard:
            discover_context = model_name is None and self._context_tokens is None
            request_context = discover_context and not self._context_lookup_unsupported
        if request_context:
            # llama.cpp includes generation_settings in this same streamed response.
            # No metadata request means llama-swap loads model only for user's request.
            payload["verbose"] = True
        tools = []
        if include_tools:
            tools.extend(FILE_INSPECT_TOOLS)
            tools.extend(FILE_TOOLS)
            tools.extend(RUNNER_TOOLS)
            tools.extend(COMMAND_TOOLS)
        if include_memory_tools:
            tools.extend(MEMORY_TOOLS)
        if include_web_tools:
            tools.extend(getattr(self, "web_tools", []))
        if tools:
            payload.update({"tools": tools, "tool_choice": "auto"})
        if _non_thinking_retry:
            payload.update({
                "reasoning_effort": "none",
                "chat_template_kwargs": {"enable_thinking": False},
            })

        headers = {"Content-Type": "application/json"}
        if self.config.llm_api_key:
            headers["Authorization"] = f"Bearer {self.config.llm_api_key}"
        request = Request(
            self.config.llm_endpoint_url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )

        content_parts: list[str] = []
        reasoning_seen = False
        tool_call_slots: list[dict[str, Any] | None] = []
        done = False
        response: Any = None
        try:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            with cancellable_urlopen(
                request, self.config.llm_timeout_seconds, cancellation
            ) as response:
                if cancellation is not None:
                    cancellation.bind(response)
                if response.status < 200 or response.status >= 300:
                    raise BrainError(f"LLM returned HTTP {response.status}")
                for raw_line in response:
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    try:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                    except UnicodeError as error:
                        raise BrainError("LLM stream is not valid UTF-8") from error
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        raise BrainError("LLM returned non-data SSE content")
                    data = line[5:].lstrip()
                    if data == "[DONE]":
                        if done:
                            raise BrainError("LLM returned duplicate [DONE]")
                        done = True
                        continue
                    if done:
                        raise BrainError("LLM returned data after [DONE]")
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise BrainError("LLM returned invalid JSON in SSE event") from error
                    if not isinstance(event, dict):
                        raise BrainError("LLM SSE event must be an object")
                    if discover_context:
                        context_tokens = context_window_from_response(event)
                        if context_tokens:
                            self._set_context(context_tokens)
                        elif request_context:
                            self._start_context_lookup()
                    error_value = event.get("error")
                    if isinstance(error_value, dict) and error_value.get("message"):
                        raise BrainError(str(error_value["message"]))

                    choices = event.get("choices")
                    if not isinstance(choices, list):
                        raise BrainError("LLM SSE event has no choices")
                    if not choices:
                        # OpenAI-compatible streams may send a final usage-only chunk.
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise BrainError("LLM SSE event has invalid choice")
                    delta = choice.get("delta")
                    if not isinstance(delta, dict):
                        raise BrainError("LLM SSE event has invalid delta")

                    reasoning = delta.get("reasoning_content", "")
                    content = delta.get("content", "")
                    if reasoning is None:
                        reasoning = ""
                    if content is None:
                        content = ""
                    if not isinstance(reasoning, str) or not isinstance(content, str):
                        raise BrainError("LLM returned non-string content delta")
                    if reasoning:
                        reasoning_seen = True
                        if emit_activity:
                            emit("activity", {"phase": "thinking"})
                        emit("reasoning", {"delta": reasoning})
                    if content:
                        if emit_activity:
                            emit("activity", {"phase": "writing"})
                        content_parts.append(content)
                        emit("content", {"delta": content})
                    if "tool_calls" in delta:
                        merge_tool_call_deltas(tool_call_slots, delta["tool_calls"])
                        names = [
                            call["function"]["name"] for call in tool_call_slots
                            if call and call["function"]["name"]
                        ]
                        if emit_activity:
                            emit("activity", {
                                "phase": "preparing_tool",
                                "tools": [{"name": name, "state": "preparing"} for name in names],
                            })
        except HTTPError as error:
            raise BrainError(f"LLM returned HTTP {error.code}") from error
        except URLError as error:
            raise BrainError(f"cannot reach LLM: {error.reason}") from error
        except TimeoutError as error:
            raise BrainError("LLM request timed out") from error
        except (OSError, ValueError) as error:
            if cancellation is not None and cancellation.cancelled:
                raise TurnCancelled() from error
            raise BrainError(f"cannot read LLM stream: {error}") from error
        except Exception as error:
            # Closing urllib's response from another thread can surface
            # implementation-specific exceptions from its buffered iterator.
            if cancellation is not None and cancellation.cancelled:
                raise TurnCancelled() from error
            raise
        finally:
            if cancellation is not None and response is not None:
                cancellation.unbind(response)

        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if not done:
            raise BrainError("LLM returned incomplete SSE stream")
        if any(call is None for call in tool_call_slots):
            raise BrainError("LLM returned sparse tool call indexes")
        tool_calls = [call for call in tool_call_slots if call is not None]
        validate_tool_calls(tool_calls)
        normalize_model_command_calls(tool_calls)
        content = "".join(content_parts)
        follows_tool_result = bool(upstream_messages) and upstream_messages[-1].get("role") == "tool"
        if not content.strip() and not tool_calls:
            if reasoning_seen and not _non_thinking_retry and "qwen3" in payload["model"].lower():
                return self.complete(
                    messages, emit, include_tools=include_tools,
                    include_memory_tools=include_memory_tools, model_name=model_name,
                    cancellation=cancellation, emit_activity=emit_activity,
                    include_web_tools=include_web_tools,
                    _non_thinking_retry=True,
                )
            if reasoning_seen or _non_thinking_retry or not follows_tool_result:
                raise BrainError(INTERRUPTED_RESPONSE_ERROR)

        assistant: dict[str, Any] = {
            "role": "assistant",
            "content": content if content.strip() else None,
        }
        if tool_calls:
            assistant["tool_calls"] = tool_calls
        return assistant, tool_calls

