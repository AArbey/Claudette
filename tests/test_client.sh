#!/usr/bin/env bash

set -Eeuo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT

# shellcheck source=../client/main.sh
source "$repo_dir/client/main.sh"

COMMAND_TIMEOUT_SECONDS=1
MAX_TOOL_OUTPUT_BYTES=4
CLIENT_ID='cccccccccccccccccccccccccccccccc'
remote_prefixes='[["printf"],["env"],["cd"]]'
remote_available=true
malformed_response=false

# Readline submit captures every pasted line before read stops at first newline.
original_input=$'first line\nsecond line\nthird line'
READLINE_LINE=$original_input
capture_readline_input
[[ "$READLINE_CAPTURED_INPUT" == "$original_input" ]]
[[ "$READLINE_DID_CAPTURE" == true ]]
[[ "$READLINE_LINE" == 'first line\nsecond line\nthird line' ]]
read_conversation_input '' assigned_input <<< 'single line'
[[ "$assigned_input" == 'single line' ]]

brain_request() {
    [[ "$remote_available" == true ]] || return 1
    BRAIN_HTTP_STATUS=200
    if [[ "$2" == /v1/trust ]]; then
        remote_prefixes=$(jq -cn --argjson current "$remote_prefixes" --argjson edit "$3" \
            '$current + [$edit.prefix] | unique')
        BRAIN_RESPONSE=$(jq -cn --argjson prefixes "$remote_prefixes" \
            '{server_ip: "192.0.2.10", trusted_prefixes: $prefixes}')
    elif [[ "$2" == /v1/commands/check ]]; then
        if [[ "$malformed_response" == true ]]; then
            BRAIN_RESPONSE='{"allowed":true,"prefix":["wrong"]}'
            return
        fi
        local command match
        command=$(jq -c '.argv' <<<"$3")
        match=$(jq -cn --argjson prefixes "$remote_prefixes" --argjson command "$command" '
            [$prefixes[] | select(length <= ($command | length) and $command[0:length] == .)] |
            max_by(length) // []')
        BRAIN_RESPONSE=$(jq -cn --argjson prefix "$match" \
            '{allowed: ($prefix | length > 0), prefix: $prefix, server_ip: "192.0.2.10"}')
    fi
}

valid='{"program":"printf","arguments":["ok"],"reason":"test","trust_prefix":["printf"]}'
invalid='{"program":"printf","arguments":[],"reason":"test","trust_prefix":["echo"]}'
command_request_is_valid "$valid"
if command_request_is_valid "$invalid"; then
    printf 'invalid command accepted\n' >&2
    exit 1
fi

matched=$(check_trusted_command '["printf","%s","ok"]')
[[ "$matched" == '["printf"]' ]]

capture_command printf 123456
[[ "$TOOL_RESULT" == $'exit_code=0\n1234\n[output truncated]' ]]
capture_command bash -c 'i=0; while (( i < 10000 )); do printf 1234567890; i=$((i + 1)); done'
[[ "$TOOL_RESULT" == $'exit_code=0\n1234\n[output truncated]' ]]

started=$SECONDS
capture_command sh -c 'trap "" TERM; while :; do sleep 0.05; done'
(( SECONDS - started < 5 ))
[[ "$TOOL_RESULT" == exit_code=137* ]]

# Stub registration and worker. Worker process coverage lives in Python tests.
start_terminal_command_job() {
    local _call_id="$1" command="$2" _approval="$3" program
    local -a arguments=()
    program=$(jq -r '.program' <<<"$command")
    mapfile -t arguments < <(jq -r '.arguments[]' <<<"$command")
    TOOL_JOB_ID=jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj
    if [[ "$program" == cd ]]; then
        TOOL_JOB_CWD="${arguments[0]}"
        printf -v TOOL_RESULT 'exit_code=0\n%s' "$TOOL_JOB_CWD"
    else
        capture_command env -i "PATH=$PATH" "HOME=$HOME" "$program" "${arguments[@]}"
    fi
}

SECRET_MUST_NOT_LEAK=hidden
export SECRET_MUST_NOT_LEAK
execute_approved_command \
    '{"program":"env","arguments":[],"reason":"test","trust_prefix":["env"]}'
[[ "$TOOL_RESULT" != *SECRET_MUST_NOT_LEAK* ]]
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == trusted ]]
[[ $(jq -c '.prefix' <<<"$TOOL_APPROVAL") == '["env"]' ]]

execute_approved_command \
    '{"program":"printf","arguments":[""],"reason":"empty argument","trust_prefix":["printf"]}'
[[ "$TOOL_RESULT" == $'exit_code=0\n' ]]

old_dir=$PWD
execute_approved_command \
    "$(jq -cn --arg path "$test_dir" '{program:"cd",arguments:[$path],reason:"test",trust_prefix:["cd"]}')"
[[ "$PWD" == "$test_dir" ]]
cd "$old_dir"

request_command_permission <<< 'y'
[[ "$COMMAND_PERMISSION_ACTION" == allow ]]
request_command_permission <<< 't'
[[ "$COMMAND_PERMISSION_ACTION" == trust ]]
request_command_permission <<< 'n'
[[ "$COMMAND_PERMISSION_ACTION" == deny ]]
request_command_permission <<< $'i\ninspect only'
[[ "$COMMAND_PERMISSION_ACTION" == instruct ]]
[[ "$PENDING_USER_INSTRUCTION" == 'inspect only' ]]

