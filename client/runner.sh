#!/usr/bin/env bash

set -Eeuo pipefail

readonly MAX_HEADER_BYTES=16384
readonly MAX_BODY_BYTES=2097152
readonly READ_TIMEOUT_SECONDS=10
readonly RUNNER_PROTOCOL_VERSION=1
readonly RUNNER_VERSION=3

respond_json() {
    local status="$1" reason="$2" payload="$3" length
    length=$(LC_ALL=C printf '%s' "$payload" | wc -c)
    printf 'HTTP/1.1 %s %s\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n%s' \
        "$status" "$reason" "$length" "$payload"
}

fail_json() {
    local status="$1" reason="$2" message="$3" payload
    payload=$(jq -cn --arg error "$message" '{error: $error}')
    respond_json "$status" "$reason" "$payload"
    exit 0
}

normalized_remote_addr() {
    local value="${REMOTE_ADDR:-}"
    value="${value#::ffff:}"
    printf '%s' "$value"
}

read_request() {
    local line name value lower total=0
    IFS= read -r -t "$READ_TIMEOUT_SECONDS" line || fail_json 408 Timeout "request timeout"
    line="${line%$'\r'}"
    REQUEST_LINE="$line"
    while IFS= read -r -t "$READ_TIMEOUT_SECONDS" line; do
        total=$((total + ${#line} + 1))
        (( total <= MAX_HEADER_BYTES )) || fail_json 431 Large "headers too large"
        line="${line%$'\r'}"
        [[ -n "$line" ]] || break
        [[ "$line" == *:* ]] || fail_json 400 BadRequest "malformed header"
        name="${line%%:*}"
        value="${line#*:}"
        value="${value#${value%%[![:space:]]*}}"
        lower="${name,,}"
        case "$lower" in
            authorization)
                [[ -z "${AUTHORIZATION:-}" ]] || fail_json 400 BadRequest "duplicate authorization"
                AUTHORIZATION="$value"
                ;;
            content-length)
                [[ -z "${CONTENT_LENGTH_VALUE:-}" ]] || fail_json 400 BadRequest "duplicate content length"
                CONTENT_LENGTH_VALUE="$value"
                ;;
            transfer-encoding) fail_json 400 BadRequest "transfer encoding unsupported" ;;
            connection) CONNECTION_VALUE="${value,,}" ;;
            content-type) CONTENT_TYPE_VALUE="${value,,}" ;;
        esac
    done
    [[ "${CONNECTION_VALUE:-}" == close ]] || fail_json 400 BadRequest "connection close required"
}

authenticate() {
    local supplied digest
    [[ "$(normalized_remote_addr)" == "$TRUSTED_BRAIN_IP" ]] || \
        fail_json 403 Forbidden "source IP rejected"
    [[ "${AUTHORIZATION:-}" == "Bearer "* ]] || fail_json 401 Unauthorized "missing bearer token"
    supplied="${AUTHORIZATION#Bearer }"
    digest=$(printf '%s' "$supplied" | sha256sum | awk '{print $1}')
    [[ "$digest" == "$TOKEN_SHA256" ]] || fail_json 401 Unauthorized "invalid bearer token"
}

validate_execute_request() {
    jq -e --arg runner "$RUNNER_ID" '
        type == "object" and
        (keys == ["approval", "command", "cwd", "max_output_bytes", "request_id",
                  "session_id", "timeout_seconds"]) and
        (.request_id | type == "string" and test("^[A-Za-z0-9_-]{32,128}$")) and
        (.session_id | type == "string" and test("^[A-Za-z0-9_-]{32}$")) and
        (.cwd | type == "string" and startswith("/") and
            (explode | index(0) == null) and
            ((contains("\n") or contains("\r")) | not)) and
        (.timeout_seconds | type == "number" and floor == . and . >= 1 and . <= 3600) and
        (.max_output_bytes | type == "number" and floor == . and . >= 1 and . <= 1048576) and
        (.command | type == "object" and
            (keys == ["arguments", "program", "reason", "trust_prefix"]) and
            (.program | type == "string" and length >= 1 and
                (explode | index(0) == null) and
                ((contains("\n") or contains("\r")) | not)) and
            (.reason | type == "string") and
            (.arguments | type == "array" and length <= 64 and all(.[];
                type == "string" and (explode | index(0) == null) and
                ((contains("\n") or contains("\r")) | not))) and
            (.trust_prefix | type == "array" and length >= 1 and length <= 8 and all(.[];
                type == "string" and length >= 1 and (explode | index(0) == null) and
                ((contains("\n") or contains("\r")) | not))) and
            ([.program] + .arguments) as $argv |
            $argv[0:(.trust_prefix | length)] == .trust_prefix) and
        ([.command.program] + .command.arguments) as $command_argv |
        (.approval | type == "object" and (keys == ["decision", "prefix"]) and
            (.decision == "trusted" or .decision == "trusted_now" or
             .decision == "allowed_once") and
            (.prefix | type == "array") and
            (if (.decision == "trusted" or .decision == "trusted_now") then
                (.prefix | length >= 1 and length <= 8) and
                $command_argv[0:(.prefix | length)] == .prefix
             else .prefix == [] end))
    ' "$BODY_FILE" >/dev/null
}

