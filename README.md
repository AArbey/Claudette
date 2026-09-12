# AI helper

AI helper has one central Brain container and multiple lightweight Bash clients.

- Brain owns LLM credentials, model configuration, system instructions,
  knowledge, conversation history, and tool-call orchestration.
- Brain owns trusted command prefixes and checks every requested command.
- Clients and optional remote runners execute commands on their local machine.
- Brain orchestrates remote execution but never executes client commands inside
  Brain container.

Conversation sessions live in SQLite and survive Brain restarts. Each client
stores only its last session ID and asks whether to resume it on startup.

## Requirements

Brain host:

- Docker with Compose
- Reachability to an OpenAI-compatible `/chat/completions` endpoint
- Model configured to use function tools and streamed SSE responses

Client machines:

- Bash 4+
- `curl`, `jq`, `timeout`, and standard GNU utilities

Command to add the ai-helper command to the .bashrc :

```bash
echo 'ai-helper() {
    local script
    script=$(curl --fail --silent --show-error \
        --connect-timeout 5 --max-time 15 \
        http://192.168.1.115:8093/client.sh) || return
    bash -c "$script"
}' >> ~/.bashrc
source ~/.bashrc
```

Optional remote runner additionally needs Debian 12 or 13 with systemd.

## Start Brain

Create Brain configuration:

```bash
cp .env.example .env
chmod 600 .env
editor .env
```

Required model setting:

```bash
MODEL_NAME=MiniCPM5-1B-Q4_K_M
```

Default LLM endpoint:

```bash
LLM_ENDPOINT_URL=http://192.168.1.111:9292/v1/chat/completions
```

`LLM_API_KEY` may stay empty when local LLM server does not require one.

Set `BRAIN_URL` to stable private Brain address used by clients and runner
installers, for example `http://192.168.1.115:8080`.

Start container:

```bash
docker compose up -d --build
docker compose logs -f brain
```

Brain uses Docker host networking. Incoming services bind directly to
`BRAIN_PORT` and `WEB_PORT`; runner requests originate from Brain host IP. This
keeps same-host and remote runner source-IP checks consistent.

Brain loads prompt and knowledge, initializes SQLite, then starts. Temporary LLM
unavailability affects conversation requests, not Brain startup.

Check service:

```bash
curl http://127.0.0.1:8080/healthz
```

Expected response:

```json
{"status":"ready"}
```

`/livez` checks HTTP process only. `/readyz` checks SQLite availability.
`/healthz` remains readiness alias used by Bash clients.

SQLite data lives in `brain-data` Docker volume. Brain migrates known sequential
schema versions and refuses database newer than running Brain.

## Web workspace

Open Brain host on the separate web port in a browser:

```text
http://192.168.1.115:8081/
```

Dashboard lists every stored client conversation and uses SSE
to receive live reasoning and answer text. Thinking is saved with completed
responses, including responses requesting tools, and remains collapsible after
completion or reconnect. Thinking starts expanded and can be hidden. Command
cards show readable commands, approval status,
and collapsible output instead of raw tool JSON. Open/closed sections remain
stable during live updates.

Choose **System**, **Light**, or **Dark** from the sidebar. System follows device
appearance; explicit choices persist in this browser. On narrower screens,
open navigation with the menu button; Escape or the backdrop closes it.

### Durable memory

Open **Memories** in the sidebar to inspect durable memory. Memories can be global
or scoped to runner. Global memories appear in every chat; runner memories retain
server labels. The composer plus button references a memory inline.

Brain adds an index of all saved memories to model context as reference data, not instructions, and
exposes three Brain-owned tools to the model: `save_memory`,
`recall_memory`, and `delete_memory`. These tools are executed inside Brain, not on
the remote runner, and never require command approval. `save_memory` upserts by a
case-insensitive key, so models should use short stable keys such as `project.root`
or `user.shell`. `recall_memory` searches requested scope; pass `memory_id` for exact lookup.

