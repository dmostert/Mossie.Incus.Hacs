"""Base entities and the dynamic-entity helper shared by all platforms."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IncusInstancesCoordinator, IncusVolumesCoordinator
from .data import InstanceSnapshot, PoolSnapshot, ServerSnapshot, VolumeSnapshot


def server_identifier(entry_id: str) -> str:
    return f"{entry_id}:server"


def instance_identifier(entry_id: str, key: str) -> str:
    return f"{entry_id}:inst:{key}"


def pool_identifier(entry_id: str, pool: str) -> str:
    return f"{entry_id}:pool:{pool}"


def volume_identifier(entry_id: str, key: str) -> str:
    return f"{entry_id}:vol:{key}"


def server_device_info(entry_id: str, server: ServerSnapshot) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, server_identifier(entry_id))}, name=f"Incus {server.name}", manufacturer="Incus",
                      model="Incus server", sw_version=server.version)


class IncusServerEntity(CoordinatorEntity[IncusInstancesCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IncusInstancesCoordinator, entity_key: str) -> None:
        super().__init__(coordinator)
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_server_{entity_key}"
        self._attr_device_info = server_device_info(entry_id, coordinator.data.server)

    def _snapshot(self) -> ServerSnapshot | None:
        return self.coordinator.data.server if self.coordinator.data else None


class IncusInstanceEntity(CoordinatorEntity[IncusInstancesCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IncusInstancesCoordinator, key: str, entity_key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        entry_id = coordinator.config_entry.entry_id
        snap = coordinator.data.instances[key]
        self.project, self.instance_name = snap.project, snap.name
        self._attr_unique_id = f"{entry_id}_{key}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, instance_identifier(entry_id, key))}, name=snap.display_name, manufacturer="Incus",
            model="Virtual machine" if snap.type == "virtual-machine" else "Container",
            via_device=(DOMAIN, server_identifier(entry_id)), sw_version=snap.image)

    def _snapshot(self) -> InstanceSnapshot | None:
        return self.coordinator.data.instances.get(self._key) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self._snapshot() is not None


class IncusPoolEntity(CoordinatorEntity[IncusVolumesCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IncusVolumesCoordinator, pool: str, entity_key: str) -> None:
        super().__init__(coordinator)
        self._pool = pool
        entry_id = coordinator.config_entry.entry_id
        p = coordinator.data.pools[pool]
        self._attr_unique_id = f"{entry_id}_pool_{pool}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, pool_identifier(entry_id, pool))}, name=f"Storage pool {pool}", manufacturer="Incus",
            model=f"Storage pool ({p.driver})" if p.driver else "Storage pool", via_device=(DOMAIN, server_identifier(entry_id)))

    def _snapshot(self) -> PoolSnapshot | None:
        return self.coordinator.data.pools.get(self._pool) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self._snapshot() is not None


class IncusVolumeEntity(CoordinatorEntity[IncusVolumesCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: IncusVolumesCoordinator, key: str, entity_key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        entry_id = coordinator.config_entry.entry_id
        v = coordinator.data.volumes[key]
        label = v.name if v.project == "default" else f"{v.project}/{v.name}"
        self._attr_unique_id = f"{entry_id}_vol_{key}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, volume_identifier(entry_id, key))}, name=f"Volume {label} ({v.pool})", manufacturer="Incus",
            model="Storage volume", via_device=(DOMAIN, pool_identifier(entry_id, v.pool)))

    def _snapshot(self) -> VolumeSnapshot | None:
        return self.coordinator.data.volumes.get(self._key) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        return super().available and self._snapshot() is not None


class DescribedMixin:
    """native_value / attributes from ``entity_description.value_fn`` / ``attrs_fn`` applied to the entity's snapshot."""
    entity_description: Any
    # _snapshot() comes from the Incus*Entity base class; do not define it here (this mixin sits first in the MRO).

    @property
    def native_value(self):
        snap = self._snapshot()
        return None if snap is None else self.entity_description.value_fn(snap)

    @property
    def extra_state_attributes(self):
        fn = getattr(self.entity_description, "attrs_fn", None)
        snap = self._snapshot()
        return fn(snap) if fn is not None and snap is not None else None


def async_add_dynamic(entry: ConfigEntry, coordinator: Any, async_add_entities: Callable[[list], None],
                      factory: Callable[[Any], Iterable[tuple[str, Callable[[], Any]]]]) -> None:
    """Adds entities now and whenever the coordinator discovers new ones (new instances, NICs, disks, volumes).

    ``factory(data)`` yields ``(unique_id, builder)``; ``builder()`` creates the entity only if the id is new."""
    known: set[str] = set()

    @callback
    def _check() -> None:
        if coordinator.data is None:
            return
        new = []
        for uid, build in factory(coordinator.data):
            if uid not in known:
                known.add(uid)
                new.append(build())
        if new:
            async_add_entities(new)

    _check()
    entry.async_on_unload(coordinator.async_add_listener(_check))
