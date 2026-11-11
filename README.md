# Homelab

This project is contains the automation for my personal homelab.

This is primarily built as a personal setup and not necessarily meant to be a reusable project for others, though I do try my best to make the roles as reusable for a few different setups as I can. Feel free to borrow from it. If you want to run it yourself, you'll need to write your own playbooks for the apps you want, set the variables in your own inventory, and create the secrets those variables reference (see [Secrets](#secrets)).

## How It's Built

- **Hosts** run Fedora Server, installed unattended from a generated kickstart file.
- **Apps** run as [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) units, so systemd manages each container, network and volume. Each app is an Ansible role in the in-tree collection `jaredhocutt.homelab`, and each one gets its own isolated network.
- **[Traefik](https://traefik.io/)** routes every app by hostname. **[Authentik](https://goauthentik.io/)** provides SSO, either through each app's native OIDC support or as Traefik forward-auth.
- **[Pangolin](https://github.com/fosrl/pangolin)** runs on a DigitalOcean droplet (`edge`). Each app that's public gets an outbound `newt` tunnel to it, so publishing an app doesn't mean opening a port at home.
- **Secrets** live in [Bitwarden Secrets Manager](https://bitwarden.com/products/secrets-manager/) and are fetched at runtime. Nothing secret is committed, apart from a few Ansible Vault files whose password is also in Bitwarden.

```
playbooks/        one playbook per host: apps, pta, edge, router, kickstart
inventory/        hosts.yml, group_vars/, and host_vars/<host>/<role>.yml
collections/ansible_collections/jaredhocutt/homelab/
  roles/          one role per app, plus server_setup, podman, common, ...
  plugins/lookup/ bws, container_config, traefik_labels
  extensions/molecule/  one test scenario per role
scripts/          helpers run through `uv run task ...`
```

## Setup

### Prerequisites

You'll need a macOS or Linux control node. On Windows, use WSL2, and keep the repo inside the Linux filesystem, not under `/mnt/c`. Install these tools:

- [uv](https://docs.astral.sh/uv/)
- the [`bws` CLI](https://bitwarden.com/help/secrets-manager-cli/) and `jq`, which fetch the vault password
- `skopeo`, for checking image tags
- `podman`, for running molecule tests
- GNU sed (`brew install gnu-sed` on macOS)

### Install dependencies

```bash
uv sync
uv run ansible-galaxy collection install -r collections/requirements.yml
```

`uv sync` installs the pinned ansible-core plus the dev tools (ansible-lint, molecule, taskipy). Run everything through `uv run` so those pinned versions are used.

### Secrets

Two environment variables must be set in your shell:

| Variable | Used by |
|---|---|
| `BWS_ACCESS_TOKEN` | the `bws` CLI and the `bws` lookup plugin |
| `BWS_ORGANIZATION_ID` | the `bws` lookup plugin |

How you set them is up to you. I load them with a `bws-env` shell function from my dotfiles.

There are two ways secrets are used:

- **Lookups.** Inventory and roles fetch secrets by name, e.g. `"{{ lookup('jaredhocutt.homelab.bws', 'karakeep_nextauth_secret') }}"`. Names follow these patterns:
  - `<role>_<thing>` for app secrets
  - `newt_<role>_*` for an app's Pangolin tunnel
  - `ansible_host_<host>` for host addresses
  - `homelab_smtp_*` for the shared mail settings

  To list the secrets an app needs, pull the lookup names out of its inventory file:

  ```bash
  grep -oE "bws', '[^']+'" inventory/host_vars/apps/karakeep.yml | cut -d"'" -f3 | sort -u
  ```

- **Ansible Vault.** `vault/vault-open.sh` fetches the vault password from the secret named `ansible_vault_password`. Set `ANSIBLE_VAULT_PASSWORD` to skip the lookup, or `ANSIBLE_VAULT_SECRET_NAME` to use a different secret name.

To check that secrets and SSH are working, ping a host:

```bash
uv run ansible apps -m ansible.builtin.ping
```

## Running playbooks

Each host has its own playbook, which sets up the server and then deploys its apps:

```bash
uv run ansible-playbook playbooks/apps.yml
```

| Playbook | What it does |
|---|---|
| `apps.yml`, `pta.yml` | Runs base server setup, then Traefik (plus Authentik on `apps`), then the apps |
| `edge.yml` | Creates the DigitalOcean droplet if it's missing, keeps its firewalls and wildcard DNS in sync, then sets up the droplet and deploys Pangolin |
| `router.yml` | Manages the pfSense DNS resolver entries |
| `kickstart.yml` | Renders a host's kickstart file; see [Installing a new host](#installing-a-new-host) |

Every role is tagged with its name, so you can deploy one app on its own:

```bash
uv run ansible-playbook -v playbooks/apps.yml --tags karakeep
```

## Installing a new host

1. Fill in `inventory/host_vars/<host>/kickstart.yml` with the host's hostname, network interfaces, disks and users. Copy an existing host's file as a starting point.
2. Render the kickstart file to `/tmp/ks.cfg`. Every host writes to the same path, so the playbook refuses to run unless `--limit` picks exactly one host:

   ```bash
   uv run ansible-playbook playbooks/kickstart.yml --limit <host>
   ```

3. Write it to a USB drive. `scripts/oemdrv.sh` (macOS only) partitions the drive with an `OEMDRV` volume and copies `ks.cfg` onto it.
4. Boot the Fedora Server installer with the USB drive plugged in. The installer finds the kickstart file on the `OEMDRV` volume and installs unattended.
5. Add the host's address to Bitwarden as `ansible_host_<host>`, then run the host's playbook. For a host that's new to this repo rather than a reinstall, first add it to `inventory/hosts.yml` and give it a playbook.

`edge` doesn't use kickstart, because `edge.yml` creates its droplet.

## Maintenance

| Task | Command |
|---|---|
| Check for newer image tags | `uv run task check-image-tags inventory/host_vars/apps` |
| Edit a vault file | `uv run task vault-edit <path>` |
| Lint | `uv run ansible-lint` |
| Test one role | `uv run task molecule test -s <role>` |

Each image tag in the inventory has a comment containing the `skopeo` command that lists its upstream tags, and `check-image-tags` runs those commands. A tag can be held back with a `# hold:` marker, and [CLAUDE.md](CLAUDE.md#image-tag-updates) explains how.

With [Claude Code](https://claude.com/claude-code), `/update-image-tags <host>` applies the updates and writes out the deploy and commit commands.

## Adding a new app

- **With Claude Code:** run `/new-role <app> [compose URL]`. It researches the app, builds the role from the closest existing one, wires up the inventory, playbook and tests, and lists any places it would deviate from the upstream compose file so you can decide on each.
- **By hand:** `uv run task duplicate-role <existing_role> <new_role>` copies a role and renames its variables. [CLAUDE.md](CLAUDE.md) documents the conventions a role should follow.

## License

[MIT](LICENSE)
