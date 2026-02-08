from __future__ import annotations

from ansible.errors import AnsibleError
from ansible.plugins.lookup import LookupBase


class LookupModule(LookupBase):
    def run(self, terms, variables=None, **kwargs):
        """
        Generate Traefik labels for a container.

        Args:
            terms: List of positional arguments [name, host, port]
            entrypoint: (optional) Traefik entrypoint name (default: websecure)
            network: (optional) Docker network name (default: traefik)

        Usage:
            lookup('jaredhocutt.homelab.traefik_labels', 'myapp', 'myapp.example.com', '8080')
            lookup('jaredhocutt.homelab.traefik_labels', name, host, port, entrypoint='web')
        """
        if len(terms) < 3:
            raise AnsibleError('traefik_labels lookup requires 3 arguments: name, host, port')

        name = terms[0]
        host = terms[1]
        port = terms[2]
        entrypoint = kwargs.get('entrypoint', 'websecure')
        network = kwargs.get('network', 'traefik')

        labels = {
            'traefik.enable': 'true',
            'traefik.docker.network': network,
            f'traefik.http.routers.{name}.rule': f'Host(`{host}`)',
            f'traefik.http.routers.{name}.entrypoints': entrypoint,
            f'traefik.http.services.{name}.loadbalancer.server.port': str(port)
        }

        return [labels]  # Lookups must return lists
