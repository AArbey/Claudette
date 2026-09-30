#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RUNNER_STATE_DIR=$(mktemp -d)
trap 'rm -rf -- "$RUNNER_STATE_DIR"' EXIT

TOKEN=secret
EXPECTED_TOKEN_HASH=$(printf '%s' "$TOKEN" | sha256sum | awk '{print $1}')
BRAIN_URL=http://127.0.0.1:1

run_raw() {
    REMOTE_ADDR="${REMOTE_ADDR_OVERRIDE:-192.0.2.1}" \
    RUNNER_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    RUNNER_USER=test RUNNER_HOME=/tmp RUNNER_STATE_DIR="$RUNNER_STATE_DIR" \
    RUNNER_PATH=/usr/bin:/bin TRUSTED_BRAIN_IP=192.0.2.1 \
    RUNNER_FILE_TOOL="$ROOT/client/file_tool.py" \
    RUNNER_COMMAND_WORKER="$ROOT/client/command_worker.py" \
    RUNNER_FILE_STATE_DIR="$RUNNER_STATE_DIR/file-edits" \
    BRAIN_URL="$BRAIN_URL" TOKEN_SHA256="$EXPECTED_TOKEN_HASH" \
    bash "$ROOT/client/runner.sh"
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
jq -e '.status == "ready" and .protocol_version == 2 and .runner_version == 6' \
    <<<"$(body_from_response "$health")" >/dev/null

invalid_update=$(request POST /v1/update '{"token":"bad"}')
[[ "$invalid_update" == HTTP/1.1\ 400* ]]

fake_bin="$RUNNER_STATE_DIR/fake-bin"
mkdir "$fake_bin"
cat >"$fake_bin/curl" <<'CURL'
#!/usr/bin/env bash
while (($#)); do
    if [[ "$1" == -o ]]; then shift; output=$1; break; fi
    shift
done
printf '#!/usr/bin/env bash\nprintf "updated\\n"\n' >"$output"
CURL
chmod +x "$fake_bin/curl"
update=$(PATH="$fake_bin:$PATH" request POST /v1/update '{"token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}')
[[ "$update" == HTTP/1.1\ 200* ]]
jq -e '.updated == true' <<<"$(body_from_response "$update")" >/dev/null

bad_source=$(REMOTE_ADDR_OVERRIDE=192.0.2.99 request GET /healthz)
[[ "$bad_source" == HTTP/1.1\ 403* ]]

payload=$(jq -cn --arg brain_url "$BRAIN_URL" '{
    request_id:("r" * 32), job_id:("r" * 32), job_token:("k" * 64),
    brain_url:$brain_url, session_id:("s" * 32), cwd:"/tmp",
    command:{program:"printf", arguments:["hello"], reason:"test", trust_prefix:["printf"]},
    approval:{decision:"allowed_once", prefix:[]},
    max_runtime_seconds:5, max_output_bytes:1024
}')
first=$(request POST /v1/execute "$payload")
[[ "$first" == HTTP/1.1\ 202* ]]
jq -e '.status == "running" and .job_id == ("r" * 32)' \
    <<<"$(body_from_response "$first")" >/dev/null
duplicate=$(request POST /v1/execute "$payload")
jq -e '.status == "completed" and .exit_code == 0 and .output == "hello"' \
    <<<"$(body_from_response "$duplicate")" >/dev/null

changed=$(jq '.command.arguments = ["changed"]' <<<"$payload")
duplicate_changed=$(request POST /v1/execute "$changed")
jq -e '.output == "hello"' <<<"$(body_from_response "$duplicate_changed")" >/dev/null

unknown_payload=$(jq '.request_id = ("u" * 32) | .job_id = ("u" * 32)' <<<"$payload")
mkdir "$RUNNER_STATE_DIR/requests/$(printf 'u%.0s' {1..32})"
unknown=$(request POST /v1/execute "$unknown_payload")
jq -e '.status == "outcome_unknown" and .exit_code == 125' \
    <<<"$(body_from_response "$unknown")" >/dev/null

truncated_payload=$(jq '.request_id = ("t" * 32) | .job_id = ("t" * 32) |
    .command.arguments = ["123456"] | .max_output_bytes = 4' <<<"$payload")
request POST /v1/execute "$truncated_payload" >/dev/null
truncated=$(request POST /v1/execute "$truncated_payload")
jq -e '.truncated == true and (.output | length) <= 4' \
    <<<"$(body_from_response "$truncated")" >/dev/null

timeout_payload=$(jq '.request_id = ("z" * 32) | .job_id = ("z" * 32) |
    .command = {program:"sleep",arguments:["2"],reason:"test",trust_prefix:["sleep"]} |
    .max_runtime_seconds = 1' <<<"$payload")
request POST /v1/execute "$timeout_payload" >/dev/null
timed_out=$(request POST /v1/execute "$timeout_payload")
jq -e '.status == "timed_out" and .exit_code == 124' \
    <<<"$(body_from_response "$timed_out")" >/dev/null

cd_payload=$(jq '.request_id = ("c" * 32) | .job_id = ("c" * 32) |
    .command = {program:"cd",arguments:["/"],reason:"test",trust_prefix:["cd"]}' <<<"$payload")
request POST /v1/execute "$cd_payload" >/dev/null
cd_response=$(request POST /v1/execute "$cd_payload")
jq -e '.exit_code == 0 and .cwd == "/"' <<<"$(body_from_response "$cd_response")" >/dev/null

file_payload=$(jq -cn --arg cwd "$RUNNER_STATE_DIR" '{action:"apply",request_id:("f" * 32),
    cwd:$cwd,path:"target.txt",operation:"create",old_text:"",new_text:"hello\n",reason:"test"}')
file_response=$(request POST /v1/file "$file_payload")
jq -e '.ok == true and .edit.operation == "create" and (.edit.diff | contains("+hello"))' \
    <<<"$(body_from_response "$file_response")" >/dev/null
[[ $(<"$RUNNER_STATE_DIR/target.txt") == hello ]]
inspect_payload=$(jq -cn --arg cwd "$RUNNER_STATE_DIR" \
    '{action:"read_file",cwd:$cwd,path:"target.txt"}')
inspected=$(request POST /v1/file "$inspect_payload")
jq -e '.ok == true and .content == "hello\n" and .total_lines == 1' \
    <<<"$(body_from_response "$inspected")" >/dev/null
search_payload=$(jq -cn --arg cwd "$RUNNER_STATE_DIR" \
    '{action:"search_text",cwd:$cwd,pattern:"hello",file_glob:"*.txt"}')
searched=$(request POST /v1/file "$search_payload")
jq -e '.ok == true and (.matches | length) == 1 and .matches[0].line == 1' \
    <<<"$(body_from_response "$searched")" >/dev/null
restore_payload=$(jq -cn --arg hash "$(jq -r '.edit.after_hash' <<<"$(body_from_response "$file_response")")" \
    '{action:"restore",edit_id:("f" * 32),request_id:("g" * 32),expected_hash:$hash}')
restored=$(request POST /v1/file "$restore_payload")
jq -e '.ok == true and .edit.status == "restored"' <<<"$(body_from_response "$restored")" >/dev/null
[[ ! -e "$RUNNER_STATE_DIR/target.txt" ]]

chunked=$(printf 'POST /v1/execute HTTP/1.1\r\nAuthorization: Bearer %s\r\nConnection: close\r\nTransfer-Encoding: chunked\r\n\r\n' "$TOKEN" | run_raw)
[[ "$chunked" == HTTP/1.1\ 400* ]]

printf 'runner tests passed\n'
