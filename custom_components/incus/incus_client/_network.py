from __future__ import annotations

from typing import Any

from . import models
from ._core import Core, E, Q
from .operations import Operation
from .transport import HTTPStream


class NetworkMixin(Core):
    # ---- networks --------------------------------------------------------------------------
    def get_network_names(self) -> list[str]:
        return self._names("/networks")

    def get_networks(self) -> list[models.Network]:
        return self._get_list(models.Network, "/networks", Q(("recursion", "1")))

    def get_network(self, name: str) -> tuple[models.Network, str]:
        return self._get_e(models.Network, f"/networks/{E(name)}")

    def get_network_leases(self, name: str) -> list[models.NetworkLease]:
        return self._get_list(models.NetworkLease, f"/networks/{E(name)}/leases")

    def get_network_state(self, name: str) -> models.NetworkState:
        return self._get(models.NetworkState, f"/networks/{E(name)}/state")

    def get_network_allocations(self, all_projects: bool) -> list[models.NetworkAllocations]:
        return self._get_list(models.NetworkAllocations, "/network-allocations", None, all_projects)

    def create_network(self, network: models.NetworksPost) -> None:
        self._send("POST", "/networks", network)

    def update_network(self, name: str, network: models.NetworkPut, etag: str) -> None:
        self._send("PUT", f"/networks/{E(name)}", network, etag)

    def rename_network(self, name: str, network: models.NetworkPost) -> None:
        self._send("POST", f"/networks/{E(name)}", network)

    def delete_network(self, name: str) -> None:
        self._send("DELETE", f"/networks/{E(name)}")

    # ---- ACLs ------------------------------------------------------------------------------
    def get_network_acl_names(self) -> list[str]:
        return self._names("/network-acls")

    def get_network_acls(self) -> list[models.NetworkACL]:
        return self._get_list(models.NetworkACL, "/network-acls", Q(("recursion", "1")))

    def get_network_acl(self, name: str) -> tuple[models.NetworkACL, str]:
        return self._get_e(models.NetworkACL, f"/network-acls/{E(name)}")

    def get_network_acl_logfile(self, name: str) -> HTTPStream:
        return self._raw_stream(f"/network-acls/{E(name)}/log")

    def create_network_acl(self, acl: models.NetworkACLsPost) -> None:
        self._send("POST", "/network-acls", acl)

    def update_network_acl(self, name: str, acl: models.NetworkACLPut, etag: str) -> None:
        self._send("PUT", f"/network-acls/{E(name)}", acl, etag)

    def rename_network_acl(self, name: str, acl: models.NetworkACLPost) -> None:
        self._send("POST", f"/network-acls/{E(name)}", acl)

    def delete_network_acl(self, name: str) -> None:
        self._send("DELETE", f"/network-acls/{E(name)}")

    # ---- forwards, load balancers, peers (all scoped to a network) -------------------------
    def get_network_forward_addresses(self, network_name: str) -> list[str]:
        return self._names(f"/networks/{E(network_name)}/forwards")

    def get_network_forwards(self, network_name: str) -> list[models.NetworkForward]:
        return self._get_list(models.NetworkForward, f"/networks/{E(network_name)}/forwards", Q(("recursion", "1")))

    def get_network_forward(self, network_name: str, listen_address: str) -> tuple[models.NetworkForward, str]:
        return self._get_e(models.NetworkForward, f"/networks/{E(network_name)}/forwards/{E(listen_address)}")

    def create_network_forward(self, network_name: str, forward: models.NetworkForwardsPost) -> None:
        self._send("POST", f"/networks/{E(network_name)}/forwards", forward)

    def update_network_forward(self, network_name: str, listen_address: str, forward: models.NetworkForwardPut, etag: str) -> None:
        self._send("PUT", f"/networks/{E(network_name)}/forwards/{E(listen_address)}", forward, etag)

    def delete_network_forward(self, network_name: str, listen_address: str) -> None:
        self._send("DELETE", f"/networks/{E(network_name)}/forwards/{E(listen_address)}")

    def get_network_load_balancer_addresses(self, network_name: str) -> list[str]:
        return self._names(f"/networks/{E(network_name)}/load-balancers")

    def get_network_load_balancers(self, network_name: str) -> list[models.NetworkLoadBalancer]:
        return self._get_list(models.NetworkLoadBalancer, f"/networks/{E(network_name)}/load-balancers", Q(("recursion", "1")))

    def get_network_load_balancer(self, network_name: str, listen_address: str) -> tuple[models.NetworkLoadBalancer, str]:
        return self._get_e(models.NetworkLoadBalancer, f"/networks/{E(network_name)}/load-balancers/{E(listen_address)}")

    def create_network_load_balancer(self, network_name: str, load_balancer: models.NetworkLoadBalancersPost) -> None:
        self._send("POST", f"/networks/{E(network_name)}/load-balancers", load_balancer)

    def update_network_load_balancer(self, network_name: str, listen_address: str, load_balancer: models.NetworkLoadBalancerPut, etag: str) -> None:
        self._send("PUT", f"/networks/{E(network_name)}/load-balancers/{E(listen_address)}", load_balancer, etag)

    def delete_network_load_balancer(self, network_name: str, listen_address: str) -> None:
        self._send("DELETE", f"/networks/{E(network_name)}/load-balancers/{E(listen_address)}")

    def get_network_peer_names(self, network_name: str) -> list[str]:
        return self._names(f"/networks/{E(network_name)}/peers")

    def get_network_peers(self, network_name: str) -> list[models.NetworkPeer]:
        return self._get_list(models.NetworkPeer, f"/networks/{E(network_name)}/peers", Q(("recursion", "1")))

    def get_network_peer(self, network_name: str, peer_name: str) -> tuple[models.NetworkPeer, str]:
        return self._get_e(models.NetworkPeer, f"/networks/{E(network_name)}/peers/{E(peer_name)}")

    def create_network_peer(self, network_name: str, peer: models.NetworkPeersPost) -> None:
        self._send("POST", f"/networks/{E(network_name)}/peers", peer)

    def update_network_peer(self, network_name: str, peer_name: str, peer: models.NetworkPeerPut, etag: str) -> None:
        self._send("PUT", f"/networks/{E(network_name)}/peers/{E(peer_name)}", peer, etag)

    def delete_network_peer(self, network_name: str, peer_name: str) -> None:
        self._send("DELETE", f"/networks/{E(network_name)}/peers/{E(peer_name)}")

    # ---- zones and records -----------------------------------------------------------------
    def get_network_zone_names(self) -> list[str]:
        return self._names("/network-zones")

    def get_network_zones(self) -> list[models.NetworkZone]:
        return self._get_list(models.NetworkZone, "/network-zones", Q(("recursion", "1")))

    def get_network_zone(self, name: str) -> tuple[models.NetworkZone, str]:
        return self._get_e(models.NetworkZone, f"/network-zones/{E(name)}")

    def create_network_zone(self, zone: models.NetworkZonesPost) -> None:
        self._send("POST", "/network-zones", zone)

    def update_network_zone(self, name: str, zone: models.NetworkZonePut, etag: str) -> None:
        self._send("PUT", f"/network-zones/{E(name)}", zone, etag)

    def delete_network_zone(self, name: str) -> None:
        self._send("DELETE", f"/network-zones/{E(name)}")

    def get_network_zone_record_names(self, zone: str) -> list[str]:
        return self._names(f"/network-zones/{E(zone)}/records")

    def get_network_zone_records(self, zone: str) -> list[models.NetworkZoneRecord]:
        return self._get_list(models.NetworkZoneRecord, f"/network-zones/{E(zone)}/records", Q(("recursion", "1")))

    def get_network_zone_record(self, zone: str, name: str) -> tuple[models.NetworkZoneRecord, str]:
        return self._get_e(models.NetworkZoneRecord, f"/network-zones/{E(zone)}/records/{E(name)}")

    def create_network_zone_record(self, zone: str, record: models.NetworkZoneRecordsPost) -> None:
        self._send("POST", f"/network-zones/{E(zone)}/records", record)

    def update_network_zone_record(self, zone: str, name: str, record: models.NetworkZoneRecordPut, etag: str) -> None:
        self._send("PUT", f"/network-zones/{E(zone)}/records/{E(name)}", record, etag)

    def delete_network_zone_record(self, zone: str, name: str) -> None:
        self._send("DELETE", f"/network-zones/{E(zone)}/records/{E(name)}")

    # ---- integrations ----------------------------------------------------------------------
    def get_network_integration_names(self) -> list[str]:
        return self._names("/network-integrations")

    def get_network_integrations(self) -> list[models.NetworkIntegration]:
        return self._get_list(models.NetworkIntegration, "/network-integrations", Q(("recursion", "1")))

    def get_network_integration(self, name: str) -> tuple[models.NetworkIntegration, str]:
        return self._get_e(models.NetworkIntegration, f"/network-integrations/{E(name)}")

    def create_network_integration(self, integration: models.NetworkIntegrationsPost) -> None:
        self._send("POST", "/network-integrations", integration)

    def update_network_integration(self, name: str, integration: models.NetworkIntegrationPut, etag: str) -> None:
        self._send("PUT", f"/network-integrations/{E(name)}", integration, etag)

    def rename_network_integration(self, name: str, network: models.NetworkIntegrationPost) -> None:
        self._send("POST", f"/network-integrations/{E(name)}", network)

    def delete_network_integration(self, name: str) -> None:
        self._send("DELETE", f"/network-integrations/{E(name)}")
