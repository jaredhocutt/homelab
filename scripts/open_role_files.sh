#!/usr/bin/env bash

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <role>" >&2
    exit 1
fi

ROLE="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLES_DIR="$(dirname "$SCRIPT_DIR")/collections/ansible_collections/jaredhocutt/homelab/roles"

code \
    "$ROLES_DIR/$ROLE/defaults/main.yml" \
    "$ROLES_DIR/$ROLE/vars/main.yml" \
    "$ROLES_DIR/$ROLE/tasks/main.yml" \
    "$ROLES_DIR/$ROLE/handlers/main.yml"
