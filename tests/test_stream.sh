#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$repo_dir/client/main.sh"
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT

BRAIN_URL=http://mock
BRAIN_CONNECT_TIMEOUT_SECONDS=1
SESSION_ID=mock
mock_exit=0
curl() { printf '%s' "$mock_stream"; return "$mock_exit"; }
reconcile_after_stream_failure() { :; }

# Exact text, including Unicode, backslashes, empty chunks and trailing newlines.
mock_stream=$'event: reasoning\ndata: {"delta":"think\\n"}\n\nevent: content\ndata: {"delta":"héllo\\n\\n"}\n\nevent: content\ndata: {"delta":""}\n\nevent: content\ndata: {"delta":"\\\\world"}\n\nevent: done\ndata: {}\n\n__BRAIN_HTTP_STATUS__:200\n'
stream_turn '{}' >"$test_dir/output"
printf 'Thinking: think\n\nAssistant: héllo\n\n\\world\n' >"$test_dir/expected"
cmp "$test_dir/expected" "$test_dir/output"
[[ "$SESSION_STATUS" == ready ]]

# NUL guard must accept ordinary/empty strings and reject actual code point zero.
for text_json in '""' '"normal text"' '"\\u0000"' '"é\\n"'; do
    printf 'event: content\ndata: {"delta":%s}\n' "$text_json" |
        parse_brain_stream >"$test_dir/parsed"
done
for text_json in '"\u0000"' '"a\u0000b"' '"end\u0000"'; do
    if printf 'event: content\ndata: {"delta":%s}\n' "$text_json" |
        parse_brain_stream >"$test_dir/parsed" 2>/dev/null; then
        printf 'NUL text accepted\n' >&2
        exit 1
    fi
done

for mock_stream in \
    $'event: content\ndata: {broken}\n__BRAIN_HTTP_STATUS__:200\n' \
    $'event: content\ndata: {"delta":null}\n__BRAIN_HTTP_STATUS__:200\n' \
    $'event: content\ndata: {"delta":"a\\u0000b"}\n__BRAIN_HTTP_STATUS__:200\n' \
    $'event: content\ndata: {"delta":"partial"}\n__BRAIN_HTTP_STATUS__:200\n' \
    $'event: done\ndata: {}\nevent: content\ndata: {"delta":"late"}\n__BRAIN_HTTP_STATUS__:200\n' \
    $'event: done\ndata: {}\n__BRAIN_HTTP_STATUS__:500\n' \
    $'event: tool_calls\ndata: {"tool_calls":[]}\n__BRAIN_HTTP_STATUS__:200\n'; do
    if stream_turn '{}' >"$test_dir/output" 2>&1; then
        printf 'Invalid stream accepted\n' >&2
        exit 1
    fi
done

mock_stream=$'event: done\ndata: {}\n__BRAIN_HTTP_STATUS__:200\n'
mock_exit=18
if stream_turn '{}' >"$test_dir/output" 2>&1; then
    printf 'Failed curl accepted\n' >&2
    exit 1
fi

printf 'stream tests: ok\n'