Composer plus button supports text/code, PDF, and DOCX uploads, plus server path
references. Uploaded text is stored per conversation and included when referenced.

Memories and attachments are stored in SQLite alongside conversations. Schema
version 8 permits global memory rows and adds attachment storage. Reinstalling the
same runner identity keeps its memory; deleting that runner removes its scoped
memory through the database foreign key. The
web memory API is served only on `WEB_PORT`, matching the rest of the dashboard
management surface.

Sidebar search matches conversation titles and previews, or server names and
IPs in Servers. Search does not switch the selected conversation. Conversation
actions (**•••**) include Rename, Pin/Unpin, Archive/Restore, and Delete. Pins
appear first within the current filter. Names and pins are shared across
operators and browsers through Brain; manual titles cannot be overwritten by
delayed automatic title generation.

Completed answers render Markdown with tables and code blocks. Copy buttons
cover messages, code, commands, and output. Rendering libraries ship locally;
HTML, embedded images, and unsafe links are disabled. Thinking remains collapsible.
Use **Jump to latest** to return to new messages after scrolling back.

Every user message has **Edit** and **Resend** actions. Either action creates a
new response branch while preserving the original continuation. Arrow controls
beside a branched message switch between its saved continuations. Each branch
retains its own messages, pending command state, and working directory.

Use composer below transcript to continue inactive CLI conversation from web.
Without runner, web conversation remains chat-only. With active runner selected,
Brain may dispatch approved commands to that server. If CLI stopped with pending
command calls, sending web message cancels those calls before continuing.
Interrupted turns accept new instruction directly. Archived conversations must
be restored first.

Drafts are separate for each conversation and survive navigation/reload within
the browser tab using session storage. You can draft the next message while
Brain replies. Desktop Enter sends and Shift+Enter adds a line; touch devices
use the Send button. A pending approval banner opens the relevant command.
Connection failures keep drafts available and expose Retry. If a message was
accepted before a connection error, check the transcript before continuing;
the interface does not automatically resubmit it.

Choose **New conversation** and optionally select active runner. Existing
conversation header can switch target. Target change during active turn queues
until turn ends and creates visible transcript notice. CLI owner remains unchanged.

The **Servers** section lists source IPs in the left sidebar. Select a server,
then manage its trusted prefixes. The first installed runner supplies its hostname
as the default display name; edit or clear that persistent name at any time.
Each observed CLI identity shows runner status: not installed, offline, online,
busy, or error. It also shows installed runner release and whether latest release
bundled with Brain is installed. **Install / repair** updates outdated runners.
**Setup** generates single-use command valid for ten minutes.
Type a prefix such as `docker ps` and choose **Add prefix**, or use the remove
button beside an existing prefix. Quote arguments containing spaces, for example
`git -C "/srv/my project" status`. Prefixes contain 1-8 exact argument tokens;
shell expansion is never performed. Clients seen from the same IP share policy.
This includes clients behind the same NAT address.

Brain stores the authoritative policy in SQLite (`/data/brain.sqlite3`, in the
`brain-data` volume). Each client asks Brain to check exact argv before every
command. If checking fails or Brain returns malformed policy, command does not
run. Web changes apply before next command; they do not cancel running commands
or commands explicitly allowed once. Removing a specific prefix still leaves
broader matching prefixes effective.

New source IP policies start empty. Existing local prefix files are ignored and
left untouched; no permissions are imported. Choosing `[t]` in terminal adds
prefix to shared source-IP policy and updates dashboard.
Command badges record existing trust, newly saved trust, one-time approval,
denial, cancellation, and invalid requests. Suggested prefixes grant no access.

Reconnecting loads a fresh snapshot. Dashboard polls only active view every
three seconds. Conversation list reads compact stored summaries instead of
parsing every transcript. System prompt and knowledge are never returned by the
conversation API. Saved thinking and approval metadata are display-only and
are excluded from upstream model requests. Earlier thinking and decisions
that were never recorded cannot be recovered.

