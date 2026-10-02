# brain/server.py

import sys
import threading

from server.brain.service import BrainService
from .config import Config
from .utils import load_system_prompt, log_event
from .http_api import BrainHTTPServer
from .errors import BrainError


def main() -> int:
    try:
        config = Config.from_environment()
        system_prompt = load_system_prompt(
            config.system_prompt_path,
            config.knowledge_dir,
            config.max_knowledge_bytes,
        )
        service = BrainService(config, system_prompt)
        server = BrainHTTPServer((config.bind_host, config.port), service)
        try:
            web_server = BrainHTTPServer(
                (config.bind_host, config.web_port), service, web=True
            )
        except OSError:
            server.server_close()
            raise
    except BrainError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Error: cannot start Brain: {error}", file=sys.stderr)
        return 1

    log_event(
        "brain_ready",
        bind_host=config.bind_host,
        brain_port=config.port,
        web_port=config.web_port,
    )
    web_thread = threading.Thread(target=web_server.serve_forever, daemon=True)
    web_thread.start()
    service.start_runner_monitor()
    service.start_command_job_monitor()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service._runner_monitor_stop.set()
        web_server.shutdown()
        web_server.server_close()
        web_thread.join()
        server.server_close()
    return 0