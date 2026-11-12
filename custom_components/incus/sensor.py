"""Sensors: instance state (CPU, memory, disk, network ...), server summary, storage pools and custom volumes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant

from .coordinator import IncusRuntimeData
from .data import InstanceSnapshot
from .entity import (DescribedMixin, IncusInstanceEntity, IncusPoolEntity, IncusServerEntity, IncusVolumeEntity,
                     async_add_dynamic)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class IncusSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any]] | None = None


_BYTES = dict(device_class=SensorDeviceClass.DATA_SIZE, native_unit_of_measurement=UnitOfInformation.BYTES)


def _seconds(ns: int | None) -> float | None:
    return None if ns is None else round(ns / 1e9, 3)


# ---- instances -------------------------------------------------------------------------------
INSTANCE_SENSORS: tuple[IncusSensorDescription, ...] = (
    IncusSensorDescription(
        key="status", name="Status", icon="mdi:server", value_fn=lambda s: s.status,
        attrs_fn=lambda s: {"project": s.project, "type": s.type, "architecture": s.architecture, "location": s.location,
                            "description": s.description, "profiles": list(s.profiles), "image": s.image, **s.limits}),
    IncusSensorDescription(
        key="cpu_time", name="CPU time", device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING, suggested_display_precision=1, value_fn=lambda s: _seconds(s.cpu_usage_ns),
        attrs_fn=lambda s: {"allocated_time_s": _seconds(s.cpu_allocated_ns)}),
    IncusSensorDescription(
        key="cpu_usage", name="CPU usage", icon="mdi:cpu-64-bit", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value_fn=lambda s: s.cpu_percent,
        attrs_fn=lambda s: {"note": "Percent of one CPU core since the previous poll; can exceed 100 on multi-core instances"}),
    IncusSensorDescription(
        key="memory_usage", name="Memory usage", state_class=SensorStateClass.MEASUREMENT,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=0, value_fn=lambda s: s.memory_usage,
        attrs_fn=lambda s: {"memory_total_bytes": s.memory_total}, **_BYTES),
    IncusSensorDescription(
        key="memory_peak", name="Memory peak", state_class=SensorStateClass.MEASUREMENT,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=0,
        entity_registry_enabled_default=False, value_fn=lambda s: s.memory_peak, **_BYTES),
    IncusSensorDescription(
        key="memory_percent", name="Memory usage percent", icon="mdi:memory", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value_fn=lambda s: s.memory_percent,
        attrs_fn=lambda s: {"note": "Relative to the instance's memory limit, or the host's memory if there is no limit"}),
    IncusSensorDescription(
        key="swap_usage", name="Swap usage", state_class=SensorStateClass.MEASUREMENT,
        suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=0,
        entity_registry_enabled_default=False, value_fn=lambda s: s.swap_usage, **_BYTES),
    IncusSensorDescription(
        key="processes", name="Processes", icon="mdi:application-cog", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.processes),
    IncusSensorDescription(
        key="uptime", name="Started", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.started_at if s.is_running else None),
    IncusSensorDescription(
        key="ip_address", name="IP address", icon="mdi:ip-network", value_fn=lambda s: s.ipv4,
        attrs_fn=lambda s: {"ipv6": s.ipv6, "interfaces": {n: list(nic.addresses) for n, nic in s.networks.items()}}),
    IncusSensorDescription(key="ipv6_address", name="IPv6 address", entity_registry_enabled_default=False, value_fn=lambda s: s.ipv6),
    IncusSensorDescription(
        key="last_used", name="Last used", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False, value_fn=lambda s: s.last_used_at),
    IncusSensorDescription(
        key="pid", name="PID", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, value_fn=lambda s: s.pid),
)


def instance_dynamic_descriptions(snap: InstanceSnapshot) -> list[IncusSensorDescription]:
    """Per-disk and per-NIC sensors, created as the devices appear in the instance's state."""
    out: list[IncusSensorDescription] = []
    for disk in snap.disks:
        out.append(IncusSensorDescription(
            key=f"disk_{disk}", name=f"Disk {disk} usage", state_class=SensorStateClass.MEASUREMENT,
            suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=0,
            value_fn=lambda s, d=disk: s.disks[d].usage if d in s.disks else None,
            attrs_fn=lambda s, d=disk: {"total_bytes": s.disks[d].total} if d in s.disks else None, **_BYTES))
    for nic, n in snap.networks.items():
        if nic == "lo" or n.type == "loopback":
            continue
        attrs = lambda s, nn=nic: ({"mac": s.networks[nn].hwaddr, "state": s.networks[nn].state, "mtu": s.networks[nn].mtu,
                                    "host_interface": s.networks[nn].host_name, "addresses": list(s.networks[nn].addresses)}
                                   if nn in s.networks else None)
        out.append(IncusSensorDescription(
            key=f"net_{nic}_rx", name=f"{nic} received", state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=1,
            value_fn=lambda s, nn=nic: s.networks[nn].rx_bytes if nn in s.networks else None, attrs_fn=attrs, **_BYTES))
        out.append(IncusSensorDescription(
            key=f"net_{nic}_tx", name=f"{nic} sent", state_class=SensorStateClass.TOTAL_INCREASING,
            suggested_unit_of_measurement=UnitOfInformation.MEGABYTES, suggested_display_precision=1,
            value_fn=lambda s, nn=nic: s.networks[nn].tx_bytes if nn in s.networks else None, **_BYTES))
    return out


