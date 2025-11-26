#!/usr/bin/env bash

if [ -n "$ANSIBLE_VAULT_PASSWORD" ]; then
    echo "$ANSIBLE_VAULT_PASSWORD"
else
    gpg --batch --use-agent --decrypt vault/vault-pass.gpg
fi
