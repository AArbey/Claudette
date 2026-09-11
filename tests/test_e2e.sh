#!/usr/bin/env bash

set -Eeuo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d)
mock_port=19292
brain_port=18080
web_port=18081
mock_pid=''
brain_pid=''

cleanup() {
    [[ -n "$brain_pid" ]] && kill "$brain_pid" 2>/dev/null || true
    [[ -n "$mock_pid" ]] && kill "$mock_pid" 2>/dev/null || true
    wait "$brain_pid" 2>/dev/null || true
    wait "$mock_pid" 2>/dev/null || true
    rm -rf -- "$test_dir"
}
trap cleanup EXIT

python3 "$repo_dir/tests/mock_llm.py" "$mock_port" >"$test_dir/mock.log" 2>&1 &
mock_pid=$!

start_brain() {
    LLM_ENDPOINT_URL="http://127.0.0.1:${mock_port}/v1/chat/completions" \
    MODEL_NAME=mock \
    BRAIN_BIND_HOST=127.0.0.1 \
    BRAIN_PORT="$brain_port" \
    WEB_PORT="$web_port" \
    DATABASE_PATH="$test_dir/brain.sqlite3" \
    SYSTEM_PROMPT_PATH="$repo_dir/server/config/system-prompt.txt" \
    KNOWLEDGE_DIR="$repo_dir/server/knowledge" \
    CLIENT_SCRIPT_PATH="$repo_dir/client/main.sh" \
    WEB_DIR="$repo_dir/server/web" \
        python3 "$repo_dir/server/brain.py" >"$test_dir/brain.log" 2>&1 &
    brain_pid=$!

    local attempt
    for (( attempt = 0; attempt < 50; attempt++ )); do
        if curl --silent --fail "http://127.0.0.1:${brain_port}/healthz" >/dev/null 2>&1; then
            return
        fi
        sleep 0.1
    done
    printf 'Brain failed to start\n' >&2
    sed -n '1,120p' "$test_dir/brain.log" >&2
    return 1
}

prepare_home() {
    mkdir -p "$1"
    printf '[["printf"]]\n' >"$1/.bash-helper-trusted-commands.json"
    chmod 600 "$1/.bash-helper-trusted-commands.json"
}

download_client() {
    curl --fail --silent --show-error \
        "http://127.0.0.1:${brain_port}/client.sh"
}

server_one_home="$test_dir/server-one"
server_two_home="$test_dir/server-two"
prepare_home "$server_one_home"
prepare_home "$server_two_home"

