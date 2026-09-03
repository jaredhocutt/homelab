#!/usr/bin/env bash

# Supplies the Ansible vault password (wired up via `vault_password_file` in
# ansible.cfg). The password lives in Bitwarden Secrets Manager; set
# ANSIBLE_VAULT_PASSWORD to bypass the lookup entirely (useful in CI).

set -euo pipefail

if [ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]; then
    echo "$ANSIBLE_VAULT_PASSWORD"
    exit 0
fi

SECRET_NAME="${ANSIBLE_VAULT_SECRET_NAME:-ansible_vault_password}"

for cmd in bws jq; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "Error: '$cmd' is required to fetch the vault password." >&2
        exit 1
    fi
done

if [ -z "${BWS_ACCESS_TOKEN:-}" ]; then
    echo "Error: BWS_ACCESS_TOKEN is not set." >&2
    exit 1
fi

# `bws secret get` only accepts a UUID, so resolve by name via `list`, which
# returns the values inline.
PASSWORD="$(
    bws secret list -o json \
        | jq -r --arg key "$SECRET_NAME" 'map(select(.key == $key)) | first | .value // empty'
)"

if [ -z "$PASSWORD" ]; then
    echo "Error: no secret named '$SECRET_NAME' is accessible to this access token." >&2
    exit 1
fi

echo "$PASSWORD"
