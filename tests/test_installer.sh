#!/usr/bin/env bash

set -Eeuo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
test_dir=$(mktemp -d)
trap 'rm -rf -- "$test_dir"' EXIT

# shellcheck source=../client/install-runner.sh
source "$repo_dir/client/install-runner.sh"

EXPECTED_CLIENT_ID=rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr
mkdir -p "$test_dir/root/.local/state/ai-helper"
printf '%s\n' "$EXPECTED_CLIENT_ID" >"$test_dir/root/.local/state/ai-helper/client-id"

getent() {
    if [[ "$1" == passwd && "$2" == root ]]; then
        printf 'root:x:0:0:root:%s:/bin/bash\n' "$test_dir/root"
        return
    fi
    command getent "$@"
}

id() {
    if [[ "$1" == -gn && "$2" == root ]]; then
        printf 'root\n'
        return
    fi
    command id "$@"
}

unset SUDO_USER
resolve_user
[[ "$RUNNER_USER" == root ]]
[[ "$RUNNER_HOME" == "$test_dir/root" ]]
[[ "$RUNNER_GROUP" == root ]]
[[ "$CLIENT_ID" == "$EXPECTED_CLIENT_ID" ]]

SUDO_USER=root
resolve_user
[[ "$RUNNER_USER" == root ]]

printf 'installer tests: ok\n'
