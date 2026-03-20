#!/usr/bin/env python3

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase
from ansible.utils.display import Display

display = Display()

CONTAINER_DICT_MERGE_TYPES = ["env", "labels",]
CONTAINER_LIST_MERGE_TYPES = ("secrets", "quadlet_options")


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        if variables is None:
            raise AnsibleError("container_config lookup requires access to variables")
        if len(terms) != 1:
            raise AnsibleError(
                "container_config lookup requires 1 argument: config_type"
            )

        self.set_options(var_options=variables, direct=kwargs)

        config_type = terms[0]
        container_name = kwargs.get("container", None)
        role_name = variables.get("role_name") or variables.get("ansible_role_name")
        if not role_name:
            raise AnsibleError(
                "container_config lookup could not determine the role name"
            )

        prefix = role_name
        if container_name is not None:
            prefix = f"{role_name}_{container_name}"

        if config_type in CONTAINER_DICT_MERGE_TYPES:
            return [self._merge_dicts(prefix, config_type, variables)]
        elif config_type in CONTAINER_LIST_MERGE_TYPES:
            return [self._merge_lists(prefix, config_type, variables)]
        else:
            raise AnsibleError(
                f"container_config lookup does not support config type: {config_type}. "
                f" Supported types are: {CONTAINER_DICT_MERGE_TYPES + CONTAINER_LIST_MERGE_TYPES}"
            )

    def _resolve(self, variables, var_name, default):
        raw = variables.get(var_name, default)
        return self._templar.template(raw)

    def _merge_dicts(self, prefix, config_type, variables):
        common = self._resolve(variables, f"common_container_{config_type}_defaults", {})
        defaults = self._resolve(variables, f"{prefix}_container_{config_type}_defaults", {})
        overrides = self._resolve(variables, f"{prefix}_container_{config_type}", {})

        display.vv(f"Resolving container config for {prefix} {config_type} with common: {common}, defaults: {defaults}, overrides: {overrides}")

        result = {}
        result.update(common)
        result.update(defaults)
        result.update(overrides)
        return result

    def _merge_lists(self, prefix, config_type, variables):
        common = self._resolve(variables, f"common_container_{config_type}_defaults", [])
        defaults = self._resolve(variables, f"{prefix}_container_{config_type}_defaults", [])
        overrides = self._resolve(variables, f"{prefix}_container_{config_type}", [])

        display.vv(f"Resolving container config for {prefix} {config_type} with common: {common}, defaults: {defaults}, overrides: {overrides}")

        return list(common) + list(defaults) + list(overrides)