# ---- server ----------------------------------------------------------------------------------
SERVER_SENSORS: tuple[IncusSensorDescription, ...] = (
    IncusSensorDescription(key="instances_total", name="Instances", icon="mdi:server-network",
                           state_class=SensorStateClass.MEASUREMENT, value_fn=lambda s: s.instances_total),
    IncusSensorDescription(key="instances_running", name="Running instances", icon="mdi:server-network",
                           state_class=SensorStateClass.MEASUREMENT, value_fn=lambda s: s.instances_running),
    IncusSensorDescription(key="version", name="Version", entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda s: s.version,
                           attrs_fn=lambda s: {"api_version": s.api_version, "clustered": s.clustered}),
)

# ---- storage ---------------------------------------------------------------------------------
_BIG = dict(suggested_unit_of_measurement=UnitOfInformation.GIGABYTES, suggested_display_precision=2, **_BYTES)

POOL_SENSORS: tuple[IncusSensorDescription, ...] = (
    IncusSensorDescription(key="space_used", name="Used", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda p: p.space_used,
                           attrs_fn=lambda p: {"driver": p.driver, "status": p.status, "description": p.description}, **_BIG),
    IncusSensorDescription(key="space_total", name="Total", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda p: p.space_total, **_BIG),
    IncusSensorDescription(key="space_free", name="Free", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda p: p.space_free, **_BIG),
    IncusSensorDescription(key="space_percent", name="Used percent", native_unit_of_measurement=PERCENTAGE,
                           state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value_fn=lambda p: p.space_percent),
    IncusSensorDescription(key="volume_count", name="Custom volumes", icon="mdi:harddisk-plus",
                           state_class=SensorStateClass.MEASUREMENT, value_fn=lambda p: p.volume_count),
)

VOLUME_SENSORS: tuple[IncusSensorDescription, ...] = (
    IncusSensorDescription(
        key="used", name="Used", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda v: v.used,
        attrs_fn=lambda v: {"pool": v.pool, "project": v.project, "content_type": v.content_type, "description": v.description,
                            "location": v.location, "created_at": v.created_at.isoformat() if v.created_at else None,
                            "used_by": list(v.used_by), "config": v.config}, **_BIG),
    IncusSensorDescription(key="quota", name="Size limit", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda v: v.quota, **_BIG),
    IncusSensorDescription(key="free", name="Free", state_class=SensorStateClass.MEASUREMENT, value_fn=lambda v: v.free, **_BIG),
    IncusSensorDescription(key="percent", name="Used percent", native_unit_of_measurement=PERCENTAGE,
                           state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value_fn=lambda v: v.percent),
)


class IncusInstanceSensor(DescribedMixin, IncusInstanceEntity, SensorEntity):
    def __init__(self, coordinator, key: str, description: IncusSensorDescription) -> None:
        super().__init__(coordinator, key, description.key)
        self.entity_description = description


class IncusServerSensor(DescribedMixin, IncusServerEntity, SensorEntity):
    def __init__(self, coordinator, description: IncusSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description


class IncusPoolSensor(DescribedMixin, IncusPoolEntity, SensorEntity):
    def __init__(self, coordinator, pool: str, description: IncusSensorDescription) -> None:
        super().__init__(coordinator, pool, description.key)
        self.entity_description = description


class IncusVolumeSensor(DescribedMixin, IncusVolumeEntity, SensorEntity):
    def __init__(self, coordinator, key: str, description: IncusSensorDescription) -> None:
        super().__init__(coordinator, key, description.key)
        self.entity_description = description


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    rt: IncusRuntimeData = entry.runtime_data
    eid = entry.entry_id
    inst, vols = rt.instances, rt.volumes

    def server_entities(data):
        for d in SERVER_SENSORS:
            yield f"{eid}_server_{d.key}", (lambda d=d: IncusServerSensor(inst, d))

    def instance_entities(data):
        for key, snap in data.instances.items():
            for d in (*INSTANCE_SENSORS, *instance_dynamic_descriptions(snap)):
                yield f"{eid}_{key}_{d.key}", (lambda key=key, d=d: IncusInstanceSensor(inst, key, d))

    def pool_entities(data):
        for pool in data.pools:
            for d in POOL_SENSORS:
                yield f"{eid}_pool_{pool}_{d.key}", (lambda pool=pool, d=d: IncusPoolSensor(vols, pool, d))
        for key in data.volumes:
            for d in VOLUME_SENSORS:
                yield f"{eid}_vol_{key}_{d.key}", (lambda key=key, d=d: IncusVolumeSensor(vols, key, d))

    async_add_dynamic(entry, inst, async_add_entities, server_entities)
    async_add_dynamic(entry, inst, async_add_entities, instance_entities)
    async_add_dynamic(entry, vols, async_add_entities, pool_entities)
