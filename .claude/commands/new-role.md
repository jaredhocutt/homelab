---
description: Research an app and scaffold a new jaredhocutt.homelab role for it, following this repo's conventions
argument-hint: <app name> [compose URL, target host, notes…]
---

Create a new role in the `jaredhocutt.homelab` collection for the app described here:

REQUEST = "$ARGUMENTS"

If REQUEST is blank, ask which app to deploy and stop until you have an answer.

The new role must look like every other role in this repo, so that a reader can't tell which one was written last. CLAUDE.md is the authority on the shared rules (Quadlet pattern, `container_config` / `traefik_labels` lookups, `.volume` suffixes, `destroy.yml`, `meta` dependency vars, image tag comments). This command adds the workflow, the per-file details, and the report. When this file and the existing roles disagree, follow the roles and say so in your report.

Work through the phases below in order. Don't write any files until Phase 3.

## Phase 1: Research the app

Read the upstream project before designing anything. Use WebSearch and WebFetch, and fetch raw files (`raw.githubusercontent.com/...`) instead of rendered GitHub pages.

1. Find the official docs and the **official** docker-compose file. If REQUEST names a compose URL, use that one. Otherwise, take it from the project's repo at the release tag you plan to pin, not `main`. Note every URL you use; they go in the report.
2. Read the install, configuration/environment, reverse-proxy, and authentication pages. Don't rely on the compose file alone, because compose examples often leave out settings the docs call required in production.
3. From the compose file and the docs, write down:
   - **Images:** the fully-qualified name of each one (`docker.io/…`, `ghcr.io/…`, since Podman needs the registry), the upstream tag scheme, and the latest stable release.
   - **Sidecars** (postgres, redis, meilisearch, a browser, workers…), each with the version upstream pins. Note whether upstream pins it exactly; if so, the inventory tag gets a `# hold: <url>` marker.
   - **Ports:** which one is the web UI, and whether any non-HTTP ports need publishing.
   - **Volumes and container paths**, and which ones hold persistent data versus cache.
   - **Environment variables**, split into required, recommended, and secret (keys, passwords, tokens, client secrets, and URLs with embedded credentials).
   - **Health:** the documented health endpoint or command, and whether the image ships `curl` or `wget`.
   - **Runtime needs:** `user`/PUID/PGID, `cap_add`, `shm_size`, `init`, `command`/`entrypoint` overrides, ulimits, devices, and `depends_on` conditions.
   - **Reverse proxy:** the base/public URL, trusted proxies, allowed hosts, and websocket needs.
   - **Auth:** does the app support native OIDC? If so, find the env vars and the redirect URI. If not, plan forward-auth, and work out which paths must bypass it (API, webhooks, mobile apps, shares/RSS, health).
   - **Upgrade notes:** migrations, one-way upgrades, and pinned major versions.

## Phase 2: Settle open questions

Use AskUserQuestion with clickable options, and ask only about choices that REQUEST and the defaults below don't already settle. Combine them into one call:

- **Host:** default `apps`. The other app hosts are `pta` and `edge`, and each has `playbooks/<host>.yml` and `inventory/host_vars/<host>/`.
- **Exposure:** default internal-only through Traefik. Ask whether the app should also be public through Pangolin, which adds a `newt` instance.
- **Auth:** default native OIDC through Authentik if the app supports it, otherwise forward-auth (`<role>_traefik_auth: true`).
- **Role name:** snake_case, e.g. `it_tools`. Ask only if it's ambiguous. `<role>_name` defaults to the hyphenated form (`it-tools`), which becomes the container, network, and hostname prefix.

## Phase 3: Scaffold from the closest existing role

Pick the existing role whose shape matches best, and read all of its files before copying it:

| Shape | Start from |
|---|---|
| Single container, no volumes | `it_tools` |
| Single container with volumes | `seerr`, `audiobookshelf` |
| App + postgres | `tandoor`, `warracker`, `homebox` |
| App + mariadb | `snipe_it`, `romm` |
| App + postgres + redis | `n8n`, `tracearr` |
| App + meilisearch/browser sidecars | `karakeep`, `bar_assistant` |
| Several app containers (server/worker) | `authentik`, `n8n` |

Also check the roles listed in CLAUDE.md for ones that use the same sidecar image, and match how they configure it.

Duplicate it with the non-interactive form (the picker needs a TTY):