unknown_result() {
    local request_id="$1" cwd="$2" destination="$3" temporary
    temporary="${destination}.tmp.$$"
    jq -cn --arg request_id "$request_id" --arg cwd "$cwd" '
        {request_id: $request_id, status: "outcome_unknown", exit_code: 125,
         output: "Previous execution stopped before result was saved.", cwd: $cwd,
         truncated: false}
    ' >"$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$destination"
}

execute_request() {
    local request_id session_id cwd program timeout_seconds output_limit
    local request_dir result_file temporary output_file byte_count exit_code=0
    local new_cwd truncated=false capture_limit
    local -a arguments=() pipeline_status=()

    validate_execute_request || fail_json 400 BadRequest "invalid execute request"
    request_id=$(jq -r '.request_id' "$BODY_FILE")
    session_id=$(jq -r '.session_id' "$BODY_FILE")
    cwd=$(jq -r '.cwd' "$BODY_FILE")
    request_dir="$RUNNER_STATE_DIR/requests/$request_id"
    result_file="$request_dir/result.json"
    if ! mkdir "$request_dir" 2>/dev/null; then
        if [[ ! -f "$result_file" ]]; then
            unknown_result "$request_id" "$cwd" "$result_file"
        fi
        respond_json 200 OK "$(<"$result_file")"
        return
    fi
    chmod 700 "$request_dir"
    cp -- "$BODY_FILE" "$request_dir/request.json"
    chmod 600 "$request_dir/request.json"
    trap 'if [[ -n "${result_file:-}" && ! -f "$result_file" ]]; then unknown_result "$request_id" "$cwd" "$result_file"; fi' EXIT

    program=$(jq -r '.command.program' "$BODY_FILE")
    mapfile -t arguments < <(jq -r '.command.arguments[]' "$BODY_FILE")
    timeout_seconds=$(jq -r '.timeout_seconds' "$BODY_FILE")
    output_limit=$(jq -r '.max_output_bytes' "$BODY_FILE")
    new_cwd="$cwd"
    output_file="$request_dir/output"
    : >"$output_file"

    if [[ "$program" == cd ]]; then
        if (( ${#arguments[@]} != 1 )); then
            exit_code=1
            printf '%s' 'cd requires exactly one path argument' >"$output_file"
        elif new_cwd=$(cd -- "$cwd" 2>/dev/null && cd -- "${arguments[0]}" 2>/dev/null && pwd -P); then
            printf '%s' "$new_cwd" >"$output_file"
        else
            exit_code=1
            printf '%s' 'cd failed: target is not an accessible directory' >"$output_file"
        fi
    elif ! cd -- "$cwd" 2>/dev/null; then
        exit_code=1
        printf '%s' 'working directory is not accessible' >"$output_file"
    else
        capture_limit=$((output_limit + 1))
        set +e
        (
            timeout --signal=TERM --kill-after=1s "${timeout_seconds}s" \
                env -i "PATH=$RUNNER_PATH" "HOME=$RUNNER_HOME" "PWD=$PWD" \
                "USER=$RUNNER_USER" "LOGNAME=$RUNNER_USER" "LANG=C.UTF-8" "TERM=dumb" \
                "$program" "${arguments[@]}" 2>&1
        ) | {
            head -c "$capture_limit" >"$output_file"
            cat >/dev/null
        }
        pipeline_status=("${PIPESTATUS[@]}")
        set -e
        exit_code=${pipeline_status[0]}
    fi

    byte_count=$(wc -c <"$output_file")
    if (( byte_count > output_limit )); then
        head -c "$output_limit" "$output_file" >"${output_file}.trimmed"
        mv -f -- "${output_file}.trimmed" "$output_file"
        truncated=true
    fi
    temporary="${result_file}.tmp.$$"
    jq -n --arg request_id "$request_id" --arg status completed \
        --argjson exit_code "$exit_code" --rawfile output "$output_file" \
        --arg cwd "$new_cwd" --argjson truncated "$truncated" '
        {request_id: $request_id, status: $status, exit_code: $exit_code,
         output: $output, cwd: $cwd, truncated: $truncated}
    ' >"$temporary"
    chmod 600 "$temporary"
    mv -f -- "$temporary" "$result_file"
    trap - EXIT
    respond_json 200 OK "$(<"$result_file")"
}

execute_file_request() {
    local result_file result
    [[ -f "$RUNNER_FILE_TOOL" ]] || fail_json 503 Unavailable "file editor is not installed"
    result_file=$(mktemp "$RUNNER_STATE_DIR/file-result.XXXXXX")
    if ! python3 "$RUNNER_FILE_TOOL" --state-dir "${RUNNER_FILE_STATE_DIR:-$RUNNER_HOME/.local/state/ai-helper/file-edits}" \
        <"$BODY_FILE" >"$result_file"; then
        rm -f -- "$result_file"
        fail_json 500 Internal "file editor failed"
    fi
    result=$(<"$result_file")
    rm -f -- "$result_file"
    jq -e 'type == "object" and (.ok | type == "boolean")' <<<"$result" >/dev/null || \
        fail_json 500 Internal "invalid file editor response"
    respond_json 200 OK "$result"
}

main() {
    umask 077
    mkdir -p -- "$RUNNER_STATE_DIR/requests"
    read_request
    authenticate
    case "$REQUEST_LINE" in
        'GET /healthz HTTP/1.1')
            [[ -z "${CONTENT_LENGTH_VALUE:-}" || "${CONTENT_LENGTH_VALUE:-}" == 0 ]] || \
                fail_json 400 BadRequest "health request must be empty"
            respond_json 200 OK "$(jq -cn --arg id "$RUNNER_ID" --arg home "$RUNNER_HOME" \
                --argjson protocol "$RUNNER_PROTOCOL_VERSION" \
                --argjson runner_version "$RUNNER_VERSION" \
                '{runner_id: $id, status: "ready", home: $home,
                  protocol_version: $protocol, runner_version: $runner_version}')"
            ;;
        'POST /v1/execute HTTP/1.1'|'POST /v1/file HTTP/1.1')
            [[ "${CONTENT_TYPE_VALUE:-}" == application/json* ]] || \
                fail_json 415 Unsupported "application/json required"
            [[ "${CONTENT_LENGTH_VALUE:-}" =~ ^[0-9]+$ ]] || \
                fail_json 411 LengthRequired "content length required"
            (( CONTENT_LENGTH_VALUE <= MAX_BODY_BYTES )) || \
                fail_json 413 Large "request body too large"
            BODY_FILE=$(mktemp "$RUNNER_STATE_DIR/request.XXXXXX")
            trap 'rm -f -- "${BODY_FILE:-}"' EXIT
            timeout --signal=TERM --kill-after=1s "${READ_TIMEOUT_SECONDS}s" \
                dd bs=1 count="$CONTENT_LENGTH_VALUE" status=none >"$BODY_FILE" || \
                fail_json 408 Timeout "request body timeout"
            [[ "$(wc -c <"$BODY_FILE")" == "$CONTENT_LENGTH_VALUE" ]] || \
                fail_json 400 BadRequest "incomplete request body"
            if [[ "$REQUEST_LINE" == 'POST /v1/file HTTP/1.1' ]]; then
                execute_file_request
            else
                execute_request
            fi
            rm -f -- "$BODY_FILE"
            trap - EXIT
            ;;
        *) fail_json 404 NotFound "unsupported request" ;;
    esac
}

main "$@"