Conversation filter shows current, archived, all, or sessions needing resume.
Selected inactive conversations may be archived, restored, or permanently
deleted. Deletion asks for confirmation. Live conversations cannot be deleted.
Optional support-model titles run after completed answer is saved and terminal
receives `done`; accepted titles must contain 3-7 words.

Set `WEB_PORT` in `.env` to change the web port (default `8081`). It must
differ from `BRAIN_PORT`. Web assets and `/v1/conversations` routes are served
only on the web port; Bash clients continue using `BRAIN_PORT`.

Brain dashboard/API has no operator authentication. Anyone able to reach Brain can read conversations and
change trusted prefixes or generate runner enrollment, granting command execution
on clients. Keep
both ports restricted to trusted operators. Web edits require same-origin JSON
requests; this protects against cross-site browser requests, not untrusted LAN users.

## Optional always-available runner

Run `ai-helper` once on target Debian user, then open **Servers**, select server,
and choose **Install / repair** beside that user. Copy generated command. It
downloads to a temporary file, runs as root directly or through `sudo`, then
removes the temporary file.

```bash
(install_file=$(mktemp) && curl -fsS http://BRAIN:8081/runner/install/ONE_TIME_TOKEN -o "$install_file" && { if [ "$(id -u)" -eq 0 ]; then bash "$install_file"; else sudo bash "$install_file"; fi; }; status=$?; rm -f -- "$install_file"; exit "$status")
```

Direct root installation is supported. When already logged in as root, either
`| bash` or `| sudo bash` installs runner for root. With `sudo` from another
account, runner stays assigned to that account through `SUDO_USER`.

Installer verifies existing stable client ID, installs only missing `jq` or
`coreutils`, installs Bash worker and per-user systemd units, then enables socket.
It does not install Python, edit firewall, modify shell files, or replace system
packages. Commands run as installing user with `NoNewPrivileges=yes`.

systemd listens without persistent worker. `Accept=yes` starts one Bash process
for each health or execution HTTP connection; process exits after response.
Each runner accepts up to 64 simultaneous connections. Multiple users use
separate ports selected from `8766-8865`.

Brain dispatches trusted tool calls from one model response concurrently.
Untrusted calls remain pending until each receives an approval or denial.

Runner accepts only authenticated HTTP/1.1 from configured Brain source IP.
Protocol requires `Content-Length` and `Connection: close`; chunking, malformed
headers, oversized requests, invalid argv, and slow reads are rejected. Bearer
credential remains stored in Brain SQLite; runner stores SHA-256 digest only.

Brain checks runner every 60 seconds. **Check now** tests reachability,
credentials, runner ID, ready state, and home path.
Successful check remains online for 90 seconds. Firewall and routing must allow
Brain host to connect directly to selected server port. Set
`RUNNER_TRUSTED_SOURCE_IP` when Brain URL host differs from source IP observed by
runners.

Each command uses deterministic request ID. Runner caches completed result.
Repeated request returns cached result. Interrupted request returns
`outcome_unknown` and never executes again automatically.

## Configure clients

Add this function to `~/.bashrc` on each client server:

```bash
ai-helper() {
    local script
    script=$(curl --fail --silent --show-error \
        --connect-timeout 5 --max-time 15 \
        http://192.168.1.115:8093/client.sh) || return
    bash -c "$script"
}
```

Replace address with Brain private address when different, then reload shell:

```bash
source ~/.bashrc
ai-helper
```

Every invocation downloads current client from Brain. Brain injects configured
`BRAIN_URL`, connection limits, and execution limits. No client configuration
file or installed client script is needed.

Docker + this `.bashrc` function is the supported launch path. `client/main.sh`
contains the client implementation; Brain injects settings and its entry point
when serving `/client.sh`.

Downloaded client stores only local state:

- stable client ID in `~/.local/state/ai-helper/client-id`
- last Brain session ID in `~/.local/state/ai-helper/session-id`

