#!/usr/bin/env bash

set -Eeuo pipefail

TOOL_RESULT=''
TOOL_FILE_EDIT=''
TOOL_JOB_ID=''
TOOL_JOB_CWD=''
COMMAND_WORKER_FILE=''
COMMAND_JOB_STATE_DIR=''
COMMAND_WORKER_PIDS=()
TOOL_APPROVAL='{"decision":"invalid","prefix":[]}'
COMMAND_PERMISSION_ACTION=''
PENDING_USER_INSTRUCTION=''
LAST_BRAIN_ERROR=''
BRAIN_RESPONSE=''
BRAIN_HTTP_STATUS=''
SESSION_ID=''
CLIENT_ID=''
SESSION_STATUS='ready'
PENDING_TOOL_CALLS='[]'
READLINE_CAPTURED_INPUT=''
READLINE_DID_CAPTURE=false

COLOR_THINKING=''
COLOR_ASSISTANT=''
COLOR_USER=''
COLOR_STATUS=''
COLOR_ERROR=''
COLOR_COMMAND=''
COLOR_RESET=''

if [[ -z "${NO_COLOR:-}" ]]; then
    if [[ -t 1 ]]; then
        COLOR_THINKING=$'\033[35m'
        COLOR_ASSISTANT=$'\033[32m'
        COLOR_USER=$'\033[34m'
        COLOR_STATUS=$'\033[32m'
        COLOR_COMMAND=$'\033[31m'
        COLOR_RESET=$'\033[0m'
    fi
    [[ -t 2 ]] && COLOR_ERROR=$'\033[31m'
fi

print_status() { printf '%s%s%s\n' "$COLOR_STATUS" "$1" "$COLOR_RESET"; }
print_error() { printf '%sError: %s%s\n' "$COLOR_ERROR" "$1" "$COLOR_RESET" >&2; }

