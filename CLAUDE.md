# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Ansible project for managing a personal homelab. Almost all logic lives in a single in-tree collection (`jaredhocutt.homelab`); the playbooks in `playbooks/` just stitch its roles together per host.

## Environment

- Python is managed with **uv** (`pyproject.toml` / `uv.lock`); `requires-python = ">=3.12,<3.13"`.
- `uv sync` installs both runtime and dev dependencies (ansible-core, ansible-lint, molecule, taskipy, etc.).
- Run everything through `uv run …` so the pinned ansible-core / collection requirements are used.
- Galaxy collection dependencies are pinned in `collections/requirements.yml`; install with `uv run ansible-galaxy collection install -r collections/requirements.yml`.
- The control node must be macOS or Linux (including WSL2 on Windows) — ansible-core does not run natively on Windows. On WSL2, keep the repo inside the Linux filesystem (`~/...`), not `/mnt/c/...`, so file modes and performance behave.
- Required outside of uv: the `bws` CLI and `jq` (vault password), `skopeo` (`check-image-tags`), `podman` (molecule), and GNU sed — `gsed` via `brew install gnu-sed` on macOS.
- `BWS_ACCESS_TOKEN` and `BWS_ORGANIZATION_ID` must be in the environment. Everything secret-related depends on them; the `bws` CLI needs only the token, since it is org-scoped, but the `jaredhocutt.homelab.bws` lookup needs both.
- Vault password comes from Bitwarden Secrets Manager, under the secret name `ansible_vault_password`. `vault/vault-open.sh` (wired up via `ansible.cfg`) fetches it with `bws secret list | jq`, resolving by name because `bws secret get` only accepts a UUID. `ANSIBLE_VAULT_PASSWORD` overrides the lookup; `ANSIBLE_VAULT_SECRET_NAME` overrides the secret name.

## Common commands

Always run from the repo root unless noted.

- Run a playbook: `uv run ansible-playbook playbooks/apps.yml`. There is one playbook per host:
  - `apps.yml` and `pta.yml` run `server_setup`, then the foundation apps (`traefik`, plus `authentik` on apps), then the apps.
  - `edge.yml` provisions the DigitalOcean droplet, then deploys to it (see below).
  - `router.yml` manages pfSense DNS resolver entries.
  - `kickstart.yml` runs against all hosts, but renders each host's kickstart file on localhost.
