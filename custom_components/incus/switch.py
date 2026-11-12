"""Power switch per instance (on = running/starting/frozen)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity, SwitchEntityDescription

from .entity import IncusInstanceEntity, async_add_dynamic

PARALLEL_UPDATES = 1
_ON_CODES = (103, 106, 110)    # Running, Starting, Frozen


class IncusPowerSwitch(IncusInstanceEntity, SwitchEntity):
    entity_description = SwitchEntityDescription(key="power", name="Power", device_class=SwitchDeviceClass.SWITCH, icon="mdi:power")

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, key, "switch_power")

    @property
    def is_on(self) -> bool | None:
        s = self._snapshot()
        return None if s is None else s.status_code in _ON_CODES

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        s = self._snapshot()
        return None if s is None else {"status": s.status}

    async def async_turn_on(self, **kwargs: Any) -> None:
        s = self._snapshot()
        action = "unfreeze" if s is not None and s.is_frozen else "start"
        await self.coordinator.async_instance_action(self.project, self.instance_name, action)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_instance_action(self.project, self.instance_name, "stop")


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    eid = entry.entry_id

    def entities(data):
        for key in data.instances:
            yield f"{eid}_{key}_switch_power", (lambda key=key: IncusPowerSwitch(rt.instances, key))

    async_add_dynamic(entry, rt.instances, async_add_entities, entities)
