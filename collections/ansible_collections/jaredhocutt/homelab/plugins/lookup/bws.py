#!/usr/bin/env python3

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = """
name: bws
author:
  - Jared Hocutt (@jaredhocutt)
short_description: Lookup secrets from Bitwarden Secrets Manager by name
description:
  - This lookup resolves a Bitwarden secret by its name (key) instead of UUID.
  - It lists all secrets in the organization to find the matching UUID, then
    delegates to the C(bitwarden.secrets.lookup) plugin to fetch the value.
  - The name-to-UUID mapping is cached for the duration of the Ansible run.
options:
  _terms:
    description: Secret name (key) to lookup.
    required: true
  organization_id:
    description: Bitwarden organization UUID.
    default: $BWS_ORGANIZATION_ID
    required: true
    type: string
    env:
      - name: BWS_ORGANIZATION_ID
  access_token:
    description: Access token to use.
    default: $BWS_ACCESS_TOKEN
    env:
      - name: BWS_ACCESS_TOKEN
    required: true
    type: string
  base_url:
    description: Base URL for the Bitwarden instance.
    default: https://vault.bitwarden.com
    required: false
    type: string
  api_url:
    description: API URL. If provided, identity_url must also be provided.
    default: https://api.bitwarden.com
    required: false
    type: string
  identity_url:
    description: Identity URL. If provided, api_url must also be provided.
    default: https://identity.bitwarden.com
    required: false
    type: string
  state_file_dir:
    description: Directory to store state file for authentication.
    default: ~/.config/bitwarden-sm-ansible
    required: false
    type: string
  field:
    description: Field to return from the secret.
    default: value
    required: false
    type: string
"""

EXAMPLES = """
- name: Lookup a secret by name
  ansible.builtin.debug:
    msg: "{{ lookup('jaredhocutt.homelab.bws', 'my-secret-name') }}"

- name: Lookup a secret with explicit organization ID
  ansible.builtin.debug:
    msg: "{{ lookup('jaredhocutt.homelab.bws', 'my-secret-name', organization_id='<org-uuid>') }}"
"""

RETURN = """
_list:
  description: Value of the secret
  type: list
  elements: str
"""

import os

from ansible.errors import AnsibleError, AnsibleLookupError
from ansible.plugins.loader import lookup_loader
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

try:
    from bitwarden_sdk import BitwardenClient, DeviceType, client_settings_from_dict
except ImportError as e:
    raise AnsibleError(
        "The jaredhocutt.homelab.bws lookup plugin requires the 'bitwarden-sdk' "
        "python package."
    ) from e


BITWARDEN_BASE_URL = "https://vault.bitwarden.com"


class LookupModule(LookupBase):
    # Class-level cache: (access_token, org_id) -> {name: uuid_str}
    _name_cache = {}

    def run(self, terms, variables=None, **kwargs):
        self.set_options(var_options=variables, direct=kwargs)

        if not terms:
            raise AnsibleError("No secret name provided")
        secret_name = terms[0]

        organization_id = self.get_option("organization_id") or os.getenv("BWS_ORGANIZATION_ID")
        if not organization_id:
            raise AnsibleError(
                "organization_id is required. Set BWS_ORGANIZATION_ID or pass "
                "organization_id as a keyword argument."
            )

        access_token = self.get_option("access_token") or os.getenv("BWS_ACCESS_TOKEN")
        if not access_token:
            raise AnsibleError(
                "access_token is required. Set BWS_ACCESS_TOKEN or pass "
                "access_token as a keyword argument."
            )

        name_map = self._get_name_map(access_token, organization_id)

        if secret_name not in name_map:
            raise AnsibleLookupError(
                f"No secret found with name '{secret_name}'. "
                f"Ensure the secret exists and the machine account has access to it."
            )

        secret_uuid = name_map[secret_name]
        display.vv(f"bws: resolved '{secret_name}' -> {secret_uuid}")

        # Delegate to bitwarden.secrets.lookup with the resolved UUID
        bws_lookup = lookup_loader.get("bitwarden.secrets.lookup")
        if bws_lookup is None:
            raise AnsibleError(
                "Could not load bitwarden.secrets.lookup plugin. "
                "Ensure the bitwarden.secrets collection is installed."
            )

        # Pass through all relevant kwargs
        lookup_kwargs = {}
        for opt in (
            "access_token",
            "base_url",
            "api_url",
            "identity_url",
            "state_file_dir",
            "field",
        ):
            val = self.get_option(opt)
            if val is not None:
                lookup_kwargs[opt] = val

        return bws_lookup.run([secret_uuid], variables=variables, **lookup_kwargs)

    def _get_name_map(self, access_token, organization_id):
        cache_key = (access_token, organization_id)

        if cache_key in LookupModule._name_cache:
            display.vv("bws: using cached name-to-UUID mapping")
            return LookupModule._name_cache[cache_key]

        display.vv("bws: building name-to-UUID mapping via secrets().list()")

        base_url = self.get_option("base_url") or BITWARDEN_BASE_URL
        api_url = self.get_option("api_url")
        identity_url = self.get_option("identity_url")
        state_file_dir = self.get_option("state_file_dir")

        # Derive API/Identity URLs from base_url if it's not the default
        if base_url != BITWARDEN_BASE_URL:
            api_url = f"{base_url.rstrip('/')}/api"
            identity_url = f"{base_url.rstrip('/')}/identity"

        client = BitwardenClient(
            client_settings_from_dict(
                {
                    "apiUrl": api_url,
                    "deviceType": DeviceType.SDK,
                    "identityUrl": identity_url,
                    "userAgent": "bitwarden/sm-ansible",
                }
            )
        )

        # Authenticate
        state_file_dir = os.path.expanduser(state_file_dir)
        os.makedirs(state_file_dir, exist_ok=True)

        # Extract access_token_id for state file naming (matches upstream pattern)
        try:
            first_part = access_token.split(":")[0]
            access_token_id = first_part.split(".")[1]
        except (IndexError, ValueError):
            access_token_id = "default"

        state_file = os.path.join(state_file_dir, access_token_id)

        try:
            try:
                client.auth().login_access_token(access_token, state_file)
            except AttributeError:
                client.access_token_login(access_token, state_file)
        except Exception as e:
            raise AnsibleError(f"Failed to login with access token: {e}") from e

        # List all secrets
        try:
            response = client.secrets().list(organization_id)
        except Exception as e:
            raise AnsibleError(
                f"Failed to list secrets for organization '{organization_id}': {e}"
            ) from e

        if not response.data:
            raise AnsibleError(
                f"No secrets returned for organization '{organization_id}'. "
                f"Ensure the machine account has access."
            )

        name_map = {}
        for secret in response.data.data:
            name = secret.key
            uuid_str = str(secret.id)
            if name in name_map:
                display.warning(
                    f"bws: duplicate secret name '{name}' found "
                    f"(UUIDs: {name_map[name]}, {uuid_str}). Using first occurrence."
                )
                continue
            name_map[name] = uuid_str

        display.vv(f"bws: cached {len(name_map)} secret name(s)")
        LookupModule._name_cache[cache_key] = name_map
        return name_map
