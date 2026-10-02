from pathlib import Path
import shlex
import ipaddress
from urllib.parse import urlsplit

from .config import Config
from .errors import BrainError


def server_setup_command(config: Config) -> str:
    marker = "# brain-ai-helper managed launcher"
    client_url = shlex.quote(config.brain_url.rstrip("/") + "/client.sh")
    launcher = (
        f"{marker}\n"
        "ai-helper() {\n"
        "    local script\n"
        f"    script=$(curl --fail --silent --show-error --connect-timeout 5 --max-time 15 {client_url}) || return\n"
        "    bash -c \"$script\"\n"
        "}"
    )
    return (
        f"grep -Fq {shlex.quote(marker)} ~/.bashrc 2>/dev/null || "
        f"printf '\\n%s\\n' {shlex.quote(launcher)} >> ~/.bashrc; "
        "source ~/.bashrc && ai-helper"
    )


def render_client_script(config: Config) -> bytes:
    try:
        source = config.client_script_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BrainError(f"cannot read client script: {error}") from error
    except UnicodeError as error:
        raise BrainError("client script is not valid UTF-8") from error
    if not source.startswith("#!/usr/bin/env bash\n"):
        raise BrainError("client script has invalid shebang")

    assignments = {
        "BRAIN_URL": config.brain_url,
        "COMMAND_TIMEOUT_SECONDS": str(config.client_command_timeout_seconds),
        "MAX_TOOL_OUTPUT_BYTES": str(min(config.client_max_tool_output_bytes, 65536)),
        "BRAIN_CONNECT_TIMEOUT_SECONDS": str(
            config.client_brain_connect_timeout_seconds
        ),
        "BRAIN_REQUEST_TIMEOUT_SECONDS": str(
            config.client_brain_request_timeout_seconds
        ),
    }
    rendered = ["#!/usr/bin/env bash", ""]
    rendered.extend(
        f"{name}={shlex.quote(value)}" for name, value in assignments.items()
    )
    rendered.extend(["", source.split("\n", 1)[1], '\nmain "$@"\n'])
    return "\n".join(rendered).encode()


def read_bash_script(path: Path, label: str) -> str:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BrainError(f"cannot read {label}: {error}") from error
    except UnicodeError as error:
        raise BrainError(f"{label} is not valid UTF-8") from error
    if not source.startswith("#!/usr/bin/env bash\n"):
        raise BrainError(f"{label} has invalid shebang")
    return source


def render_runner_installer(config: Config, token: str, client_id: str) -> bytes:
    source = read_bash_script(config.runner_installer_path, "runner installer")
    hostname = urlsplit(config.brain_url).hostname or ""
    assignments = {
        "BRAIN_URL": config.brain_url,
        "ENROLLMENT_TOKEN": token,
        "EXPECTED_CLIENT_ID": client_id,
        "RUNNER_SOURCE_HOST": config.runner_trusted_source_ip or hostname,
        "RUNNER_PORT_START": str(config.runner_port_start),
        "RUNNER_PORT_END": str(config.runner_port_end),
    }
    rendered = ["#!/usr/bin/env bash", ""]
    rendered.extend(f"{key}={shlex.quote(value)}" for key, value in assignments.items())
    rendered.extend(["", source.split("\n", 1)[1], '\nmain "$@"\n'])
    return "\n".join(rendered).encode()

def normalize_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise BrainError("invalid server IP") from error
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return address.compressed