These files use local user's HOME and permissions `0600`. Stable client ID keeps
conversation ownership across restarts and `/new` conversations. Trust follows
source IP instead. Moving client to another IP applies that IP's policy.
Do not copy client-ID file to another machine unless it should share identity.
A saved session associated with a different client starts a new session instead.
Edit prefixes in Servers web section or use `[t]`.

On startup, valid locally saved session prompts:

```text
Resume previous session [R] / new [n]?
```

Press Enter to resume. Use `/new` during conversation to preserve current
session in Brain history and create another. `exit` preserves current session.
Terminal input keeps every line from multiline clipboard pastes in one message.

Brain records accepted user input and command results before contacting LLM.
If network, client stream, or LLM fails, use `/resume` to retry continuation
without losing input or running submitted commands again. Brain finishes and
saves an in-progress response even when terminal stream disconnects.

If model emits reasoning but no final answer or tool call before running any
command, client explains that response was interrupted and asks
`Continue chat? [y/N]`. Answering `yes` sends
`Your session was interrupted, continue` as a recovery turn. If empty response
comes immediately after command result, Brain treats command result as normal
end of turn instead. Saved commands are never run again.

## Command policy

Model receives one tool: `run_command`.

Every request includes exact program, argument array, reason, and suggested
trusted argv prefix. Client either denies, allows once, runs and trusts prefix,
or replaces pending request with a new instruction.

```text
AI requested function: run_command
AI requests command:
  docker ps
Reason: "List running Docker containers."
Trust option: allow future commands from this server IP starting with:
  docker ps
Choosing [y] runs once; [t] saves this shared IP prefix in Brain.
Output will be sent to AI.
Allow [y] once, [t] run + trust prefix, [i] send new instruction, [N] deny?
```

Stored prefix `docker ps` permits `docker ps --format ...`; it does not permit
`docker psx` or `docker run`. Longest exact argv-token prefix wins.

Approved commands receive a small clean environment. LLM and Brain credentials
are never copied into command environment. Commands receive `TERM` at timeout
and `KILL` one second later if needed. Temporary captured output stays bounded
to configured cap plus one byte; remaining output is drained without storage.
Tool output returns to Brain as untrusted model context.

## Prompt and knowledge

Base instructions live in `server/config/system-prompt.txt`.
Prompt requires user-facing final answer after tool calls, even when command
output already answers request.

Runner-backed conversations append selected hostname and server IP to model
system context. Changing runner updates that context before next turn.

Place trusted UTF-8 `.md` or `.txt` files under `server/knowledge/`. Brain loads
them alphabetically at startup and rejects total size above
`MAX_KNOWLEDGE_BYTES`.

After prompt or knowledge changes:

```bash
docker compose restart brain
```

Existing sessions retain old system context stored at creation. Use `/new` on
clients requiring updated context.

Client implementation is mounted read-only into Brain container. Changes to
`client/main.sh` are served on next `ai-helper` invocation without copying files
to client servers.

