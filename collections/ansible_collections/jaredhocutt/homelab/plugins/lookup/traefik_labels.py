#!/usr/bin/env python3

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        if len(terms) < 3:
            raise AnsibleError(
                "traefik_labels lookup requires 3 arguments: name, host, port"
            )

        name = terms[0]
        host = terms[1]
        port = terms[2]
        entrypoint = kwargs.get("entrypoint", "websecure")
        network = kwargs.get("network", "traefik")
        auth = kwargs.get("auth", False)

        labels = {
            "traefik.enable": "true",
            "traefik.docker.network": network,
            f"traefik.http.routers.{name}.rule": f"Host(`{host}`)",
            f"traefik.http.routers.{name}.entrypoints": entrypoint,
            f"traefik.http.services.{name}.loadbalancer.server.port": str(port),
        }

        if auth:
            # Gate this router behind the Authentik forward-auth middleware
            # (defined in the traefik role's authentik.yaml dynamic config).
            labels[f"traefik.http.routers.{name}.middlewares"] = "authentik@file"
            # The outpost callback/start paths must be served by the outpost,
            # not the backend app, so route them to the Authentik service. The
            # longer PathPrefix rule outranks the bare Host rule by rule length.
            labels[f"traefik.http.routers.{name}-authentik.rule"] = (
                f"Host(`{host}`) && PathPrefix(`/outpost.goauthentik.io/`)"
            )
            labels[f"traefik.http.routers.{name}-authentik.entrypoints"] = entrypoint
            labels[f"traefik.http.routers.{name}-authentik.service"] = "authentik@file"

        return [labels]  # Lookups must return lists
