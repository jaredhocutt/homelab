#!/usr/bin/env bash

set -euo pipefail

if [ $# -ne 2 ]; then
    echo "Usage: $0 <existing_role> <new_role>" >&2
    exit 1
fi

EXISTING_ROLE="$1"
NEW_ROLE="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROLES_DIR="$(dirname "$SCRIPT_DIR")/collections/ansible_collections/jaredhocutt/homelab/roles"

if [ ! -d "$ROLES_DIR/$EXISTING_ROLE" ]; then
    echo "Error: Role '$EXISTING_ROLE' does not exist in $ROLES_DIR" >&2
    exit 1
fi

if [ -d "$ROLES_DIR/$NEW_ROLE" ]; then
    echo "Error: Role '$NEW_ROLE' already exists in $ROLES_DIR" >&2
    exit 1
fi

cp -r "$ROLES_DIR/$EXISTING_ROLE" "$ROLES_DIR/$NEW_ROLE"
find "$ROLES_DIR/$NEW_ROLE" -type f -exec sed -i '' "s/$EXISTING_ROLE/$NEW_ROLE/g" {} +

echo "Successfully duplicated role '$EXISTING_ROLE' to '$NEW_ROLE'"