start_brain
[[ $(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${brain_port}/") == 404 ]]
[[ $(curl --silent --output /dev/null --write-out '%{http_code}' \
    "http://127.0.0.1:${web_port}/client.sh") == 404 ]]
[[ $(curl --silent --output /dev/null --write-out '%{http_code}' \
    --request POST --data '{}' "http://127.0.0.1:${web_port}/v1/sessions") == 405 ]]
client_script=$(download_client)
printf 'run tool\nt\nexit\n' | HOME="$server_one_home" \
    bash -c "$client_script" >"$test_dir/client-first.log"
grep -F 'Tool: printf mock-tool' "$test_dir/client-first.log" >/dev/null
grep -F 'Assistant: tool result received' "$test_dir/client-first.log" >/dev/null
first_session=$(<"$server_one_home/.local/state/ai-helper/session-id")
curl --fail --silent "http://127.0.0.1:${web_port}/" |
    grep -F '<title>Brain workspace</title>' >/dev/null
curl --fail --silent "http://127.0.0.1:${web_port}/v1/conversations" |
    jq -e --arg id "$first_session" \
        '.conversations | any(.session_id == $id)' >/dev/null
curl --fail --silent \
    "http://127.0.0.1:${web_port}/v1/conversations/${first_session}" |
    jq -e '(.client.server_ip == "127.0.0.1") and
        (.messages | any(.ui.reasoning == "Checking the request.")) and
        (.messages | any(.ui.approval == {decision: "trusted_now", prefix: ["printf"]}))' >/dev/null

kill "$brain_pid"
wait "$brain_pid" || true
brain_pid=''
start_brain
client_script=$(download_client)
printf '\nhello\nexit\n' | HOME="$server_one_home" \
    bash -c "$client_script" >"$test_dir/client-resume.log"
grep -F 'Resumed session:' "$test_dir/client-resume.log" >/dev/null
grep -F 'Assistant: mock reply' "$test_dir/client-resume.log" >/dev/null
curl --fail --silent \
    "http://127.0.0.1:${web_port}/v1/conversations/${first_session}" |
    jq -e '.messages | any(.ui.reasoning == "Checking the request.")' >/dev/null

printf 'hello\nexit\n' | HOME="$server_two_home" \
    bash -c "$client_script" >"$test_dir/client-second-server.log"
second_session=$(<"$server_two_home/.local/state/ai-helper/session-id")
[[ "$first_session" != "$second_session" ]]
grep -F 'New session:' "$test_dir/client-second-server.log" >/dev/null

printf '\n/new\nexit\n' | HOME="$server_one_home" \
    bash -c "$client_script" >"$test_dir/client-new.log"
replacement_session=$(<"$server_one_home/.local/state/ai-helper/session-id")
[[ "$replacement_session" != "$first_session" ]]
curl --fail --silent \
    "http://127.0.0.1:${brain_port}/v1/sessions/${first_session}" >/dev/null
curl --fail --silent "http://127.0.0.1:${web_port}/v1/conversations" |
    jq -e --arg old "$first_session" --arg new "$replacement_session" \
        '.conversations as $items |
         ($items | any(.session_id == $old)) and
         ($items | any(.session_id == $new))' \
        >/dev/null

first_client=$(<"$server_one_home/.local/state/ai-helper/client-id")
second_client=$(<"$server_two_home/.local/state/ai-helper/client-id")
[[ "$first_client" != "$second_client" ]]

# Removing trust in dashboard applies before next command. Legacy cache stays untouched.
curl --fail --silent --request POST \
    --header 'Content-Type: application/json' --header 'X-Brain-UI: 1' \
    --header "Origin: http://127.0.0.1:${web_port}" \
    --data '{"action":"remove","prefix":["printf"]}' \
    "http://127.0.0.1:${web_port}/v1/servers/127.0.0.1/trust" |
    jq -e '.trusted_prefixes == []' >/dev/null
[[ $(jq -c . "$server_one_home/.bash-helper-trusted-commands.json") == '[["printf"]]' ]]
printf '\nrun tool\nn\nexit\n' | HOME="$server_one_home" \
    bash -c "$client_script" >"$test_dir/client-revoked.log"
[[ $(jq -c . "$server_one_home/.bash-helper-trusted-commands.json") == '[["printf"]]' ]]
if grep -F 'Tool: printf' "$test_dir/client-revoked.log" >/dev/null; then
    printf 'Revoked command ran automatically\n' >&2
    exit 1
fi
curl --fail --silent "http://127.0.0.1:${web_port}/v1/servers" |
    jq -e '.servers[0].trusted_prefixes == []' >/dev/null

# Dashboard addition is shared by every client on source IP.
curl --fail --silent --request POST \
    --header 'Content-Type: application/json' --header 'X-Brain-UI: 1' \
    --header "Origin: http://127.0.0.1:${web_port}" \
    --data '{"action":"add","command":"printf mock-tool"}' \
    "http://127.0.0.1:${web_port}/v1/servers/127.0.0.1/trust" |
    jq -e '.trusted_prefixes == [["printf", "mock-tool"]]' >/dev/null
curl --fail --silent "http://127.0.0.1:${web_port}/v1/servers" |
    jq -e '.servers[0].client_names | length > 0' >/dev/null
printf '\nrun tool\nexit\n' | HOME="$server_one_home" \
    bash -c "$client_script" >"$test_dir/client-added.log"
grep -F 'trusted prefix: printf mock-tool' "$test_dir/client-added.log" >/dev/null

printf 'end-to-end smoke: ok\n'