capture_readline_input() {
    READLINE_CAPTURED_INPUT=$READLINE_LINE
    READLINE_DID_CAPTURE=true
    READLINE_LINE=${READLINE_LINE//$'\n'/\\n}
    READLINE_POINT=${#READLINE_LINE}
}

configure_readline() {
    [[ -t 0 ]] || return 0
    set -o emacs
    bind -x '"\e[99~": capture_readline_input'
    bind '"\e[98~": accept-line'
    bind '"\C-M": "\e[99~\e[98~"'
    bind '"\C-J": "\e[99~\e[98~"'
}

read_conversation_input() {
    local prompt="$1" output_name="$2" raw_input
    READLINE_CAPTURED_INPUT=''
    READLINE_DID_CAPTURE=false
    IFS= read -er -p "$prompt" raw_input || return
    if [[ "$READLINE_DID_CAPTURE" == true ]]; then
        raw_input=$READLINE_CAPTURED_INPUT
    fi
    printf -v "$output_name" '%s' "$raw_input"
}

require_commands() {
    local name
    local -a packages=()
    for name in "$@"; do
        if ! command -v "$name" >/dev/null 2>&1; then
            case "$name" in
                timeout|sha256sum) packages+=(coreutils) ;;
                *) packages+=("$name") ;;
            esac
        fi
    done
    if (( ${#packages[@]} > 0 )); then
        print_status "Installing missing dependencies: ${packages[*]}"
        if (( EUID == 0 )); then
            apt install -y "${packages[@]}"
        else
            sudo apt install -y "${packages[@]}"
        fi
    fi
}

format_argv_json() {
    local argv_json="$1" formatted='' token
    local -a tokens=()
    mapfile -t tokens < <(jq -r '.[]' <<<"$argv_json")
    for token in "${tokens[@]}"; do
        printf -v token '%q' "$token"
        formatted+="${formatted:+ }$token"
    done
    printf '%s' "$formatted"
}

check_trusted_command() {
    local command_json="$1" payload
    payload=$(jq -cn --argjson argv "$command_json" '{argv: $argv}')
    if ! brain_request POST /v1/commands/check "$payload" || \
        [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Could not check trusted command with Brain. Command not run."
        return 1
    fi
    jq -ce --argjson command "$command_json" '
        (keys == ["allowed", "prefix", "server_ip"]) and
        (.allowed | type == "boolean") and
        (.server_ip | type == "string" and length > 0) and
        (.prefix | type == "array" and length <= 65 and
            all(.[]; type == "string" and length > 0 and
                (index("\u0000") == null) and
                ((contains("\n") or contains("\r")) | not))) and
        (if .allowed then
            (.prefix | length) > 0 and $command[0:(.prefix | length)] == .prefix
         else .prefix == [] end) |
        if . then input_filename else empty end
    ' <<<"$BRAIN_RESPONSE" >/dev/null || {
        print_error "Brain returned invalid command policy. Command not run."
        return 1
    }
    jq -ce '.prefix' <<<"$BRAIN_RESPONSE"
}

add_trusted_prefix() {
    local payload
    payload=$(jq -cn --argjson prefix "$1" '{prefix: $prefix}')
    if ! brain_request POST /v1/trust "$payload" || [[ "$BRAIN_HTTP_STATUS" != 200 ]] || \
        ! jq -e --argjson prefix "$1" '
            (.server_ip | type == "string" and length > 0) and
            ((.trusted_prefixes | type) == "array") and
            any(.trusted_prefixes[]; . == $prefix)
        ' <<<"$BRAIN_RESPONSE" >/dev/null; then
        print_error "Could not save trusted prefix in Brain."
        return 1
    fi
}

# Local command execution

cleanup_command_workers() {
    local pid
    for pid in "${COMMAND_WORKER_PIDS[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    for pid in "${COMMAND_WORKER_PIDS[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    [[ -z "$COMMAND_WORKER_FILE" ]] || rm -f -- "$COMMAND_WORKER_FILE"
}

ensure_command_worker() {
    [[ -n "$COMMAND_WORKER_FILE" && -f "$COMMAND_WORKER_FILE" ]] && return 0
    mkdir -p -- "$COMMAND_JOB_STATE_DIR"
    chmod 700 "$COMMAND_JOB_STATE_DIR"
    COMMAND_WORKER_FILE=$(mktemp "$COMMAND_JOB_STATE_DIR/worker.XXXXXXXX.py")
    if ! curl --fail --silent --show-error --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
        --max-time "$BRAIN_REQUEST_TIMEOUT_SECONDS" "$BRAIN_URL/command-worker.py" \
        -o "$COMMAND_WORKER_FILE" || \
        [[ "$(head -n 1 "$COMMAND_WORKER_FILE")" != '#!/usr/bin/env python3' ]]; then
        rm -f -- "$COMMAND_WORKER_FILE"
        COMMAND_WORKER_FILE=''
        return 1
    fi
    chmod 600 "$COMMAND_WORKER_FILE"
}

command_job_result() {
    local job="$1" state background
    TOOL_JOB_ID=$(jq -r '.job_id' <<<"$job")
    TOOL_JOB_CWD=$(jq -r '.cwd' <<<"$job")
    TOOL_APPROVAL=$(jq -c '.approval' <<<"$job")
    state=$(jq -r '.state' <<<"$job")
    background=$(jq -r '.background' <<<"$job")
    if [[ "$state" == completed || "$state" == stopped || "$state" == timed_out ]]; then
        TOOL_RESULT=$(jq -r '"exit_code=\(.exit_code)\n\(.output)"' <<<"$job")
        print_status "Command job $TOOL_JOB_ID: $state"
    elif [[ "$state" == outcome_unknown || "$state" == unreachable ]]; then
        TOOL_RESULT=$(jq -r '"Command outcome unknown or worker unreachable; job_id=\(.job_id). Current output:\n\(.output)"' <<<"$job")
        print_status "Command job $TOOL_JOB_ID: $state"
    else
        TOOL_RESULT=$(jq -r '"Command still running; job_id=\(.job_id). Current output snapshot:\n\(.output)"' <<<"$job")
        if [[ "$background" == true ]]; then
            print_status "Command job $TOOL_JOB_ID continues in background."
        fi
    fi
}

wait_terminal_command_job() {
    local job_id="$1" job state background
    while true; do
        if ! brain_request GET "/v1/sessions/$SESSION_ID/command-jobs/$job_id" || \
            [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
            TOOL_RESULT="Command job $job_id status unavailable; command not rerun."
            TOOL_JOB_ID="$job_id"
            return
        fi
        job=$(jq -c '.job' <<<"$BRAIN_RESPONSE") || return 1
        state=$(jq -r '.state' <<<"$job")
        background=$(jq -r '.background' <<<"$job")
        if [[ "$state" == completed || "$state" == stopped || "$state" == timed_out || \
              "$state" == outcome_unknown || "$state" == unreachable || "$background" == true ]]; then
            command_job_result "$job"
            return
        fi
        sleep 0.5
    done
}

start_terminal_command_job() {
    local call_id="$1" command="$2" approval="$3" job_id job_token
    local job_dir request_file registration response worker_pid
    if ! ensure_command_worker; then
        TOOL_RESULT="Tool error: command worker unavailable. Command not run."
        return
    fi
    job_id=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')
    job_token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
    job_dir="$COMMAND_JOB_STATE_DIR/$job_id"
    mkdir -m 700 -- "$job_dir" || {
        TOOL_RESULT="Tool error: could not create command job state."
        return
    }
    request_file="$job_dir/request.json"
    jq -cn --arg job_id "$job_id" --arg job_token "$job_token" \
        --arg brain_url "$BRAIN_URL" --arg cwd "$PWD" \
        --argjson command "$command" \
        --argjson max_output_bytes "$MAX_TOOL_OUTPUT_BYTES" \
        --arg parent_pid "$$" \
        --arg path "$PATH" --arg home "$HOME" --arg user "${USER:-}" \
        --arg logname "${LOGNAME:-}" --arg lang "${LANG:-C.UTF-8}" \
        --arg term "${TERM:-dumb}" \
        '{job_id:$job_id,job_token:$job_token,brain_url:$brain_url,
          cwd:$cwd,command:$command,max_output_bytes:$max_output_bytes,
          max_runtime_seconds:3600,parent_pid:($parent_pid|tonumber),
          environment:{PATH:$path,HOME:$home,PWD:$cwd,USER:$user,
                       LOGNAME:$logname,LANG:$lang,TERM:$term}}' \
        >"$request_file"
    chmod 600 "$request_file"
    registration=$(jq -cn --arg client_id "$CLIENT_ID" --arg tool_call_id "$call_id" \
        --arg job_id "$job_id" --arg job_token "$job_token" --arg cwd "$PWD" \
        --argjson approval "$approval" \
        '{client_id:$client_id,tool_call_id:$tool_call_id,job_id:$job_id,
          job_token:$job_token,cwd:$cwd,approval:$approval}')
    if ! brain_request POST "/v1/sessions/$SESSION_ID/command-jobs" "$registration" || \
        [[ "$BRAIN_HTTP_STATUS" != 202 ]]; then
        TOOL_RESULT="Tool error: could not register command job. Command not run."
        return
    fi
    response=$(jq -c '.job' <<<"$BRAIN_RESPONSE") || {
        TOOL_RESULT="Tool error: invalid command job registration."
        return
    }
    if [[ "$(jq -r '.job_id' <<<"$response")" != "$job_id" ]]; then
        TOOL_RESULT="Tool error: command job registration ID mismatch."
        return
    fi
    jq --argjson lease "$(jq -r '.max_runtime_seconds' <<<"$response")" \
        '.max_runtime_seconds = $lease' "$request_file" >"$job_dir/request.tmp"
    chmod 600 "$job_dir/request.tmp"
    mv -f -- "$job_dir/request.tmp" "$request_file"
    python3 "$COMMAND_WORKER_FILE" run --request "$request_file" \
        --state-dir "$job_dir" --client-mode >/dev/null 2>&1 &
    worker_pid=$!
    COMMAND_WORKER_PIDS+=("$worker_pid")
    TOOL_JOB_ID="$job_id"
    wait_terminal_command_job "$job_id"
}

show_command_jobs() {
    if ! brain_request GET "/v1/sessions/$SESSION_ID/command-jobs" || \
        [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Could not read command jobs."
        return 1
    fi
    jq -r '.jobs[] | "\(.job_id)  \(.state)  \(if .background then "background" else "foreground" end)  \(.command.program)"' \
        <<<"$BRAIN_RESPONSE"
}

show_command_job() {
    local job_id="$1"
    if ! brain_request GET "/v1/sessions/$SESSION_ID/command-jobs/$job_id" || \
        [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Command job not found."
        return 1
    fi
    jq -r '.job | "Job \(.job_id): \(.state)\nDecision: \(.decision_reason)\nOutput:\n\(.output)"' \
        <<<"$BRAIN_RESPONSE"
}

stop_terminal_command_job() {
    local job_id="$1" request_file job_token result_file http_status
    request_file="$COMMAND_JOB_STATE_DIR/$job_id/request.json"
    if [[ ! -f "$request_file" ]]; then
        print_error "Local token for command job $job_id unavailable."
        return 1
    fi
    job_token=$(jq -r '.job_token' "$request_file")
    result_file=$(mktemp)
    http_status=$(curl --silent --show-error --request POST \
        --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
        --max-time "$BRAIN_REQUEST_TIMEOUT_SECONDS" \
        --header "Authorization: Bearer $job_token" \
        --header 'Content-Type: application/json' --data '{}' \
        --output "$result_file" --write-out '%{http_code}' \
        "$BRAIN_URL/v1/sessions/$SESSION_ID/command-jobs/$job_id/stop") || {
            rm -f -- "$result_file"
            print_error "Could not request command stop."
            return 1
        }
    rm -f -- "$result_file"
    if [[ "$http_status" != 202 ]]; then
        print_error "Command stop rejected (HTTP $http_status)."
        return 1
    fi
    print_status "Stop requested for command job $job_id."
}

capture_command() {
    local output_file exit_code=0 byte_count captured_output capture_limit
    local -a pipeline_status=()
    output_file=$(mktemp)
    capture_limit=$((MAX_TOOL_OUTPUT_BYTES + 1))
    set +e
    (
        timeout --signal=TERM --kill-after=1s "${COMMAND_TIMEOUT_SECONDS}s" "$@" 2>&1
        exit $?
    ) 2>/dev/null |
        {
            head -c "$capture_limit" >"$output_file"
            cat >/dev/null
        }
    pipeline_status=("${PIPESTATUS[@]}")
    set -e
    exit_code=${pipeline_status[0]}
    byte_count=$(wc -c <"$output_file")
    captured_output=$(head -c "$MAX_TOOL_OUTPUT_BYTES" -- "$output_file")
    rm -f -- "$output_file"
    if (( byte_count > MAX_TOOL_OUTPUT_BYTES )); then
        captured_output+=$'\n[output truncated]'
    fi
    printf -v TOOL_RESULT 'exit_code=%d\n%s' "$exit_code" "$captured_output"
}

shell_wrapper_is_requested() {
    local -a argv=("$@")
    local index option
    for (( index=0; index<${#argv[@]}-1; index++ )); do
        case "${argv[index]##*/}" in
            bash|sh|dash|zsh|ksh|fish)
                for option in "${argv[@]:index+1}"; do
                    if [[ "$option" =~ ^-[[:alpha:]]*c[[:alpha:]]*$ || "$option" == --command ]]; then
                        return 0
                    fi
                done
                ;;
        esac
    done
    return 1
}

command_request_is_valid() {
    jq -e '
        type == "object" and
        (keys == ["arguments", "program", "reason", "trust_prefix"]) and
        (.program | type == "string" and length >= 1 and
            ((contains("\n") or contains("\r")) | not)) and
        (.reason | type == "string") and
        (.arguments | type == "array" and length <= 64 and
            all(.[]; type == "string" and
                ((contains("\n") or contains("\r")) | not))) and
        (.trust_prefix | type == "array" and length >= 1 and length <= 65 and
            all(.[]; type == "string" and length >= 1 and
                ((contains("\n") or contains("\r")) | not))) and
        ([.program] + .arguments) as $command |
        $command[0:(.trust_prefix | length)] == .trust_prefix
    ' >/dev/null <<<"$1"
}

request_command_permission() {
    local reply
    COMMAND_PERMISSION_ACTION='deny'
    while true; do
        read -r -p "Allow [y] once, [t] run + trust prefix, [i] send new instruction, [N] deny? " reply || return
        case "$reply" in
            [Yy]|[Yy][Ee][Ss]) COMMAND_PERMISSION_ACTION='allow'; return ;;
            [Tt]|[Tt][Rr][Uu][Ss][Tt]) COMMAND_PERMISSION_ACTION='trust'; return ;;
            [Ii])
                read_conversation_input \
                    "New instruction for AI: " PENDING_USER_INSTRUCTION || {
                    PENDING_USER_INSTRUCTION=''
                    return
                }
                if [[ -z "$PENDING_USER_INSTRUCTION" ]]; then
                    print_error "Instruction must not be empty."
                    continue
                fi
                COMMAND_PERMISSION_ACTION='instruct'
                return
                ;;
            *) return ;;
        esac
    done
}

execute_approved_command() {
    local arguments_json="$1" call_id="${2:-}" existing_job_id="${3:-}"
    local program reason printable_reason
    if [[ -n "$existing_job_id" ]]; then
        wait_terminal_command_job "$existing_job_id"
        if [[ "$(jq -r '.program' <<<"$arguments_json")" == cd && "$TOOL_RESULT" == exit_code=0* ]]; then
            cd -- "$TOOL_JOB_CWD"
        fi
        return
    fi
    local printable_command printable_trust_prefix printable_matched_prefix
    local command_json trust_prefix_json matched_prefix_json execution_mode
    local -a arguments=()
    TOOL_APPROVAL='{"decision":"invalid","prefix":[]}'

    if ! command_request_is_valid "$arguments_json"; then
        TOOL_RESULT="Tool error: invalid program, arguments, reason, or trust prefix."
        return
    fi
    program=$(jq -r '.program' <<<"$arguments_json")
    reason=$(jq -r '.reason' <<<"$arguments_json")
    reason="${reason:0:500}"
    printable_reason=$(jq -rn --arg reason "$reason" '$reason | @json')
    trust_prefix_json=$(jq -c '.trust_prefix' <<<"$arguments_json")
    command_json=$(jq -c '[.program] + .arguments' <<<"$arguments_json")
    mapfile -t arguments < <(jq -r '.arguments[]' <<<"$arguments_json")
    if shell_wrapper_is_requested "$program" "${arguments[@]}"; then
        TOOL_RESULT="Tool error: shell -c wrappers are unavailable. Run a direct command."
        return
    fi
    if [[ "$program" == "cd" && ${#arguments[@]} -ne 1 ]]; then
        TOOL_RESULT="Tool error: cd requires exactly one path argument."
        return
    fi
    printable_command=$(format_argv_json "$command_json")
    printable_trust_prefix=$(format_argv_json "$trust_prefix_json")

    if ! matched_prefix_json=$(check_trusted_command "$command_json"); then
        TOOL_RESULT="Tool error: could not check command policy with Brain. Command not run."
        return
    fi
    if [[ "$matched_prefix_json" != '[]' ]]; then
        TOOL_APPROVAL=$(jq -cn --argjson prefix "$matched_prefix_json" \
            '{decision: "trusted", prefix: $prefix}')
        printable_matched_prefix=$(format_argv_json "$matched_prefix_json")
        execution_mode="(trusted prefix: $printable_matched_prefix)"
    else
        printf '\nAI requests command:\n  %s%s%s\nReason: %s\nTrust option: allow future commands from this server IP starting with:\n  %s%s%s\nChoosing [y] runs once; [t] saves this shared IP prefix in Brain.\nOutput will be sent to AI.\n' \
            "$COLOR_COMMAND" "$printable_command" "$COLOR_RESET" "$printable_reason" \
            "$COLOR_COMMAND" "$printable_trust_prefix" "$COLOR_RESET"
        request_command_permission
        case "$COMMAND_PERMISSION_ACTION" in
            allow)
                TOOL_APPROVAL='{"decision":"allowed_once","prefix":[]}'
                execution_mode="(approved once)"
                ;;
            trust)
                if ! add_trusted_prefix "$trust_prefix_json"; then
                    TOOL_RESULT="Tool error: could not save trusted prefix in Brain. Command not run."
                    return
                fi
                printf '%sTrusted prefix saved: %s%s%s%s\n' \
                    "$COLOR_STATUS" "$COLOR_COMMAND" "$printable_trust_prefix" "$COLOR_STATUS" "$COLOR_RESET"
                TOOL_APPROVAL=$(jq -cn --argjson prefix "$trust_prefix_json" \
                    '{decision: "trusted_now", prefix: $prefix}')
                execution_mode="(approved and trusted)"
                ;;
            instruct)
                TOOL_APPROVAL='{"decision":"cancelled","prefix":[]}'
                TOOL_RESULT="Command not run because user provided new instructions."
                return
                ;;
            *)
                TOOL_APPROVAL='{"decision":"denied","prefix":[]}'
                TOOL_RESULT="Permission denied by user. Do not retry unless user explicitly asks."
                return
                ;;
        esac
    fi

    printf '%sTool: %s%s%s %s%s\n' "$COLOR_STATUS" "$COLOR_COMMAND" \
        "$printable_command" "$COLOR_STATUS" "$execution_mode" "$COLOR_RESET"
    start_terminal_command_job "$call_id" "$arguments_json" "$TOOL_APPROVAL"
    if [[ "$program" == cd && "$TOOL_RESULT" == exit_code=0* ]]; then
        cd -- "$TOOL_JOB_CWD"
    fi
}

execute_file_edit() {
    local arguments_json="$1" call_id="$2" edit_id payload helper_file response
    [[ -n "${SESSION_ID:-}" ]] || { TOOL_RESULT="File edit failed: no session"; return; }
    edit_id=$(printf '%s' "$SESSION_ID:$call_id" | sha256sum | awk '{print $1}')
    payload=$(jq -cn --slurpfile args <(printf '%s' "$arguments_json") --arg id "$edit_id" --arg cwd "$PWD" \
        '$args[0] + {action:"apply",request_id:$id,cwd:$cwd}') || {
        TOOL_RESULT="File edit failed: invalid arguments"; return;
    }
    helper_file=$(mktemp)
    if ! curl --fail --silent --show-error --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
        --max-time "$BRAIN_REQUEST_TIMEOUT_SECONDS" "$BRAIN_URL/file-tool.py" -o "$helper_file"; then
        rm -f -- "$helper_file"
        TOOL_RESULT="File edit failed: could not download editor"
        return
    fi
    if [[ "$(head -n 1 "$helper_file")" != '#!/usr/bin/env python3' ]]; then
        rm -f -- "$helper_file"
        TOOL_RESULT="File edit failed: invalid editor download"
        return
    fi
    response=$(python3 "$helper_file" --state-dir "${FILE_TOOL_STATE_DIR:-$HOME/.local/state/ai-helper/file-edits}" \
        <<<"$payload") || response='{"ok":false,"error":"editor failed"}'
    rm -f -- "$helper_file"
    if ! jq -e '.ok == true and (.edit | type == "object")' <<<"$response" >/dev/null 2>&1; then
        TOOL_RESULT="File edit failed: $(jq -r '.error // "invalid editor response"' <<<"$response" 2>/dev/null)"
        return
    fi
    TOOL_FILE_EDIT=$(jq -c '.edit' <<<"$response")
    TOOL_APPROVAL='{"decision":"automatic","prefix":[]}'
    TOOL_RESULT=$(jq -r '.edit | "Edited " + .path + " (+" + (.added|tostring) + " / -" + (.removed|tostring) + "). Diff and restore available in Web UI."' <<<"$response")
    print_status "$TOOL_RESULT"
}

execute_tool_call() {
    local call="$1" name arguments_text arguments_json
    TOOL_APPROVAL='{"decision":"invalid","prefix":[]}'
    TOOL_FILE_EDIT=''
    TOOL_JOB_ID=''
    name=$(jq -r '.function.name // empty' <<<"$call")
    printf '%sAI requested function: %q%s\n' "$COLOR_STATUS" "${name:-unknown}" "$COLOR_RESET"
    arguments_text=$(jq -r '.function.arguments // "{}"' <<<"$call")
    if ! arguments_json=$(jq -ce 'if type == "object" then . else error("not object") end' \
        <<<"$arguments_text" 2>/dev/null); then
        TOOL_RESULT="Tool error: model supplied invalid JSON arguments."
        return
    fi
    case "$name" in
        run_command) execute_approved_command "$arguments_json" "$(jq -r '.id' <<<"$call")" "$(jq -r '.ui.command_job_id // empty' <<<"$call")" ;;
        edit_file) execute_file_edit "$arguments_json" "$(jq -r '.id' <<<"$call")" ;;
        *) printf -v TOOL_RESULT 'Tool error: unknown tool %q.' "$name" ;;
    esac
}

# Brain JSON requests

brain_request() {
    local method="$1" path="$2" payload="${3-}" output_file curl_status=0
    output_file=$(mktemp)
    BRAIN_HTTP_STATUS=''
    if [[ -n "$payload" || "$method" == "POST" ]]; then
        BRAIN_HTTP_STATUS=$(curl --silent --show-error --request "$method" \
            --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
            --max-time "$BRAIN_REQUEST_TIMEOUT_SECONDS" \
            --header 'Content-Type: application/json' --data-binary @- \
            --output "$output_file" --write-out '%{http_code}' \
            "${BRAIN_URL}${path}" <<<"$payload") || curl_status=$?
    else
        BRAIN_HTTP_STATUS=$(curl --silent --show-error --request "$method" \
            --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
            --max-time "$BRAIN_REQUEST_TIMEOUT_SECONDS" \
            --output "$output_file" --write-out '%{http_code}' \
            "${BRAIN_URL}${path}") || curl_status=$?
    fi
    BRAIN_RESPONSE=$(<"$output_file")
    rm -f -- "$output_file"
    if (( curl_status != 0 )); then
        print_error "Brain request failed (curl exit $curl_status)."
        return 1
    fi
}

brain_error_message() {
    jq -r '.error // "unknown error"' <<<"$BRAIN_RESPONSE" 2>/dev/null || printf 'unknown error'
}

check_brain_ready() {
    brain_request GET /healthz
    if [[ "$BRAIN_HTTP_STATUS" != 200 ]] || \
        ! jq -e '.status == "ready"' >/dev/null 2>&1 <<<"$BRAIN_RESPONSE"; then
        print_error "Brain is not ready (HTTP ${BRAIN_HTTP_STATUS:-unknown})."
        return 1
    fi
}

initialize_state_file() {
    local state_file="$1" state_dir owner
    state_dir=$(dirname -- "$state_file")
    (umask 077; mkdir -p -- "$state_dir")
    if [[ -L "$state_file" ]]; then
        print_error "State file must not be a symbolic link: $state_file"
        return 1
    fi
    if [[ -e "$state_file" ]]; then
        if [[ ! -f "$state_file" ]]; then
            print_error "State path must be a regular file: $state_file"
            return 1
        fi
        owner=$(stat -c '%u' -- "$state_file")
        if [[ "$owner" != "$EUID" ]]; then
            print_error "State file must be owned by current user."
            return 1
        fi
        chmod 600 -- "$state_file"
    fi
}

write_state_file() {
    local state_file="$1" value="$2" temporary_file
    temporary_file=$(mktemp "${state_file}.tmp.XXXXXX")
    if printf '%s\n' "$value" >"$temporary_file" && \
        chmod 600 -- "$temporary_file" && mv -- "$temporary_file" "$state_file"; then
        return
    fi
    rm -f -- "$temporary_file"
    print_error "Could not save state: $state_file"
    return 1
}

register_client() {
    local payload client_name
    initialize_state_file "$CLIENT_ID_FILE"
    if [[ ! -e "$CLIENT_ID_FILE" ]]; then
        # noclobber lets simultaneous launches agree on one persistent identity.
        (umask 077; set -o noclobber
            od -An -N16 -tx1 /dev/urandom | tr -d ' \n' >"$CLIENT_ID_FILE") || true
    fi
    CLIENT_ID=$(<"$CLIENT_ID_FILE")
    if [[ ! "$CLIENT_ID" =~ ^[A-Za-z0-9_-]{32}$ ]]; then
        print_error "Invalid client ID file: $CLIENT_ID_FILE"
        return 1
    fi
    client_name="${USER:-$(id -un)}@$(uname -n)"
    payload=$(jq -cn --arg id "$CLIENT_ID" --arg name "$client_name" \
        '{client_id: $id, name: $name}')
    if ! brain_request POST /v1/clients "$payload" || [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Could not register client with Brain."
        return 1
    fi
}

bind_session_client() {
    local payload
    payload=$(jq -cn --arg id "$CLIENT_ID" --arg cwd "$PWD" \
        '{client_id: $id, cwd: $cwd}')
    if ! brain_request POST "/v1/sessions/$SESSION_ID/client" "$payload" || \
        [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Could not bind session to this client: $(brain_error_message)"
        return 1
    fi
}

create_session() {
    brain_request POST /v1/sessions '{}'
    if [[ "$BRAIN_HTTP_STATUS" != 201 ]]; then
        print_error "Could not create Brain session: $(brain_error_message)"
        return 1
    fi
    if ! SESSION_ID=$(jq -er '.session_id | select(type == "string" and length > 0)' \
        <<<"$BRAIN_RESPONSE" 2>/dev/null); then
        print_error "Brain returned invalid session data."
        return 1
    fi
    if [[ ! "$SESSION_ID" =~ ^[A-Za-z0-9_-]{32}$ ]]; then
        print_error "Brain returned invalid session ID."
        return 1
    fi
    PENDING_TOOL_CALLS='[]'
    SESSION_STATUS='ready'
    bind_session_client
    write_state_file "$SESSION_FILE" "$SESSION_ID"
    print_status "New session: $SESSION_ID"
}

load_or_create_session() {
    local reply saved='' owner_client
    if [[ -r "$SESSION_FILE" ]]; then
        IFS= read -r saved <"$SESSION_FILE" || true
    fi
    if [[ "$saved" =~ ^[A-Za-z0-9_-]{32}$ ]]; then
        brain_request GET "/v1/sessions/$saved"
        if [[ "$BRAIN_HTTP_STATUS" == 200 ]] && \
            jq -e '.status == "ready" or .status == "awaiting_tool_results" or .status == "continuation_pending"' \
                >/dev/null 2>&1 <<<"$BRAIN_RESPONSE"; then
            SESSION_ID="$saved"
            SESSION_STATUS=$(jq -r '.status' <<<"$BRAIN_RESPONSE")
            PENDING_TOOL_CALLS=$(jq -c '.pending_tool_calls // []' <<<"$BRAIN_RESPONSE")
            owner_client=$(jq -r '.client_id // empty' <<<"$BRAIN_RESPONSE")
            if [[ -n "$owner_client" && "$owner_client" != "$CLIENT_ID" ]]; then
                print_status "Saved session belongs to another client. Starting a new session."
                create_session
                return
            fi
            read -r -p "Resume previous session [R] / new [n]? " reply || reply=''
            if [[ "$reply" != [Nn] ]]; then
                bind_session_client
                print_status "Resumed session: $SESSION_ID"
                return
            fi
            SESSION_ID=''
        fi
    fi
    create_session
}

new_session() {
    SESSION_ID=''
    create_session
}

refresh_session_state() {
    brain_request GET "/v1/sessions/$SESSION_ID" || return
    if [[ "$BRAIN_HTTP_STATUS" != 200 ]]; then
        print_error "Could not reconcile session state: $(brain_error_message)"
        return 1
    fi
    if ! SESSION_STATUS=$(jq -er \
        '.status | select(. == "ready" or . == "awaiting_tool_results" or . == "continuation_pending")' \
        <<<"$BRAIN_RESPONSE" 2>/dev/null); then
        print_error "Brain returned invalid session state."
        return 1
    fi
    PENDING_TOOL_CALLS=$(jq -c '.pending_tool_calls // []' <<<"$BRAIN_RESPONSE")
}

reconcile_after_stream_failure() {
    refresh_session_state || return
    if [[ "$SESSION_STATUS" != ready ]]; then
        print_status "Session has unfinished work. Type '/resume' to continue without repeating submitted commands."
    fi
}

# Normalized SSE stream

parse_brain_stream() {
    # One parser per response. NUL-delimited pairs preserve embedded/trailing newlines.
    # Bash cannot store NUL bytes, so reject them rather than corrupt framing.
    jq --unbuffered -Rnj '
        foreach inputs as $raw (
            {event: "", output: []};
            ($raw | rtrimstr("\r")) as $line |
            .output = [] |
            if $line | startswith("__BRAIN_HTTP_STATUS__:") then
                .output = ["http_status", ($line | ltrimstr("__BRAIN_HTTP_STATUS__:"))]
            elif $line | startswith("event: ") then
                .event = ($line | ltrimstr("event: "))
            elif $line | startswith("data: ") then
                ($line | ltrimstr("data: ") | fromjson) as $data |
                if ($data | type) != "object" then
                    error("invalid JSON object in Brain SSE event")
                else
                    .output = [.event,
                        (if .event == "reasoning" or .event == "content" then
                            if ($data.delta | type) == "string" then $data.delta
                            else error("invalid text delta") end
                        elif .event == "error" then
                            ($data.message // "unknown Brain error") |
                            if type == "string" then . else error("invalid error message") end
                        else $data | tojson end)] |
                    .event = ""
                end
            else . end;
            # Check Unicode code points: older jq builds mishandle NUL in contains().
            if any(.output[]; explode | index(0) != null) then
                error("NUL byte in Brain SSE event")
            else .output[] | ., "\u0000" end
        )
    '
}

stream_turn() {
    local payload="$1" event_name='' event_data delta error_message=''
    local http_status='' terminal_event='' curl_pid curl_status=0
    local reasoning_started=false response_started=false

    PENDING_TOOL_CALLS='[]'
    LAST_BRAIN_ERROR=''
    exec 4< <(
        curl --silent --show-error --no-buffer --request POST \
            --connect-timeout "$BRAIN_CONNECT_TIMEOUT_SECONDS" \
            --header 'Content-Type: application/json' --data-binary @- \
            --write-out $'\n__BRAIN_HTTP_STATUS__:%{http_code}\n' \
            "${BRAIN_URL}/v1/sessions/${SESSION_ID}/turns" <<<"$payload" |
            parse_brain_stream
    )
    curl_pid=$!

    while IFS= read -r -d '' -u 4 event_name; do
        if ! IFS= read -r -d '' -u 4 event_data; then
            error_message="incomplete parsed Brain event"
            break
        fi
        if [[ "$event_name" == http_status ]]; then
            http_status="$event_data"
            continue
        fi
        if [[ -n "$terminal_event" ]]; then
            error_message="Brain sent data after terminal event"
            continue
        fi
        case "$event_name" in
            reasoning)
                delta="$event_data"
                if [[ "$reasoning_started" == false ]]; then
                    printf '%sThinking: ' "$COLOR_THINKING"
                    reasoning_started=true
                fi
                printf '%s' "$delta"
                ;;
            content)
                delta="$event_data"
                if [[ "$response_started" == false ]]; then
                    [[ "$reasoning_started" == true ]] && printf '%s\n' "$COLOR_RESET"
                    printf '%sAssistant: ' "$COLOR_ASSISTANT"
                    response_started=true
                fi
                printf '%s' "$delta"
                ;;
            reset)
                [[ "$reasoning_started" == true || "$response_started" == true ]] && printf '%s\n' "$COLOR_RESET"
                print_status "Command finished. Refreshing answer."
                reasoning_started=false
                response_started=false
                ;;
            tool_calls)
                if ! PENDING_TOOL_CALLS=$(jq -ce '
                    .tool_calls |
                    select(type == "array" and length > 0) |
                    select(all(.[];
                        type == "object" and
                        (.id | type == "string" and length > 0) and
                        .type == "function" and
                        (.function | type == "object") and
                        (.function.name | type == "string" and length > 0) and
                        (.function.arguments | type == "string")))
                ' <<<"$event_data" 2>/dev/null); then
                    error_message="invalid tool_calls event"
                    continue
                fi
                terminal_event='tool_calls'
                SESSION_STATUS='awaiting_tool_results'
                ;;
            done) terminal_event='done'; SESSION_STATUS='ready' ;;
            error)
                error_message="$event_data"
                terminal_event='error'
                ;;
            *) error_message="unknown Brain SSE event: ${event_name:-missing}" ;;
        esac
        event_name=''
    done
    exec 4<&-
    wait "$curl_pid" || curl_status=$?

    if [[ "$reasoning_started" == true || "$response_started" == true ]]; then
        printf '%s\n' "$COLOR_RESET"
    fi
    if (( curl_status != 0 )); then
        print_error "Brain stream failed (transport/parser exit $curl_status)."
        reconcile_after_stream_failure || true
        return 1
    elif [[ ! "$http_status" =~ ^2[0-9][0-9]$ ]]; then
        print_error "Brain request failed with HTTP status ${http_status:-unknown}."
        reconcile_after_stream_failure || true
        return 1
    elif [[ -n "$error_message" ]]; then
        LAST_BRAIN_ERROR="$error_message"
        print_error "Brain returned an error: $error_message"
        reconcile_after_stream_failure || true
        return 1
    elif [[ -z "$terminal_event" ]]; then
        print_error "Brain returned an incomplete SSE stream."
        reconcile_after_stream_failure || true
        return 1
    fi
}