## Brain configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_ENDPOINT_URL` | `http://192.168.1.111:9292/v1/chat/completions` | Upstream model endpoint |
| `LLM_API_KEY` | empty | Optional upstream bearer token |
| `MODEL_NAME` | required | Upstream model name |
| `SUPPORT_MODEL_NAME` | empty (disabled) | Model for short conversation titles, using the same endpoint and API key |
| `MODEL_CONTEXT_TOKENS` | `32768` | Fallback context size; Web UI checks `/running`, reads loaded llama.cpp runtime through `/props`, then falls back to `/v1/models` metadata |
| `BRAIN_BIND_HOST` | `0.0.0.0` | Brain listen address |
| `BRAIN_PORT` | `8080` | Brain listen port |
| `WEB_PORT` | `8081` | Web dashboard and conversation API port |
| `BRAIN_URL` | required | Stable Brain URL injected into clients and runner installers |
| `DATABASE_PATH` | `/data/brain.sqlite3` | Session database |
| `SYSTEM_PROMPT_PATH` | `/config/system-prompt.txt` | Base prompt |
| `KNOWLEDGE_DIR` | `/knowledge` | Knowledge files |
| `CLIENT_SCRIPT_PATH` | `/client/main.sh` | Bash client served by Brain |
| `RUNNER_SCRIPT_PATH` | `/client/runner.sh` | Bash one-shot runner served by Brain |
| `RUNNER_INSTALLER_PATH` | `/client/install-runner.sh` | Rendered systemd installer |
| `WEB_DIR` | `/web` | Read-only web interface files |
| `MAX_TOOL_ROUNDS` | `8` | Tool-call rounds per user turn |
| `MAX_KNOWLEDGE_BYTES` | `65536` | Total knowledge byte limit |
| `MAX_REQUEST_BYTES` | `5242880` | Client request byte limit |
| `LLM_TIMEOUT_SECONDS` | `300` | Upstream request timeout |
| `CLIENT_COMMAND_TIMEOUT_SECONDS` | `30` | Rendered local command timeout |
| `CLIENT_MAX_TOOL_OUTPUT_BYTES` | `65536` | Rendered local output cap |
| `CLIENT_BRAIN_CONNECT_TIMEOUT_SECONDS` | `10` | Rendered Brain connection timeout |
| `CLIENT_BRAIN_REQUEST_TIMEOUT_SECONDS` | `30` | Total timeout for non-streaming client API requests |
| `RUNNER_PORT_START` | `8766` | First automatic runner port |
| `RUNNER_PORT_END` | `8865` | Last automatic runner port |
| `RUNNER_PROBE_SECONDS` | `60` | Runner health interval |
| `RUNNER_ONLINE_SECONDS` | `90` | Online status lifetime |
| `RUNNER_TRUSTED_SOURCE_IP` | Brain URL host | Source IP accepted by runner |

## HTTP API

Brain exposes:

- `GET /healthz`
- `GET /livez`
- `GET /readyz`
- `GET /client.sh`
- `GET /runner.sh`
- `GET /runner/install/{single_use_token}`
- `GET /v1/conversations`
- `POST /v1/conversations` with `{"runner_id":null}` (web dashboard)
- `GET /v1/conversations/{id}`
- `DELETE /v1/conversations/{id}` (web dashboard)
- `POST /v1/conversations/{id}/archive` with `{"archived":true}` (web dashboard)
- `POST /v1/conversations/{id}/turns` with `{"content":"Continue"}` (web dashboard)
- `POST /v1/conversations/{id}/turns` with `{"content":"Edited","branch_from":0}`
- `POST /v1/conversations/{id}/branches/{branch_id}` with `{}` (web dashboard)
- `POST /v1/conversations/{id}/runner` with `{"runner_id":null}`
- `POST /v1/conversations/{id}/commands/{tool_call_id}` with approval decision
- `GET /v1/conversations/{id}/events` (web SSE: snapshot, reasoning/content deltas, deleted)
- `GET /v1/servers` (web port)
- `GET /v1/runners` (web port)
- `POST /v1/runners/{id}/check` with `{}` (web port)
- `POST /v1/runner-enrollments` with `{"client_id":"..."}` (web port)
- `POST /v1/runner-enrollments/{single_use_token}` (installer)
- `POST /v1/servers/{ip}/name` with `{"name":"Build server"}` (web port;
  empty name clears it)
- `POST /v1/sessions`
- `GET /v1/sessions/{id}`
- `DELETE /v1/sessions/{id}`
- `POST /v1/sessions/{id}/turns`
- `POST /v1/clients` with `client_id` and `name`
- `GET /v1/clients/{id}` (client port)
- `POST /v1/sessions/{id}/client` with `client_id` and optional `cwd` (one client per session)
- `POST /v1/commands/check` with `{"argv":["docker","ps"]}` (client port)
- `POST /v1/trust` with `{"prefix":["docker","ps"]}` (client port)
- `POST /v1/servers/{ip}/trust` with `{"action":"add","command":"docker ps"}`
  or `{"action":"remove","prefix":["docker","ps"]}` (web port)

