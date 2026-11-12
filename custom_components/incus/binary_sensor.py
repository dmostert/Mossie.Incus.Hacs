"""Binary sensors: instance running, volume in use."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription

from .entity import DescribedMixin, IncusInstanceEntity, IncusVolumeEntity, async_add_dynamic

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class IncusBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool]
    attrs_fn: Callable[[Any], dict[str, Any]] | None = None


INSTANCE_BINARY = (
    IncusBinaryDescription(key="running", name="Running", device_class=BinarySensorDeviceClass.RUNNING,
                           value_fn=lambda s: s.is_running, attrs_fn=lambda s: {"status": s.status, "frozen": s.is_frozen}),
)
VOLUME_BINARY = (
    IncusBinaryDescription(key="in_use", name="In use", icon="mdi:link-variant", value_fn=lambda v: bool(v.used_by),
                           attrs_fn=lambda v: {"used_by": list(v.used_by)}),
)


class IncusInstanceBinarySensor(DescribedMixin, IncusInstanceEntity, BinarySensorEntity):
    def __init__(self, coordinator, key: str, description: IncusBinaryDescription) -> None:
        super().__init__(coordinator, key, description.key)
        self.entity_description = description

    @property
    def is_on(self):
        return self.native_value


class IncusVolumeBinarySensor(DescribedMixin, IncusVolumeEntity, BinarySensorEntity):
    def __init__(self, coordinator, key: str, description: IncusBinaryDescription) -> None:
        super().__init__(coordinator, key, description.key)
        self.entity_description = description

    @property
    def is_on(self):
        return self.native_value


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    eid = entry.entry_id

    def instance_entities(data):
        for key in data.instances:
            for d in INSTANCE_BINARY:
                yield f"{eid}_{key}_{d.key}", (lambda key=key, d=d: IncusInstanceBinarySensor(rt.instances, key, d))

    def volume_entities(data):
        for key in data.volumes:
            for d in VOLUME_BINARY:
                yield f"{eid}_vol_{key}_{d.key}", (lambda key=key, d=d: IncusVolumeBinarySensor(rt.volumes, key, d))

    async_add_dynamic(entry, rt.instances, async_add_entities, instance_entities)
    async_add_dynamic(entry, rt.volumes, async_add_entities, volume_entities)
