import os
from dataclasses import dataclass
from pathlib import Path

from server.brain.errors import BrainError


@dataclass(frozen=True)
class Config:
    llm_endpoint_url: str
    llm_api_key: str
    model_name: str
    brain_url: str
    bind_host: str
    port: int
    web_port: int
    database_path: Path
    system_prompt_path: Path
    knowledge_dir: Path
    client_script_path: Path
    web_dir: Path
    max_tool_rounds: int
    max_knowledge_bytes: int
    max_request_bytes: int
    llm_timeout_seconds: int
    client_command_timeout_seconds: int
    client_max_tool_output_bytes: int
    client_brain_connect_timeout_seconds: int
    client_brain_request_timeout_seconds: int
    runner_script_path: Path = Path("/client/runner.sh")
    file_tool_path: Path = Path("/client/file_tool.py")
    command_worker_path: Path = Path("/client/command_worker.py")
    command_review_after_seconds: int = 30
    command_initial_lease_seconds: int = 3600
    command_review_timeout_seconds: int = 30
    runner_installer_path: Path = Path("/client/install-runner.sh")
    runner_port_start: int = 8766
    runner_port_end: int = 8865
    runner_probe_seconds: int = 60
    runner_online_seconds: int = 90
    runner_trusted_source_ip: str = ""

    @classmethod
    def from_environment(cls) -> "Config":
        def required(name: str) -> str:
            value = os.environ.get(name, "").strip()
            if not value:
                raise BrainError(f"{name} must be set")
            return value

        config = cls(
            # Model providers live in SQLite and are managed from Web UI.
            llm_endpoint_url="",
            llm_api_key="",
            model_name="",
            brain_url=required("BRAIN_URL"),
            bind_host=os.environ.get("BRAIN_BIND_HOST", "0.0.0.0"),
            port=int(os.environ.get("BRAIN_PORT", "8080")),
            web_port=int(os.environ.get("WEB_PORT", "8081")),
            database_path=Path(
                os.environ.get("DATABASE_PATH", "/data/brain.sqlite3")
            ),
            system_prompt_path=Path(
                os.environ.get("SYSTEM_PROMPT_PATH", "/config/system-prompt.txt")
            ),
            knowledge_dir=Path(os.environ.get("KNOWLEDGE_DIR", "/knowledge")),
            client_script_path=Path(
                os.environ.get("CLIENT_SCRIPT_PATH", "/client/main.sh")
            ),
            web_dir=Path(os.environ.get("WEB_DIR", "/web")),
            max_tool_rounds=int(os.environ.get("MAX_TOOL_ROUNDS", "8")),
            max_knowledge_bytes=int(os.environ.get("MAX_KNOWLEDGE_BYTES", "65536")),
            max_request_bytes=int(os.environ.get("MAX_REQUEST_BYTES", str(15 * 1024 * 1024))),
            llm_timeout_seconds=int(os.environ.get("LLM_TIMEOUT_SECONDS", "300")),
            client_command_timeout_seconds=int(os.environ.get("CLIENT_COMMAND_TIMEOUT_SECONDS", "30")),
            client_max_tool_output_bytes=int(os.environ.get("CLIENT_MAX_TOOL_OUTPUT_BYTES", "65536")),
            client_brain_connect_timeout_seconds=int(os.environ.get("CLIENT_BRAIN_CONNECT_TIMEOUT_SECONDS", "10")),
            client_brain_request_timeout_seconds=int(os.environ.get("CLIENT_BRAIN_REQUEST_TIMEOUT_SECONDS", "30")),
            runner_script_path=Path(
                os.environ.get("RUNNER_SCRIPT_PATH", "/client/runner.sh")
            ),
            file_tool_path=Path(os.environ.get("FILE_TOOL_PATH", "/client/file_tool.py")),
            command_worker_path=Path(os.environ.get("COMMAND_WORKER_PATH", "/client/command_worker.py")),
            command_review_after_seconds=int(os.environ.get("COMMAND_REVIEW_AFTER_SECONDS", "30")),
            command_initial_lease_seconds=int(os.environ.get("COMMAND_INITIAL_LEASE_SECONDS", "3600")),
            command_review_timeout_seconds=int(os.environ.get("COMMAND_REVIEW_TIMEOUT_SECONDS", "30")),
            runner_installer_path=Path(
                os.environ.get("RUNNER_INSTALLER_PATH", "/client/install-runner.sh")
            ),
            runner_port_start=int(os.environ.get("RUNNER_PORT_START", "8766")),
            runner_port_end=int(os.environ.get("RUNNER_PORT_END", "8865")),
            runner_probe_seconds=int(os.environ.get("RUNNER_PROBE_SECONDS", "60")),
            runner_online_seconds=int(os.environ.get("RUNNER_ONLINE_SECONDS", "90")),
            runner_trusted_source_ip=os.environ.get(
                "RUNNER_TRUSTED_SOURCE_IP", ""
            ).strip(),
        )
        if config.port == config.web_port:
            raise BrainError("WEB_PORT must differ from BRAIN_PORT")
        if config.runner_port_start > config.runner_port_end:
            raise BrainError("RUNNER_PORT_START must not exceed RUNNER_PORT_END")
        return config