```
uv run task duplicate-role <source_role> <new_role>
```

That only copies `roles/<source_role>` and replaces `<source_role>` with `<new_role>` everywhere in it. You then have to do these yourself:

- Copy `extensions/molecule/<source_role>/` to `extensions/molecule/<new_role>/` and rename inside it.
- Replace the hyphenated form of the source name (`it-tools`), which the script misses.
- Remove the source's leftovers: images, env vars, comments, sidecars you don't need, and any extra vars.
- Finish with `grep -rni '<source_role>\|<source-name>' roles/<new_role> extensions/molecule/<new_role>` until it comes back empty.

## Phase 4: Write the role

All paths below are relative to `collections/ansible_collections/jaredhocutt/homelab/`. Keep the file layout `defaults/ files/.keep handlers/ meta/ README.md (empty) tasks/{main,destroy}.yml templates/.keep tests/inventory vars/`.

**`defaults/main.yml`.** Keep the header comment, then use this order:

1. `<role>_name`
2. Images and tags. Defaults use a floating tag (`latest`, `release`, or a major like `"16"`); the exact pin goes in the inventory.
3. Volume names, as `"{{ <role>_name }}-<purpose>"`, and sidecars as `<role>_<sidecar>_volume_<purpose>`.
4. `<role>_traefik_enable` / `_host` / `_auth: false` / `_noauth_paths: []` and `<role>_ports`.
5. `<role>_secrets: {}`
6. The empty override set (`_container_env: {}`, `_container_secrets: []`, `_container_labels: {}`, `_container_quadlet_options: []`) for the main container, then each sidecar in turn.
7. Any app-level tunables last, e.g. karakeep's `nextauth_url` derived from the Traefik host.

**`vars/main.yml`.** Copy the source role's banner sections and their exact spelling: Environment, Secrets, Container Labels, Quadlet Options. Put fixed internal values (DB name/user, internal URLs like `http://{{ <role>_name }}-postgres:5432`) above the first banner. Each container gets its `_container_{env,secrets,labels,quadlet_options}_defaults`.
- `<role>_secrets_defaults` maps Podman secret names (`<role>_<thing>`) to variables. `<role>_secrets_combined` combines that with `<role>_secrets`. Containers reference secrets as `<secret>,type=env,target=<ENV>`.
- Labels come from `traefik_labels` with `auth=` and `noauth_paths=` set, wrapped in the `ternary(..., {})` on `traefik_enable`. Sidecar labels are `{}`.
- The main container's `Requires=`/`After=` on its sidecars goes in `<role>_quadlet_options_depends`, and is used as its quadlet options default.
- Add a short comment wherever a value isn't obvious, saying *why*, the way karakeep explains its websocket URL. Don't comment the obvious.

**`tasks/main.yml`.** Steps go in this order: pull images (one looped task) → create network (`isolate: "true"`) → create volumes (looped, with `options: "{{ <var>_options | default(omit) }}"`) → create secrets → sidecars, dependencies first → main container → `flush_handlers`.
- Container task keys follow the existing order: `name`, `image`, `network`, `env`, `secrets`, then any of `security_opt`/`cap_add`/`shm_size`/`init`/`command`, then `volumes`, `publish`, the `healthcheck_*` keys, `sdnotify`, `labels`, `quadlet_file_mode: "0600"`, `quadlet_options`, `state: quadlet`, `notify`, `become: true`.
- Sidecars join only `<role>.network`. The main container also joins `common_traefik_network ~ '.network'` when Traefik is enabled, and uses `publish: "{{ <role>_traefik_enable | ternary(omit, <role>_ports) }}"`.
- Healthchecks use the `CMD-SHELL` form with `healthcheck_failure_action: kill`, and the interval/retries/start_period/timeout keys like the other roles. Any sidecar another container waits on also gets `sdnotify: healthy`, so that `After=` really waits for it. Use the standard checks: `pg_isready -d … -U …`, `redis-cli ping`, `healthcheck.sh --connect --innodb_initialized`. Only add a healthcheck whose binary exists in the image. If there isn't one, leave it out and say so in the report.
- Use `security_opt: [label=level:s0]` or `:z` only when the app needs a shared or bind-mounted path, as `romm` and `youtarr` do.

