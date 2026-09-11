#!/usr/bin/env bash

set -Eeuo pipefail

TOOL_RESULT=''
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
    [[ -t 0 ]] || return
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
                timeout) packages+=(coreutils) ;;
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
        (.prefix | type == "array" and length <= 8 and
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
        (.trust_prefix | type == "array" and length >= 1 and length <= 8 and
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
    local arguments_json="$1" program reason printable_reason
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
    if [[ "$program" == "cd" ]]; then
        if cd -- "${arguments[0]}" 2>/dev/null; then
            printf -v TOOL_RESULT 'exit_code=0\n%s' "$PWD"
        else
            TOOL_RESULT=$'exit_code=1\ncd failed: target is not an accessible directory'
        fi
        return
    fi
    capture_command env -i \
        "PATH=$PATH" "HOME=$HOME" "PWD=$PWD" "USER=${USER:-}" \
        "LOGNAME=${LOGNAME:-}" "LANG=${LANG:-C.UTF-8}" "TERM=${TERM:-dumb}" \
        "$program" "${arguments[@]}"
}

execute_tool_call() {
    local call="$1" name arguments_text arguments_json
    TOOL_APPROVAL='{"decision":"invalid","prefix":[]}'
    name=$(jq -r '.function.name // empty' <<<"$call")
    printf '%sAI requested function: %q%s\n' "$COLOR_STATUS" "${name:-unknown}" "$COLOR_RESET"
    arguments_text=$(jq -r '.function.arguments // "{}"' <<<"$call")
    if ! arguments_json=$(jq -ce 'if type == "object" then . else error("not object") end' \
        <<<"$arguments_text" 2>/dev/null); then
        TOOL_RESULT="Tool error: model supplied invalid JSON arguments."
        return
    fi
    case "$name" in
        run_command) execute_approved_command "$arguments_json" ;;
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
        if [[ -n "$PENDING_USER_INSTRUCTION" ]]; then
            TOOL_RESULT="Tool call cancelled because user provided new instructions."
            TOOL_APPROVAL='{"decision":"cancelled","prefix":[]}'
        else
            execute_tool_call "$call"
        fi
        results=$(jq -cn --argjson current "$results" --arg id "$id" \
            --arg content "$TOOL_RESULT" --argjson approval "$TOOL_APPROVAL" \
            '$current + [{tool_call_id: $id, content: $content, approval: $approval}]')
    done
    if [[ -n "$PENDING_USER_INSTRUCTION" ]]; then
        payload=$(jq -cn --argjson results "$results" \
            --arg instruction "$PENDING_USER_INSTRUCTION" --arg cwd "$PWD" \
            '{type: "tool_results", results: $results, instruction: $instruction, cwd: $cwd}')
        PENDING_USER_INSTRUCTION=''
    else
        payload=$(jq -cn --argjson results "$results" --arg cwd "$PWD" \
            '{type: "tool_results", results: $results, cwd: $cwd}')
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
        esac
        [[ -z "$input" ]] && continue
        [[ -n "$input" ]] && history -s -- "$input"
        send_user_message "$input"
    done
}

main() {
    require_commands curl jq timeout
    initialize_configuration
    initialize_state_file "$SESSION_FILE"
    check_brain_ready
    register_client
    print_status "Trusted commands managed in Brain by server IP."
    load_or_create_session
    run_conversation
}

# Brain appends the entry-point call when serving this script.
