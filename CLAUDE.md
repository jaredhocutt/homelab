# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Ansible project for managing a personal homelab. Almost all logic lives in a single in-tree collection (`jaredhocutt.homelab`); playbooks at the root just stitch its roles together per host group.

## Environment

- Python is managed with **uv** (`pyproject.toml` / `uv.lock`); `requires-python = ">=3.12,<3.13"`.
- `uv sync` installs both runtime and dev dependencies (ansible-core, ansible-lint, molecule, taskipy, etc.).
- Run everything through `uv run …` so the pinned ansible-core / collection requirements are used.
- Galaxy collection dependencies are pinned in `collections/requirements.yml`; install with `uv run ansible-galaxy collection install -r collections/requirements.yml`.
- Vault password comes from `vault/vault-pass.gpg` (decrypted by `vault/vault-open.sh`, wired up via `ansible.cfg`). `ANSIBLE_VAULT_PASSWORD` env var overrides it.

## Common commands

Always run from the repo root unless noted.

- Run a playbook: `uv run ansible-playbook playbooks/apps.yml` (other entrypoints: `edge.yml`, `media.yml`, `router.yml`, `kickstart.yml`, `sandbox.yml`).
- Target one role on a host: append `--tags <role_name>` (every role import in the playbooks is tagged with the role's short name).
- Limit to a host: `--limit <host>` (hosts defined in `inventory/hosts.yml`).
- Lint: `uv run ansible-lint`.
- Edit a vault file: `uv run task vault-edit <path>` (opens in VS Code).
- Check for newer container image tags: `uv run task check-image-tags` (interactive picker over inventory files; reads `*_image_tag:` lines whose trailing comment is a `skopeo list-tags … | jq …` pipeline).
- Open all files for a role in VS Code: `uv run task open-role-files <role>`.
- Scaffold a new role by copying an existing one: `uv run task duplicate-role <new_role>` (interactive — asks single vs multi-container, which sidecars, then ranks existing roles by similarity for you to pick the source; handles file rename + variable prefix rewrite). Pass `<existing_role> <new_role>` to skip the picker and duplicate directly.
- Run molecule tests for a role: `uv run task molecule test -s <role_name>` (changes cwd into the collection; `-s default` runs the baseline scenario). Molecule uses the **podman** driver, so a running Podman is required.

## Architecture

### Collection layout

The collection at `collections/ansible_collections/jaredhocutt/homelab/` is where all reusable code lives:

- `roles/<app>/` — one role per deployable service (audiobookshelf, authentik, traefik, pangolin, etc.). The root-level playbooks are thin orchestrators that `import_role` these by FQCN (`jaredhocutt.homelab.<role>`).
- `plugins/lookup/` — three custom lookups that roles depend on heavily:
  - `bws` — pulls secrets from Bitwarden Secrets Manager. Used everywhere a credential is needed, including in `inventory/hosts.yml` to resolve `ansible_host` IPs and in playbook `environment:` blocks (e.g. `CLOUDFLARE_TOKEN`, `DIGITALOCEAN_TOKEN`).
  - `container_config` — generates `env`, `secrets`, `labels`, and `quadlet_options` arguments for `containers.podman.podman_container` from role variables. Roles call it as `lookup('jaredhocutt.homelab.container_config', 'env'|'secrets'|'labels'|'quadlet_options')` rather than building those dicts inline.
  - `traefik_labels` — generates Traefik router/service/middleware labels for container-based services.
- `extensions/molecule/` — molecule scenarios, one directory per role. Shared config lives in `extensions/molecule/config.yml`, `prepare.yml`, and `Dockerfile.j2`; per-role `molecule.yml` files inherit from these and add overrides only when needed. **The `newt` role is the canonical reference scenario** (chosen for its simplicity) — copy it when adding new tests.

### Role pattern

Roles deploy services as **Podman Quadlet** units (not docker-compose, not raw systemd). The recurring pattern in a role's `tasks/main.yml`:

1. `containers.podman.podman_image` — pull `{{ <role>_image }}:{{ <role>_image_tag }}`.
2. `containers.podman.podman_secret` — register secrets from `<role>_secrets_combined`; this task must have `no_log: true` (right after `become: true`, before `loop:`) so secret values never hit the log.
3. `containers.podman.podman_container` with `state: quadlet` and `quadlet_file_mode: "0600"` — uses the `container_config` lookup to assemble env/secrets/labels/quadlet_options from role vars, and `notify`s a `Restart <name>` handler (`notify:` line before `become: true`).
4. `meta: flush_handlers` to apply changes immediately.

For roles with sidecar containers (postgres, redis, etc.), each sidecar gets its own `Create <container> container` task using `container=<container>` on the `container_config` lookup calls, its own `Restart <container> for <name>` handler, and its own `<role>_<container>_container_{env,secrets,labels,quadlet_options}[_defaults]` variable set in `defaults/main.yml` / `vars/main.yml` — mirroring the unsuffixed set used for the main container.

A container's `volumes:` entries must reference quadlet-managed volumes as `<volume_name>.volume:/path` (with the `.volume` suffix), not `<volume_name>:/path`. The suffix tells Quadlet this is another Quadlet unit, so it generates a `Requires=`/`After=` dependency on that volume's systemd service; without it, the container has no guaranteed startup ordering against its volume (it can appear to work today only because the volume already exists on disk, but it's fragile across a reboot or `daemon-reload`).

Every role also gets a `tasks/destroy.yml` that reverses `main.yml` exactly: stop each service (main + per-volume + per-network), destroy containers, destroy secrets (also `no_log: true`), destroy volumes, destroy network, remove quadlet files, reload systemd. Keep it in sync whenever `main.yml` gains or loses a container/volume.

If a role needs a host-level capability that only its `podman` dependency can provide (e.g. `traefik`, `portainer`, and `glance` all bind-mount the podman API socket into their container, which requires `podman_enable_socket: true` on the `podman` role), declare it as `vars:` on that dependency entry in the role's own `meta/main.yml` — not in the role's `defaults/main.yml` and not by requiring it in inventory. A role's own `defaults/main.yml` does **not** override a dependency's defaults for the dependency's own tasks (verified empirically); only `vars:` on the `dependencies:` entry does. This keeps the requirement self-contained in the role that needs it, rather than pushing configuration onto every consumer's inventory.

When adding a new role, follow this pattern (or run `uv run task duplicate-role <new_role>` to interactively pick the closest existing role as a source).

### Inventory and host groups

- `inventory/hosts.yml` defines per-host `ansible_host` via `bws` lookups, plus group memberships. Groups in active use: `apps`, `media`, `edge`, `router` (also rolled up into `linux` / `network`).
- `inventory/group_vars/all/{all.yml,vault.yml}` holds the shared variables (vault file is encrypted).
- `edge` is special: `playbooks/edge.yml` runs `connection: local` to provision a DigitalOcean droplet (droplet + reserved IP + firewalls + Cloudflare wildcard DNS), then `add_host`s the resulting IP and continues against it.

### Image tag updates

Every `<service>_image_tag:` variable in an inventory file should have a trailing comment with the exact `skopeo list-tags … | jq …` pipeline that lists candidate upstream tags. The `check-image-tags` script (and the `/update-image-tags` slash command) rely on this convention to suggest updates — keep the comment intact whenever you touch the tag line.