**`handlers/main.yml`.** Add `Restart {{ <role>_name }}`, plus one `Restart <sidecar> for {{ <role>_name }}` per sidecar, each a `systemd` restart with `enabled` and `daemon_reload`.

**`tasks/destroy.yml`.** This mirrors `main.yml` exactly: stop the services (main, sidecars, `<volume>-volume.service`, `<role>_name-network.service`), destroy the containers, destroy the secrets (`no_log: true`), destroy the volumes, destroy the network, remove the quadlet files, and reload systemd.

**`meta/main.yml`.** Depend on `jaredhocutt.homelab.common` and `jaredhocutt.homelab.podman`. Add `vars:` on the podman entry only if the role needs a host capability.

## Phase 5: Wire it in

- **`inventory/host_vars/<host>/<role>.yml`.** Start with the `---` / `# yamllint disable rule:line-length` header. Next come the image tags, pinned exactly, quoted when purely numeric, each with its `skopeo list-tags … | jq …` comment (copy the jq filter style from a role with the same registry). Add `# hold: <compose url>` with `{<role>_image_tag}` templating for sidecars upstream pins. Then secret variables as `lookup('jaredhocutt.homelab.bws', '<role>_<thing>')`, then `<role>_container_env` for app settings, then `<role>_secrets`/`<role>_container_secrets` for OIDC client credentials. Use the shared `homelab_smtp_*` BWS secrets for mail. If the app is public, add `newt_<role>_secrets` / `newt_<role>_container_secrets` last, copied from `karakeep.yml`. Keep host-specific config here, not in role defaults.
- **`playbooks/<host>.yml`.** Add an `import_role` block, in alphabetical order, tagged `[<role>]`. If the app is public, follow it with the `Install <role> newt` block (`newt_name: newt-<role>`, `newt_attach_to: <role>_name`, tags `[newt, <role>]`).
- **Molecule (`extensions/molecule/<role>/`).** `molecule.yml` is the two-line stub. `converge.yml` sets `<role>_traefik_enable: false` and dummy values for every variable the inventory would supply from BWS. `verify.yml` lists every expected quadlet file (asserting it exists, is non-empty, and has mode `0600`), and asserts the main container's quadlet contains its image, a volume mount, and the published port. Model it on `karakeep`/`newt`.

## Phase 6: Validate

Run these and fix what they report. If a check fails and you can't fix it, say so and include the output.

```
uv run ansible-lint
uv run task check-image-tags inventory/host_vars/<host>/<role>.yml
uv run ansible-playbook playbooks/<host>.yml --syntax-check
uv run task molecule test -s <role>
```

The molecule run needs Podman and takes a few minutes. If it can't run, say why.

## Phase 7: Report and decide on deviations

By default, the role is a **faithful translation** of the upstream compose file into this repo's conventions. Don't apply your own improvements on your own initiative; propose them.

End with this report:

1. **Sources.** The doc and compose URLs, and the version pinned.
2. **Files created/changed**, as clickable links.
3. **Convention translations.** These are informational, not up for a vote. List each compose item and what it became: published ports → Traefik labels, plaintext secrets → Podman secrets from BWS, compose volumes → Quadlet `.volume`s, `depends_on` → `Requires=`/`After=` with `sdnotify`, the default network → an isolated per-role network, `TZ` → from `common`, `restart:` → the common quadlet options, `latest` → an exact pin.
4. **Suggested deviations.** Number each one, and give: what upstream does, what you suggest, why (cite the docs page or a security/reliability reason), and the risk of each choice. Typical candidates are an extra env var the docs recommend, a missing or better healthcheck, disabling signups or telemetry, a different sidecar version, dropping an unneeded sidecar or port, forward-auth bypass paths, and resource limits.
5. **Manual steps for the user:**
   - The BWS secrets to create, with a generation command for each (e.g. `openssl rand -hex 32`).
   - The Authentik provider/application, with its redirect URI.
   - The Pangolin resource, if the app is public.
   - Any first-run setup in the app's UI.
6. **Deploy command:** `uv run ansible-playbook -v playbooks/<host>.yml --tags <role>`
7. **Commit command:** a `git cmt -m "Add and install <role>"` command in a bash block, with no AI attribution. Never commit yourself.

Then ask about the suggested deviations with AskUserQuestion, using `multiSelect: true`, one option per suggestion, at most four per question, and at most four questions per call. Apply only the ones selected. Re-run Phase 6 if anything changed.
