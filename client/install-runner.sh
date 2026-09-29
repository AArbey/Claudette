#!/usr/bin/env bash

set -Eeuo pipefail

die() { printf 'Error: %s\n' "$1" >&2; exit 1; }

resolve_user() {
    [[ $EUID -eq 0 ]] || die "run installer through sudo"
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
        RUNNER_USER="$SUDO_USER"
    else
        RUNNER_USER=root
    fi
    RUNNER_HOME=$(getent passwd "$RUNNER_USER" | cut -d: -f6)
    RUNNER_GROUP=$(id -gn "$RUNNER_USER")
    [[ -n "$RUNNER_HOME" && "$RUNNER_HOME" == /* ]] || die "cannot find user home"
    CLIENT_ID_FILE="$RUNNER_HOME/.local/state/ai-helper/client-id"
    [[ -r "$CLIENT_ID_FILE" ]] || die "run ai-helper once before installing runner"
    CLIENT_ID=$(<"$CLIENT_ID_FILE")
    [[ "$CLIENT_ID" =~ ^[A-Za-z0-9_-]{32}$ ]] || die "invalid local client ID"
    [[ "$CLIENT_ID" == "$EXPECTED_CLIENT_ID" ]] || die "command belongs to another user"
}

install_dependencies() {
    local name
    local -a packages=()
    for name in jq timeout sha256sum python3; do
        command -v "$name" >/dev/null 2>&1 && continue
        case "$name" in
            timeout|sha256sum) packages+=(coreutils) ;;
            *) packages+=("$name") ;;
        esac
    done
    (( ${#packages[@]} == 0 )) || apt install -y "${packages[@]}"
}

resolve_brain_ip() {
    if [[ "$RUNNER_SOURCE_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ || "$RUNNER_SOURCE_HOST" == *:* ]]; then
        TRUSTED_BRAIN_IP="$RUNNER_SOURCE_HOST"
    else
        TRUSTED_BRAIN_IP=$(getent ahosts "$RUNNER_SOURCE_HOST" | awk 'NR == 1 {print $1}')
    fi
    [[ -n "$TRUSTED_BRAIN_IP" ]] || die "cannot resolve Brain source IP"
}

port_is_free() {
    ! timeout 1 bash -c "exec 3<>/dev/tcp/127.0.0.1/$1" >/dev/null 2>&1
}

choose_port() {
    local existing="/etc/ai-helper-runner/$CLIENT_ID.env" port
    if [[ -r "$existing" ]]; then
        port=$(awk -F= '$1 == "RUNNER_PORT" {print $2}' "$existing")
        if [[ "$port" =~ ^[0-9]+$ && $port -ge $RUNNER_PORT_START && $port -le $RUNNER_PORT_END ]]; then
            RUNNER_PORT="$port"
            return
        fi
    fi
    for (( port=RUNNER_PORT_START; port<=RUNNER_PORT_END; port++ )); do
        if port_is_free "$port"; then RUNNER_PORT="$port"; return; fi
    done
    die "no free runner port"
}

enroll() {
    local payload response
    payload=$(jq -cn --arg client_id "$CLIENT_ID" --argjson port "$RUNNER_PORT" \
        --arg trusted_brain_ip "$TRUSTED_BRAIN_IP" --arg home "$RUNNER_HOME" \
        '{client_id: $client_id, port: $port, trusted_brain_ip: $trusted_brain_ip, home: $home}')
    response=$(curl --fail --silent --show-error --request POST \
        --connect-timeout 5 --max-time 20 --header 'Content-Type: application/json' \
        --data-binary "$payload" "$BRAIN_URL/v1/runner-enrollments/$ENROLLMENT_TOKEN") || \
        die "runner enrollment failed"
    CREDENTIAL=$(jq -er --arg id "$CLIENT_ID" '
        select(.runner_id == $id) | .credential |
        select(type == "string" and length >= 32)
    ' <<<"$response") || die "Brain returned invalid enrollment"
}

download_runner() {
    RUNNER_DOWNLOAD=$(mktemp)
    trap 'rm -f -- "${RUNNER_DOWNLOAD:-}" "${FILE_TOOL_DOWNLOAD:-}" "${COMMAND_WORKER_DOWNLOAD:-}"' EXIT
    curl --fail --silent --show-error --connect-timeout 5 --max-time 20 \
        "$BRAIN_URL/runner.sh" -o "$RUNNER_DOWNLOAD" || die "runner download failed"
    [[ "$(head -n 1 "$RUNNER_DOWNLOAD")" == '#!/usr/bin/env bash' ]] || \
        die "Brain returned invalid runner script"
    FILE_TOOL_DOWNLOAD=$(mktemp)
    curl --fail --silent --show-error --connect-timeout 5 --max-time 20 \
        "$BRAIN_URL/file-tool.py" -o "$FILE_TOOL_DOWNLOAD" || die "file editor download failed"
    [[ "$(head -n 1 "$FILE_TOOL_DOWNLOAD")" == '#!/usr/bin/env python3' ]] || \
        die "Brain returned invalid file editor"
    COMMAND_WORKER_DOWNLOAD=$(mktemp)
    curl --fail --silent --show-error --connect-timeout 5 --max-time 20 \
        "$BRAIN_URL/command-worker.py" -o "$COMMAND_WORKER_DOWNLOAD" || die "command worker download failed"
    [[ "$(head -n 1 "$COMMAND_WORKER_DOWNLOAD")" == '#!/usr/bin/env python3' ]] || \
        die "Brain returned invalid command worker"
}

install_files() {
    local token_hash unit_base
    unit_base="ai-helper-runner-$CLIENT_ID"
    install -d -m 0755 /usr/local/libexec /etc/ai-helper-runner
    install -m 0755 "$RUNNER_DOWNLOAD" /usr/local/libexec/ai-helper-runner
    install -m 0755 "$FILE_TOOL_DOWNLOAD" /usr/local/libexec/ai-helper-file-tool.py
    install -m 0755 "$COMMAND_WORKER_DOWNLOAD" /usr/local/libexec/ai-helper-command-worker.py
    token_hash=$(printf '%s' "$CREDENTIAL" | sha256sum | awk '{print $1}')
    install -d -m 0700 -o "$RUNNER_USER" -g "$RUNNER_GROUP" \
        "/var/lib/$unit_base"
    {
        printf 'RUNNER_ID=%s\n' "$CLIENT_ID"
        printf 'RUNNER_PORT=%s\n' "$RUNNER_PORT"
        printf 'RUNNER_USER=%s\n' "$RUNNER_USER"
        printf 'RUNNER_HOME=%s\n' "$RUNNER_HOME"
        printf 'RUNNER_FILE_TOOL=/usr/local/libexec/ai-helper-file-tool.py\n'
        printf 'RUNNER_COMMAND_WORKER=/usr/local/libexec/ai-helper-command-worker.py\n'
        printf 'BRAIN_URL=%s\n' "$BRAIN_URL"
        printf 'RUNNER_STATE_DIR=/var/lib/%s\n' "$unit_base"
        printf 'RUNNER_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n'
        printf 'TRUSTED_BRAIN_IP=%s\n' "$TRUSTED_BRAIN_IP"
        printf 'TOKEN_SHA256=%s\n' "$token_hash"
    } >"/etc/ai-helper-runner/$CLIENT_ID.env"
    chmod 0600 "/etc/ai-helper-runner/$CLIENT_ID.env"

    {
        printf '[Unit]\nDescription=AI helper runner socket for %s\n\n' "$RUNNER_USER"
        printf '[Socket]\nListenStream=%s\nAccept=yes\nMaxConnections=64\nNoDelay=true\n\n' "$RUNNER_PORT"
        printf '[Install]\nWantedBy=sockets.target\n'
    } >"/etc/systemd/system/$unit_base.socket"
    {
        printf '[Unit]\nDescription=AI helper one-shot runner for %s\nCollectMode=inactive-or-failed\n\n' "$RUNNER_USER"
        printf '[Service]\nType=exec\nUser=%s\nGroup=%s\n' "$RUNNER_USER" "$RUNNER_GROUP"
        printf 'EnvironmentFile=/etc/ai-helper-runner/%s.env\n' "$CLIENT_ID"
        printf 'ExecStart=/usr/local/libexec/ai-helper-runner\n'
        printf 'StandardInput=socket\nStandardOutput=socket\nStandardError=journal\n'
        printf 'NoNewPrivileges=yes\nPrivateMounts=yes\nProtectKernelTunables=yes\nProtectControlGroups=yes\n'
        printf 'ProtectKernelModules=yes\nRestrictSUIDSGID=yes\nLockPersonality=yes\n'
    } >"/etc/systemd/system/$unit_base@.service"
    chmod 0644 "/etc/systemd/system/$unit_base.socket" \
        "/etc/systemd/system/$unit_base@.service"
    systemctl daemon-reload
    systemctl enable "$unit_base.socket"
    systemctl restart "$unit_base.socket"
    systemctl is-enabled --quiet "$unit_base.socket" || \
        die "runner socket is not enabled"
    systemctl is-active --quiet "$unit_base.socket" || \
        die "runner socket failed to start; run: systemctl status $unit_base.socket"
}

main() {
    resolve_user
    install_dependencies
    resolve_brain_ip
    choose_port
    download_runner
    enroll
    install_files
    rm -f -- "$RUNNER_DOWNLOAD" "$FILE_TOOL_DOWNLOAD"
    trap - EXIT
    printf 'Runner installed for %s on port %s. Return to Brain and press Check now.\n' \
        "$RUNNER_USER" "$RUNNER_PORT"
}