Dashboard changes send `Content-Type: application/json` when carrying JSON,
`X-Brain-UI: 1`, and a matching `Origin`. Browser writes to the client port are
rejected. Trust edits apply one addition/removal atomically, preserving concurrent
edits.

User turn:

```json
{"type":"user","content":"show disk usage"}
```

Tool results:

```json
{
  "type": "tool_results",
  "results": [
    {
      "tool_call_id": "call_1",
      "content": "exit_code=0\n...",
      "approval": {"decision": "trusted", "prefix": ["docker", "ps"]}
    }
  ]
}
```

Optional `instruction` carries replacement user instruction after all pending
calls receive results or cancellation results.
Every result includes an `approval`: `decision` is `trusted`, `trusted_now`,
`allowed_once`, `denied`, `cancelled`, or `invalid`. `prefix` contains the exact
trusted argv prefix for the two trusted decisions and is empty otherwise.

Turn response is normalized SSE with `reasoning`, `content`, and one terminal
event: `tool_calls`, `done`, or `error`. Brain accepts upstream `data:` SSE only
and requires `[DONE]`.
Upstream reasoning uses `reasoning_content`; the legacy `reasoning` alias is
not supported.

Session state may be `ready`, `awaiting_tool_results`, or
`continuation_pending`. Last state means command results are safely stored and
model continuation needs explicit retry using empty `tool_results`.

## Security

V1 has no Brain authentication and no TLS. Expose Brain only on trusted,
firewalled network. Any host reaching port can read all stored conversations,
create sessions, consume LLM resources, download executable client code, and
change any server-IP trusted prefixes through dashboard. Client IDs identify
conversation owners; they are not authentication credentials.

`ai-helper` executes code downloaded over raw HTTP. Any LAN actor able to alter
Brain response can execute arbitrary commands as client user before command
approval policy starts. Private addressing provides no integrity protection.
Same warning applies to runner installer and Brain-to-runner bearer token.

Client protections still apply:

- Commands use exact argv entries. Shell syntax requires explicit `bash -lc`.
- Trusted commands use argv-token prefixes, never raw string prefixes.
- Broad prefixes such as `bash -lc`, `sudo`, or `rm` are dangerous.
- Session files reject symlinks and require current ownership.
- Commands execute only on client after Brain policy check or local approval.

OS permissions remain primary boundary. Run clients as unprivileged users.

Container runs non-root with read-only root filesystem, all Linux capabilities
dropped, `no-new-privileges`, and rotated JSON logs. `/data` remains writable
for SQLite.

## Tests

Retained checks cover command safety, stream validation, durable session state,
HTTP boundaries, and the downloaded-client flow against a mock LLM.

Browser checks use a temporary SQLite database, simulated model responses, and
simulated runner execution. They exercise themes, mobile layouts, drafts,
Markdown filtering, approvals, metadata, and server management. Install
Playwright as a development tool outside the runtime, then run:

```bash
npm install --prefix /tmp/brain-web-tools playwright@1.63.0
PLAYWRIGHT_MODULE=/tmp/brain-web-tools/node_modules/playwright \
CHROME_BIN=/path/to/chrome node tests/test_web.cjs
```

Chrome needs its standard OS libraries, including `libnspr4`, `libnss3`, and
`libasound2t64` on Debian 13. Screenshots and fixture logs default to
`/tmp/brain-web-artifacts`; override with `WEB_TEST_ARTIFACTS`.

```bash
python3 -m unittest tests.test_brain
bash tests/test_client.sh
bash tests/test_installer.sh
bash tests/test_stream.sh
bash tests/test_runner.sh
bash tests/test_e2e.sh
```

```bash
bash -n client/main.sh
python3 -m py_compile server/brain.py
python3 -m unittest discover -s tests -v
./tests/test_client.sh
bash tests/test_stream.sh
./tests/test_e2e.sh
git diff --check
docker compose config --quiet
docker compose build
```