recover_interrupted_chat() {
    local reply payload
    if [[ "$LAST_BRAIN_ERROR" != "LLM returned neither content nor tool calls" || \
        "$SESSION_STATUS" != continuation_pending ]]; then
        return 1
    fi
    print_status "AI generated reasoning but no final answer."
    read -r -p "Continue chat? [y/N] " reply || return 1
    case "$reply" in
        [Yy]|[Yy][Ee][Ss]) ;;
        *) return 1 ;;
    esac
    payload=$(jq -cn --arg content "Your session was interrupted, continue" \
        '{type: "recovery", content: $content}')
    stream_turn_with_recovery "$payload"
}

stream_turn_with_recovery() {
    stream_turn "$1" && return
    recover_interrupted_chat
}

submit_tool_results() {
    local calls="$1" results='[]' call id payload index count
    count=$(jq 'length' <<<"$calls")
    for (( index = 0; index < count; index++ )); do
        call=$(jq -c ".[$index]" <<<"$calls")
        id=$(jq -r '.id' <<<"$call")
        TOOL_RESULT=''
        TOOL_FILE_EDIT=''
        TOOL_JOB_ID=''
        if [[ -n "$PENDING_USER_INSTRUCTION" ]]; then
            TOOL_RESULT="Tool call cancelled because user provided new instructions."
            TOOL_APPROVAL='{"decision":"cancelled","prefix":[]}'
        else
            execute_tool_call "$call"
        fi
        results=$(jq -cn --slurpfile current <(printf '%s' "$results") --arg id "$id" \
            --arg content "$TOOL_RESULT" --argjson approval "$TOOL_APPROVAL" \
            --slurpfile file_edit <(printf '%s' "${TOOL_FILE_EDIT:-null}") \
            --arg job_id "$TOOL_JOB_ID" \
            '$current[0] + [{tool_call_id: $id, content: $content, approval: $approval}
              + (if $file_edit[0] == null then {} else {file_edit: $file_edit[0]} end)
              + (if $job_id == "" then {} else {job_id: $job_id} end)]')
    done
    if [[ -n "$PENDING_USER_INSTRUCTION" ]]; then
        payload=$(jq -cn --slurpfile results <(printf '%s' "$results") \
            --arg instruction "$PENDING_USER_INSTRUCTION" --arg cwd "$PWD" \
            '{type: "tool_results", results: $results[0], instruction: $instruction, cwd: $cwd}')
        PENDING_USER_INSTRUCTION=''
    else
        payload=$(jq -cn --slurpfile results <(printf '%s' "$results") --arg cwd "$PWD" \
            '{type: "tool_results", results: $results[0], cwd: $cwd}')
    fi
    stream_turn_with_recovery "$payload"
}

