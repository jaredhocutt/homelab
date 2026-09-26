#!/usr/bin/env python3

from __future__ import absolute_import, division, print_function

__metaclass__ = type

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase


# Identity headers the Authentik outpost adds (authResponseHeaders in the traefik
# role's authentik.yaml)
AUTHENTIK_HEADERS = [
    "X-Authentik-Username",
    "X-Authentik-Groups",
    "X-Authentik-Email",
    "X-Authentik-Name",
    "X-Authentik-Uid",
    "X-Authentik-Jwt",
    "X-Authentik-Meta-Jwks",
    "X-Authentik-Meta-Outpost",
    "X-Authentik-Meta-Provider",
    "X-Authentik-Meta-App",
    "X-Authentik-Meta-Version",
]


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
        noauth_paths = kwargs.get("noauth_paths") or []

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

            if noauth_paths:
                # Paths that must work without a browser login (API, webhooks,
                # pings) get their own high-priority router that skips
                # forward-auth and goes straight to the app.
                prefixes = " || ".join(f"PathPrefix(`{path}`)" for path in noauth_paths)
                labels[f"traefik.http.routers.{name}-noauth.rule"] = (
                    f"Host(`{host}`) && ({prefixes})"
                )
                labels[f"traefik.http.routers.{name}-noauth.entrypoints"] = entrypoint
                labels[f"traefik.http.routers.{name}-noauth.service"] = name
                labels[f"traefik.http.routers.{name}-noauth.priority"] = "1000"
                labels[f"traefik.http.routers.{name}-noauth.middlewares"] = (
                    f"{name}-strip-auth-headers"
                )
                # The noauth router bypasses Authentik, so strip any
                # client-supplied identity header an app might trust (e.g.
                # REMOTE_USER_HEADER). The underscore spelling is stripped too,
                # since WSGI and others map it to the same key.
                for header in AUTHENTIK_HEADERS:
                    for spelling in (header, header.replace("-", "_")):
                        labels[
                            f"traefik.http.middlewares.{name}-strip-auth-headers"
                            f".headers.customrequestheaders.{spelling}"
                        ] = ""

        return [labels]  # Lookups must return lists
