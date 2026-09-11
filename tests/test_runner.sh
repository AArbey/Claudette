#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNNER_STATE_DIR=$(mktemp -d)
trap 'rm -rf -- "$RUNNER_STATE_DIR"' EXIT

readonly TOKEN=secret
EXPECTED_TOKEN_HASH=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')
readonly EXPECTED_TOKEN_HASH

run_raw() {
    REMOTE_ADDR="${REMOTE_ADDR_OVERRIDE:-192.0.2.1}" \
    RUNNER_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    RUNNER_USER=test RUNNER_HOME=/tmp RUNNER_STATE_DIR="$RUNNER_STATE_DIR" \
    RUNNER_PATH=/usr/bin:/bin TRUSTED_BRAIN_IP=192.0.2.1 \
    TOKEN_SHA256="$EXPECTED_TOKEN_HASH" bash "$ROOT/client/runner.sh"
}

request() {
    local method="$1" path="$2" body="${3-}" length
    length=$(LC_ALL=C printf '%s' "$body" | wc -c)
    printf '%s %s HTTP/1.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n%s' \
        "$method" "$path" "$TOKEN" "$length" "$body" | run_raw
}

body_from_response() { printf '%s' "${1#*$'\r\n\r\n'}"; }

health=$(request GET /healthz)
[[ "$health" == HTTP/1.1\ 200* ]]
jq -e '.status == "ready" and .home == "/tmp" and .protocol_version == 1 and .runner_version == 1' \
    <<<"$(body_from_response "$health")" >/dev/null

bad_source=$(REMOTE_ADDR_OVERRIDE=192.0.2.99 request GET /healthz)
[[ "$bad_source" == HTTP/1.1\ 403* ]]

payload=$(jq -cn '{
    request_id:("r" * 32), session_id:("s" * 32), cwd:"/tmp",
    command:{program:"printf", arguments:["hello"], reason:"test", trust_prefix:["printf"]},
    approval:{decision:"allowed_once", prefix:[]}, timeout_seconds:5, max_output_bytes:1024
}')
first=$(request POST /v1/execute "$payload")
first_body=$(body_from_response "$first")
jq -e '.status == "completed" and .exit_code == 0 and .output == "hello"' \
    <<<"$first_body" >/dev/null

trusted_payload=$(jq '
    .request_id = ("v" * 32) |
    .approval = {decision:"trusted_now", prefix:["printf"]}
' <<<"$payload")
trusted=$(request POST /v1/execute "$trusted_payload")
jq -e '.status == "completed" and .exit_code == 0 and .output == "hello"' \
    <<<"$(body_from_response "$trusted")" >/dev/null

changed=$(jq '.command.arguments = ["changed"]' <<<"$payload")
duplicate=$(request POST /v1/execute "$changed")
jq -e '.output == "hello"' <<<"$(body_from_response "$duplicate")" >/dev/null

unknown_payload=$(jq '.request_id = ("u" * 32)' <<<"$payload")
mkdir "$RUNNER_STATE_DIR/requests/$(printf 'u%.0s' {1..32})"
unknown=$(request POST /v1/execute "$unknown_payload")
jq -e '.status == "outcome_unknown" and .exit_code == 125' \
    <<<"$(body_from_response "$unknown")" >/dev/null

truncated_payload=$(jq -cn '{
    request_id:("t" * 32), session_id:("s" * 32), cwd:"/tmp",
    command:{program:"printf", arguments:["123456"], reason:"test", trust_prefix:["printf"]},
    approval:{decision:"allowed_once", prefix:[]}, timeout_seconds:5, max_output_bytes:4
}')
truncated=$(request POST /v1/execute "$truncated_payload")
jq -e '.output == "1234" and .truncated == true' \
    <<<"$(body_from_response "$truncated")" >/dev/null

timeout_payload=$(jq -cn '{
    request_id:("z" * 32), session_id:("s" * 32), cwd:"/tmp",
    command:{program:"sleep", arguments:["2"], reason:"test", trust_prefix:["sleep"]},
    approval:{decision:"allowed_once", prefix:[]}, timeout_seconds:1, max_output_bytes:1024
}')
timed_out=$(request POST /v1/execute "$timeout_payload")
jq -e '.exit_code == 124' <<<"$(body_from_response "$timed_out")" >/dev/null

cd_payload=$(jq -cn '{
    request_id:("c" * 32), session_id:("s" * 32), cwd:"/tmp",
    command:{program:"cd", arguments:["/"], reason:"test", trust_prefix:["cd"]},
    approval:{decision:"allowed_once", prefix:[]}, timeout_seconds:5, max_output_bytes:1024
}')
cd_response=$(request POST /v1/execute "$cd_payload")
jq -e '.exit_code == 0 and .cwd == "/"' <<<"$(body_from_response "$cd_response")" >/dev/null

chunked=$(printf 'POST /v1/execute HTTP/1.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n\r\n' "$TOKEN" | run_raw)
[[ "$chunked" == HTTP/1.1\ 400* ]]

printf 'runner tests passed\n'