finish_pending_turn() {
    local calls payload
    if [[ "$SESSION_STATUS" == continuation_pending ]]; then
        payload='{"type":"tool_results","results":[]}'
        stream_turn_with_recovery "$payload" || return
    fi
    while [[ "$PENDING_TOOL_CALLS" != '[]' ]]; do
        calls="$PENDING_TOOL_CALLS"
        PENDING_TOOL_CALLS='[]'
        submit_tool_results "$calls" || return
    done
}

send_user_message() {
    local payload
    if [[ "$SESSION_STATUS" != ready ]]; then
        print_error "Session has unfinished work. Type '/resume' or '/new'."
        return 1
    fi
    payload=$(jq -cn --arg content "$1" --arg cwd "$PWD" \
        '{type: "user", content: $content, cwd: $cwd}')
    stream_turn_with_recovery "$payload" || return
    finish_pending_turn
}

# Configuration and conversation

initialize_configuration() {
    local variable_name
    if [[ -z "${BRAIN_URL:-}" ]]; then
        print_error "Environment variable 'BRAIN_URL' is not set."
        return 1
    fi
    BRAIN_URL="${BRAIN_URL%/}"
    if [[ ! "$BRAIN_URL" =~ ^https?://[^[:space:]]+$ ]] || \
        [[ "$BRAIN_URL" == *$'\n'* || "$BRAIN_URL" == *$'\r'* ]]; then
        print_error "BRAIN_URL must be a valid HTTP(S) URL."
        return 1
    fi
    COMMAND_JOB_STATE_DIR="${HOME}/.local/state/ai-helper/command-jobs"
    trap cleanup_command_workers EXIT
    SESSION_FILE="${HOME}/.local/state/ai-helper/session-id"
    CLIENT_ID_FILE="${HOME}/.local/state/ai-helper/client-id"
    for variable_name in COMMAND_TIMEOUT_SECONDS MAX_TOOL_OUTPUT_BYTES \
        BRAIN_CONNECT_TIMEOUT_SECONDS BRAIN_REQUEST_TIMEOUT_SECONDS; do
        if [[ ! "${!variable_name:-}" =~ ^[1-9][0-9]*$ ]]; then
            print_error "$variable_name must be a positive integer."
            return 1
        fi
    done
    for variable_name in SESSION_FILE CLIENT_ID_FILE; do
        if [[ "${!variable_name}" != /* ]]; then
            print_error "$variable_name must be an absolute path."
            return 1
        fi
    done
}

run_conversation() {
    local input prompt='You: '
    configure_readline
    if [[ -n "$COLOR_USER" ]]; then
        printf -v prompt '\001%s\002You: \001%s\002' "$COLOR_USER" "$COLOR_RESET"
    fi
    if [[ "$SESSION_STATUS" != ready ]]; then
        print_status "Resuming unfinished turn."
        finish_pending_turn || return
    fi
    print_status "Type '/resume' to retry unfinished work, '/new' for a new session, or 'exit' to quit."
    while read_conversation_input "$prompt" input; do
        case "$input" in
            exit) print_status "Session saved. Exiting."; return ;;
            /resume)
                refresh_session_state
                if [[ "$SESSION_STATUS" == ready ]]; then
                    print_status "Session has no unfinished work."
                else
                    finish_pending_turn
                fi
                continue
                ;;
            /new) new_session; continue ;;
            /jobs) show_command_jobs; continue ;;
            /job\ *) show_command_job "${input#/job }"; continue ;;
            /stop-job\ *) stop_terminal_command_job "${input#/stop-job }"; continue ;;
        esac
        [[ -z "$input" ]] && continue
        [[ -n "$input" ]] && history -s -- "$input"
        send_user_message "$input"
    done
}

main() {
    require_commands curl jq timeout sha256sum python3
    initialize_configuration
    initialize_state_file "$SESSION_FILE"
    check_brain_ready
    register_client
    print_status "Trusted commands managed in Brain by server IP."
    load_or_create_session
    run_conversation
}

# Brain appends the entry-point call when serving this script.