# Approval metadata must describe the actual decision and saved prefix.
PENDING_USER_INSTRUCTION=''
remote_prefixes='[]'
execute_approved_command "$valid" <<< 'y'
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == allowed_once ]]
execute_approved_command "$valid" <<< 'n'
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == denied ]]
[[ "$TOOL_RESULT" == 'Permission denied'* ]]
execute_approved_command "$valid" <<< 't'
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == trusted_now ]]
execute_approved_command "$valid"
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == trusted ]]

# Brain revocation applies before next execution.
remote_prefixes='[]'
execute_approved_command "$valid" <<< 'n'
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == denied ]]
remote_available=false
execute_approved_command "$valid" <<< 'y'
[[ "$TOOL_RESULT" == *'Command not run.' ]]
remote_available=true
malformed_response=true
execute_approved_command "$valid" <<< 'y'
[[ "$TOOL_RESULT" == *'Command not run.' ]]
malformed_response=false

# Resume uses tracked job snapshot before policy check or command start.
saved_wait_function=$(declare -f wait_terminal_command_job)
wait_terminal_command_job() {
    TOOL_JOB_ID="$1"
    TOOL_JOB_CWD="$PWD"
    TOOL_RESULT=$'exit_code=0\nresumed'
    TOOL_APPROVAL='{"decision":"allowed_once","prefix":[]}'
}
remote_available=false
execute_approved_command "$valid" resume_call jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj
[[ "$TOOL_RESULT" == $'exit_code=0\nresumed' ]]
[[ "$TOOL_JOB_ID" == jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj ]]
remote_available=true
eval "$saved_wait_function"

# Internal reset clears terminal stream state before refreshed answer.
BRAIN_URL=http://brain.test
BRAIN_CONNECT_TIMEOUT_SECONDS=1
SESSION_ID=ssssssssssssssssssssssssssssssss
SESSION_STATUS=continuation_pending
curl() {
    printf 'event: content\ndata: {"delta":"stale"}\n\nevent: reset\ndata: {}\n\nevent: content\ndata: {"delta":"fresh"}\n\nevent: done\ndata: {}\n\n__BRAIN_HTTP_STATUS__:200\n'
}
stream_turn '{}' >"$test_dir/reset-output"
unset -f curl
[[ "$SESSION_STATUS" == ready ]]
grep -q 'Command finished. Refreshing answer.' "$test_dir/reset-output"
grep -q 'Assistant: fresh' "$test_dir/reset-output"

# Reasoning-only response offers recovery and submits exact continuation message.
stream_attempt=0
recovery_payload=''
stream_turn() {
    stream_attempt=$((stream_attempt + 1))
    if (( stream_attempt == 1 )); then
        LAST_BRAIN_ERROR='LLM returned neither content nor tool calls'
        SESSION_STATUS='continuation_pending'
        return 1
    fi
    recovery_payload="$1"
    SESSION_STATUS='ready'
}
stream_turn_with_recovery '{}' <<< 'yes'
[[ "$stream_attempt" == 2 ]]
jq -e '. == {type: "recovery", content: "Your session was interrupted, continue"}' \
    <<<"$recovery_payload" >/dev/null

# Redirect cancels every pending call and sends the new instruction once.
stream_turn() { printf '%s' "$1" >"$test_dir/submitted.json"; }
calls=$(jq -cn --arg arguments "$valid" \
    '["one", "two"] | map({id: ., function: {name: "run_command", arguments: $arguments}})')
submit_tool_results "$calls" <<< $'i\ninspect only'
jq -e '.instruction == "inspect only" and (.results | length == 2) and
    all(.results[]; .approval == {decision: "cancelled", prefix: []})' \
    "$test_dir/submitted.json" >/dev/null

# Local terminal edit works without installed runner and sends Web diff metadata.
FILE_TOOL_STATE_DIR="$test_dir/file-edits"
BRAIN_URL=http://brain.test
BRAIN_CONNECT_TIMEOUT_SECONDS=1
BRAIN_REQUEST_TIMEOUT_SECONDS=1
SESSION_ID=ssssssssssssssssssssssssssssssss
curl() {
    local output=''
    while (( $# )); do
        if [[ "$1" == -o ]]; then output="$2"; shift 2; else shift; fi
    done
    cp -- "$repo_dir/client/file_tool.py" "$output"
}
local_path="$test_dir/local.txt"
local_args=$(jq -cn --arg path "$local_path" '{path:$path,operation:"create",old_text:"",new_text:"created\n",reason:"test"}')
execute_file_edit "$local_args" local_call
[[ $(<"$local_path") == created ]]
[[ $(jq -r '.operation' <<<"$TOOL_FILE_EDIT") == create ]]
[[ $(jq -r '.decision' <<<"$TOOL_APPROVAL") == automatic ]]

# Large edit arguments and diff must not exceed shell argv limits during submission.
large_args=$(jq -cn --arg path "$test_dir/large.txt" \
    --rawfile content <(python3 -c 'print("x\n" * 45000, end="")') \
    '{path:$path,operation:"create",old_text:"",new_text:$content,reason:"large diff"}')
large_call=$(jq -cn --slurpfile args <(printf '%s' "$large_args") \
    '{id:"large_call",function:{name:"edit_file",arguments:($args[0] | tojson)}}')
submit_tool_results "[$large_call]"
jq -e '.results[0].file_edit.added == 45000 and
    (.results[0].file_edit.diff | length > 128000) and
    .results[0].approval.decision == "automatic"' "$test_dir/submitted.json" >/dev/null
unset -f curl

printf 'client tests: ok\n'
