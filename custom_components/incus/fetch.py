"""Blocking calls against an Incus server (run these in an executor). Home-Assistant-independent."""
from __future__ import annotations

import logging
from typing import Iterable

from .data import VolumesData, build_pool, build_volume, instance_key
from .inventory import Inventory
from .incus_client import IncusError, models

_LOGGER = logging.getLogger(__name__)
ALL = "*"


def parse_projects(value: str | Iterable[str] | None) -> list[str]:
    """"default, web" -> ["default", "web"]; "*" (anywhere in the list) -> ["*"]."""
    if value is None:
        return ["default"]
    items = value.split(",") if isinstance(value, str) else list(value)
    names = [i.strip() for i in items if i and i.strip()]
    if ALL in names:
        return [ALL]
    return list(dict.fromkeys(names)) or ["default"]


def fetch_server(client) -> models.Server:
    return client.get_server()[0]


def fetch_instances(client, projects: list[str]) -> list[models.InstanceFull]:
    """All instances (with state) in the given projects (["*"] = every project)."""
    if projects == [ALL]:
        return client.get_instances_full_all_projects()
    out: list[models.InstanceFull] = []
    for p in projects:
        out += client.use_project(p).get_instances_full()
    return out


def fetch_volumes(client, projects: list[str], excluded_pools: frozenset[str] = frozenset(),
                  excluded_volumes: frozenset[str] = frozenset()) -> VolumesData:
    """Storage pools (space usage) and every *custom* volume (created by users, not instance/image volumes) with usage.

    Excluded pools/volumes are still listed (so changes are noticed) but cost no further requests."""
    pools: dict = {}
    volumes: dict = {}
    discovered_pools: set[str] = set()
    discovered_volumes: set[str] = set()
    for pool in client.get_storage_pools():
        pname = pool.name or ""
        discovered_pools.add(pname)
        pool_excluded = pname in excluded_pools
        resources = None
        if not pool_excluded:
            try:
                resources = client.get_storage_pool_resources(pname)
            except IncusError as e:
                _LOGGER.debug("No resources for pool %s: %s", pname, e)
        listed: list[models.StorageVolume] = []
        try:
            if projects == [ALL]:
                listed = client.get_storage_pool_volumes_all_projects(pname)
            else:
                for p in projects:
                    listed += client.use_project(p).get_storage_pool_volumes(pname)
        except IncusError as e:
            _LOGGER.warning("Could not list volumes of pool %s: %s", pname, e)
        count = 0
        for vol in listed:
            if vol.type != "custom":
                continue
            count += 1
            project = vol.project or (projects[0] if projects != [ALL] else "default")
            key = f"{pname}/{project}/{vol.name or ''}"
            discovered_volumes.add(key)
            if pool_excluded or key in excluded_volumes:
                continue
            try:
                state = client.use_project(project).get_storage_pool_volume_state(pname, "custom", vol.name or "")
            except IncusError as e:
                _LOGGER.debug("No state for volume %s/%s: %s", pname, vol.name, e)
                state = None
            snap = build_volume(pname, vol, state, project)
            volumes[snap.key] = snap
        if not pool_excluded:
            pools[pname] = build_pool(pool, resources, count)
    return VolumesData(pools=pools, volumes=volumes, discovered_pools=frozenset(discovered_pools),
                       discovered_volumes=frozenset(discovered_volumes))


def scan_inventory(client, projects: list[str]) -> Inventory:
    """Cheap listing of everything that exists (no state/usage calls): used by the options flow to offer a checklist."""
    keys: set[str] = set()
    if projects == [ALL]:
        listed = client.get_instances_all_projects()
    else:
        listed = []
        for p in projects:
            listed += [(i, p) for i in client.use_project(p).get_instances()]
    for item in listed:
        inst, fallback = item if isinstance(item, tuple) else (item, "default")
        keys.add(instance_key(inst.project or fallback, inst.name or ""))
    pools: set[str] = set()
    volumes: set[str] = set()
    for pool in client.get_storage_pools():
        pname = pool.name or ""
        pools.add(pname)
        vols = []
        try:
            if projects == [ALL]:
                vols = [(v, "default") for v in client.get_storage_pool_volumes_all_projects(pname)]
            else:
                for p in projects:
                    vols += [(v, p) for v in client.use_project(p).get_storage_pool_volumes(pname)]
        except IncusError as e:
            _LOGGER.warning("Could not list volumes of pool %s: %s", pname, e)
        for v, fallback in vols:
            if v.type == "custom":
                volumes.add(f"{pname}/{v.project or fallback}/{v.name or ''}")
    return Inventory(instances=frozenset(keys), pools=frozenset(pools), volumes=frozenset(volumes))
