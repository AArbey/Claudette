#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo_dir/client/main.sh"
SESSION_ID=mock
CLIENT_ID=local
BRAIN_URL=http://mock
BRAIN_CONNECT_TIMEOUT_SECONDS=1

remote='{"id":"remote/1","type":"function","function":{"name":"run_command","arguments":"{\"command\":\"printf ok\",\"reason\":\"Inspect\",\"runner_id\":\"beta\"}"},"ui":{"remote":true,"state":"approval","target":{"executor":"runner","runner_id":"beta","client_name":"ops@beta","server_ip":"192.0.2.22"}}}'
TOOL_RESULT=''
execute_tool_call "$remote" >/dev/null
[[ "$TOOL_RESULT" == *'never locally'* ]]
TOOL_RESULT=''
execute_tool_call "$(jq 'del(.ui)' <<<"$remote")" >/dev/null
[[ "$TOOL_RESULT" == *'cannot execute locally'* ]]

request_path=''
request_body=''
refresh_count=0
stream_turn() { request_body="$1"; request_path="${2-}"; }
refresh_session_state() { refresh_count=$((refresh_count + 1)); }
for choice in a t d; do
    resolve_cli_remote_command "$remote" <<<"$choice" >/dev/null
    [[ "$request_path" == '/v1/sessions/mock/commands/remote%2F1' ]]
    [[ "$(jq -r '.client_id' <<<"$request_body")" == local ]]
    case "$choice" in a) expected=allow_once ;; t) expected=trust ;; d) expected=deny ;; esac
    [[ "$(jq -r '.decision' <<<"$request_body")" == "$expected" ]]
done
failed=$(jq '.ui.state="failed" | .ui.error="Unavailable"' <<<"$remote")
for choice in r c; do
    resolve_cli_remote_command "$failed" <<<"$choice" >/dev/null 2>&1
    case "$choice" in r) expected=retry ;; c) expected=cancel ;; esac
    [[ "$(jq -r '.decision' <<<"$request_body")" == "$expected" ]]
done
[[ "$refresh_count" == 5 ]]

# Local results submitted separately; remote approvals remain on Brain.
local_call='{"id":"local","type":"function","function":{"name":"run_command","arguments":"{}"}}'
executed=''
execute_tool_call() {
    executed=$(jq -r '.id' <<<"$1")
    TOOL_RESULT=ok
    TOOL_APPROVAL='{"decision":"allowed_once","prefix":[]}'
}
stream_turn_with_recovery() { request_body="$1"; }
calls=$(jq -cn --argjson remote "$remote" --argjson local "$local_call" '[$remote,$local]')
submit_tool_results "$calls"
[[ "$executed" == local ]]
[[ "$(jq -r '.results | length' <<<"$request_body")" == 1 ]]
[[ "$(jq -r '.results[0].tool_call_id' <<<"$request_body")" == local ]]
submit_tool_results "$(jq -cn --argjson remote "$remote" '[$remote]')" <<<a >/dev/null
[[ "$(jq -r '.decision' <<<"$request_body")" == allow_once ]]

# New remote-approval instructions retain multiline text.
read_conversation_input() { printf -v "$2" 'line one\nline two'; }
resolve_cli_remote_command "$remote" <<<i >/dev/null
[[ "$(jq -r '.instruction' <<<"$request_body")" == $'line one\nline two' ]]
[[ "$(jq -r '.results | length' <<<"$request_body")" == 0 ]]
printf 'runner routing CLI tests: ok\n'
