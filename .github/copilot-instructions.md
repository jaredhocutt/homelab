# Copilot Instructions for Homelab

## Project Overview
This is an Ansible-based homelab infrastructure project that deploys self-hosted applications via **Podman Quadlets** on Fedora servers. The project uses a custom Ansible collection (`jaredhocutt.homelab`) containing 30+ roles for various applications.

## Architecture

### Inventory Structure
- **Hosts**: `localhost`, `router`, `apps`, `media` — defined in [inventory/hosts.yml](../inventory/hosts.yml)
- **Group vars**: `inventory/group_vars/` for shared config, `inventory/host_vars/` for per-host overrides
- **Secrets**: All sensitive values use `bitwarden.secrets.lookup` with UUIDs stored in `bws_uuid.*` variables

### Role Pattern (Critical)
Every application role in `collections/ansible_collections/jaredhocutt/homelab/roles/` follows this structure:
```
<role_name>/
├── defaults/main.yml    # User-overridable variables (e.g., <app>_image_tag, <app>_traefik_host)
├── vars/main.yml        # Internal variables (*_base suffix) - DO NOT override
├── tasks/main.yml       # Main task flow
├── handlers/main.yml    # Restart handlers
└── templates/           # Jinja2 templates if needed
```

### Variable Naming Convention
- `<app>_name`: Container/service name (e.g., `traefik_name: traefik`)
- `<app>_image` / `<app>_image_tag`: Container image and version
- `<app>_volume_*`: Named volumes
- `<app>_env_vars`: User environment variables (merged with `<app>_env_vars_base`)
- `<app>_secrets` / `<app>_container_secrets`: Podman secrets
- `<app>_traefik_enable` / `<app>_traefik_host`: Traefik reverse proxy config
- `<app>_quadlet_options`: Additional systemd quadlet options

**Base variables** (suffix `_base`) are defined in `vars/main.yml` and merged with user-provided values. Never define `*_base` variables in `defaults/`.

### Traefik Integration
All web applications integrate with Traefik via container labels. The pattern in `vars/main.yml`:
```yaml
<app>_container_labels_traefik:
  - key: traefik.enable
    value: "true"
  - key: traefik.http.routers.{{ <app>_name }}.rule
    value: Host(`{{ <app>_traefik_host }}`)
  - key: traefik.http.routers.{{ <app>_name }}.entrypoints
    value: "{{ common_traefik_entrypoint }}"
```

## Key Commands
```bash
# Run playbook for a host
ansible-playbook playbooks/apps.yml

# Run specific role via tag
ansible-playbook playbooks/apps.yml --tags traefik

# Build execution environment
poetry run task build-ee

# Lint with ansible-lint
poetry run ansible-lint
```

## Creating a New Role

1. Follow the existing pattern from [traefik role](../collections/ansible_collections/jaredhocutt/homelab/roles/traefik/) or [n8n role](../collections/ansible_collections/jaredhocutt/homelab/roles/n8n/)
2. Standard task order in `tasks/main.yml`:
   - Pull container images (`containers.podman.podman_image`)
   - Create network (`containers.podman.podman_network` with `state: quadlet`)
   - Create volumes (`containers.podman.podman_volume` with `state: quadlet`)
   - Create secrets (`containers.podman.podman_secret`)
   - Create containers (`containers.podman.podman_container` with `state: quadlet`)
3. Add role to appropriate playbook in `playbooks/` with matching tag
4. Define host-specific overrides (image tags, secrets) in `inventory/host_vars/<host>.yml`

## Dependencies
- **Python**: 3.12 (managed via Poetry)
- **Collections**: See [collections/requirements.yml](../collections/requirements.yml) — notably `containers.podman`, `bitwarden.secrets`
- **Vault**: GPG-encrypted vault password at `vault/vault-pass.gpg`, auto-decrypted via [vault/vault-open.sh](../vault/vault-open.sh)