- Target one role on a host: append `--tags <role_name>` (every role import in the playbooks is tagged with the role's short name).
- Limit to a host: `--limit <host>` (hosts defined in `inventory/hosts.yml`).
- Lint: `uv run ansible-lint`.
- Edit a vault file: `uv run task vault-edit <path>` (uses `$EDITOR`; set it inline to override, e.g. `EDITOR='code --wait' uv run task vault-edit <path>`).
- Check for newer container image tags: `uv run task check-image-tags [<file-or-dir>]`, e.g. `inventory/host_vars/apps` to check every app file (with no argument, an interactive picker over the `host_vars` / `group_vars` entries; reads `*_image_tag:` lines whose trailing comment is a `skopeo list-tags … | jq …` pipeline, and reports each update's `file:line`).
- Open all files for a role in VS Code: `uv run task open-role-files <role>`.
- Scaffold a new role by copying an existing one: `uv run task duplicate-role <existing_role> <new_role>`. Both arguments are required; there is no interactive picker. The script copies `roles/<existing_role>` to `roles/<new_role>` and replaces every occurrence of `<existing_role>` with `<new_role>` in the copied files (GNU sed). It does not copy the molecule scenario, and it does not replace the hyphenated form of the name used in `<role>_name` (e.g. `it-tools` for `it_tools`), so do both by hand and grep the new role for leftovers of the source.
- Run molecule tests for a role: `uv run task molecule test -s <role_name>` (changes cwd into the collection). The `default` scenario is an empty placeholder that molecule's shared-state lookup needs; there's nothing to run in it. Molecule uses the **podman** driver, so a running Podman is required.

## Architecture

### Collection layout

The collection at `collections/ansible_collections/jaredhocutt/homelab/` is where all reusable code lives:

- `roles/<app>/` — one role per deployable service (audiobookshelf, authentik, traefik, pangolin, etc.). The root-level playbooks are thin orchestrators that `import_role` these by FQCN (`jaredhocutt.homelab.<role>`).
- `plugins/lookup/` — three custom lookups that roles depend on heavily:
  - `bws` — pulls secrets from Bitwarden Secrets Manager. Used everywhere a credential is needed, including in `inventory/hosts.yml` to resolve `ansible_host` IPs and in playbook `environment:` blocks (e.g. `CLOUDFLARE_TOKEN`, `DIGITALOCEAN_TOKEN`).
  - `container_config` — generates `env`, `secrets`, `labels`, and `quadlet_options` arguments for `containers.podman.podman_container` from role variables. Roles call it as `lookup('jaredhocutt.homelab.container_config', 'env'|'secrets'|'labels'|'quadlet_options')` rather than building those dicts inline.
  - `traefik_labels` — generates Traefik router/service/middleware labels for container-based services. Every role except `authentik` (which serves the forward-auth outpost, so it can't sit behind it) passes `auth=<role>_traefik_auth` (default `false`) and `noauth_paths=<role>_traefik_noauth_paths` (default `[]`), so putting a service behind Authentik forward-auth, with bypass paths for APIs or webhooks, is an inventory change. The noauth router strips client-supplied `X-Authentik-*` headers; never hand-roll these labels in inventory.
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

If a role needs a host-level capability that only its `podman` dependency can provide (e.g. `traefik` and `glance` both bind-mount the podman API socket into their container, which requires `podman_enable_socket: true` on the `podman` role), declare it as `vars:` on that dependency entry in the role's own `meta/main.yml` — not in the role's `defaults/main.yml` and not by requiring it in inventory. A role's own `defaults/main.yml` does **not** override a dependency's defaults for the dependency's own tasks (verified empirically); only `vars:` on the `dependencies:` entry does. This keeps the requirement self-contained in the role that needs it, rather than pushing configuration onto every consumer's inventory.

### Authentication / SSO

The goal is SSO through Authentik on as many apps as possible on `apps` (and Pangolin on `edge`). For `pta`, the plan is Authelia instead, which isn't deployed yet.
- **Prefer native OIDC.** Put the issuer/well-known URL and other non-secret settings in `<role>_container_env`. Route the client ID and secret through `<role>_secrets` / `<role>_container_secrets`, from BWS secrets named `<role>_oidc_client_id` / `_secret` or similar. The provider is shown to users as "Hocutt SSO". Good examples are `homebox`, `karakeep` and `tandoor`; tandoor's JSON provider config goes in a Podman secret, not plain env.
- **Otherwise use forward-auth.** Set `<role>_traefik_auth: true`, and list the paths that must skip the browser login (API, webhooks, mobile apps) in `<role>_traefik_noauth_paths`.
- Many apps are configured for SSO in their own UI, so the repo alone doesn't show full SSO coverage.

When adding a new role, follow this pattern. The `/new-role <app>` command (`.claude/commands/new-role.md`) covers the whole workflow: it researches the app, picks the closest existing role, scaffolds from it with `uv run task duplicate-role <existing_role> <new_role>`, and wires up the inventory, playbook and molecule scenario.

### Inventory and host groups

- `inventory/hosts.yml` defines the hosts `apps`, `pta`, `edge`, `router` and `localhost`, with `ansible_host` resolved through `bws` lookups (`edge` has none; its IP is set at runtime). Playbooks target hosts by name. The only groups are `linux` (`apps`, `pta`, `edge`) and `network` (`router`).
- `inventory/group_vars/all/{all.yml,vault.yml}` holds the shared variables, `group_vars/linux.yml` the Linux-host ones, and `group_vars/network/vault.yml` the router's. The vault files are encrypted.
- `inventory/host_vars/{apps,edge,pta}/` are directories that Ansible merges into each host's variables, with one file per role named after the role (and its playbook tag), e.g. `apps/karakeep.yml`. A role's `newt_<role>_*` variables live in the role's file. Host-wide variables go in `common.yml` (`common_traefik_domain`, shared volumes, `ansible_python_interpreter`), `server_setup.yml` and `kickstart.yml`; edge's droplet and firewall provisioning variables are in `digitalocean.yml`, and its DNS variables in `cloudflare.yml`. A new role gets its own file, and a variable must be defined in only one file per host. `localhost.yml` and `router.yml` are still single files.
- `edge` is special: `playbooks/edge.yml` runs `connection: local` to provision a DigitalOcean droplet (droplet + reserved IP + firewalls + Cloudflare wildcard DNS), then `add_host`s the resulting IP and continues against it.

### Image tag updates

Every `<service>_image_tag:` variable in an inventory file should have a trailing comment with the exact `skopeo list-tags … | jq …` pipeline that lists candidate upstream tags. The `check-image-tags` script (and the `/update-image-tags` slash command) rely on this convention to suggest updates — keep the comment intact whenever you touch the tag line.

Before suggesting a tag, the script verifies it is actually pullable by fetching its raw manifest and confirming a `linux/amd64` image manifest exists (override with `--platform`, skip with `--no-pull-check`). This catches tags that exist in `list-tags` but that podman cannot pull — most notably a **nested image index**, where the top-level index points at further indexes instead of image manifests, and `podman pull` fails with "Unexpectedly received a manifest list instead of a manifest for a single image". Note that `skopeo inspect` recurses into nested indexes and reports such images as healthy, so only the raw-manifest check detects them. A flagged tag is reported but never silently skipped, so a later fixed release still shows up.

Versions that aren't image tags (e.g. `backrest_version`, `pangolin_badger_version`) use a `# github-release <owner>/<repo>` comment instead of a skopeo pipeline and are compared with the repository's latest GitHub release. `uv run task check-image-tags --drift` reports any `*_image_tag` / `*_version` pinned differently across inventory files (no network calls).

When a tag has to be held back, keep the `skopeo` comment intact and append a hold marker to the same line: `# hold: <reason>` or `# hold: <url>`. The script strips the marker before running the pipeline and reports the tag as **held**, so it is never counted or suggested as an update. Only the `# hold:` form is recognized; any other trailing comment does not hold anything.

- **`# hold: <reason>`** freezes the tag, e.g. a release that is a nested image index podman can't pull. The registry's newest tag is still shown for information. Remove the marker once the reason no longer applies.
- **`# hold: <url>`** makes the tag follow the version an upstream compose file pins for the same image. Use this for sidecars a project pins exactly, such as karakeep's meilisearch, postiz's temporal and elasticsearch, and tracearr's timescaledb. The script fetches the file and suggests an update only when upstream's pin differs from the current tag, and then it suggests the upstream pin, not the registry's newest tag. In a `raw.githubusercontent.com` URL, `{latest_release}` is replaced with the repository's latest GitHub release tag. Prefer `{<variable>}`, which is replaced with that variable's current value from the same file, or from any file in the same `host_vars/<host>/` directory, e.g. `.../karakeep/v{karakeep_image_tag}/docker/docker-compose.yml`, so the sidecar follows the release you actually run. When that variable is itself being updated, the script says to re-check afterwards.

## Working in this repo

- **Never commit.** Every commit is GPG-signed and signed off, which Claude can't do. Stage changes if that helps, then give a ready-to-run command in a bash block using the user's alias `git cmt` (`commit -sS`), e.g. `git cmt -m "Add and install <role>"`. Commit messages must never mention Claude or AI: no `Co-Authored-By` trailer and no "Generated with" line. For unattended work only, commit to one topic branch per item off `main` with `git commit --no-gpg-sign`; the user reviews it with `git diff main...<branch>` and re-signs before merging. Never commit on `main`.
- **Ask with clickable options.** When a decision is needed, use AskUserQuestion with options rather than asking the user to type a keyword.
- **Diffs are reviewed in VS Code / GitLens.** Don't send rendered HTML diffs.
