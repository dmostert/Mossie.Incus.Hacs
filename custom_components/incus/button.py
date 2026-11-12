"""Buttons: start / stop / restart (and force stop) an instance."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity, ButtonEntityDescription

from homeassistant.const import EntityCategory

from .entity import IncusInstanceEntity, IncusServerEntity, async_add_dynamic

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class IncusButtonDescription(ButtonEntityDescription):
    action: str
    force: bool = False


BUTTONS = (
    IncusButtonDescription(key="start", name="Start", icon="mdi:play", action="start"),
    IncusButtonDescription(key="stop", name="Stop", icon="mdi:stop", action="stop"),
    IncusButtonDescription(key="restart", name="Restart", device_class=ButtonDeviceClass.RESTART, action="restart"),
    IncusButtonDescription(key="force_stop", name="Force stop", icon="mdi:stop-circle", action="stop", force=True,
                           entity_registry_enabled_default=False),
)


class IncusInstanceButton(IncusInstanceEntity, ButtonEntity):
    def __init__(self, coordinator, key: str, description: IncusButtonDescription) -> None:
        super().__init__(coordinator, key, f"button_{description.key}")
        self.entity_description = description

    async def async_press(self) -> None:
        d = self.entity_description
        await self.coordinator.async_instance_action(self.project, self.instance_name, d.action, force=d.force)


class IncusRescanButton(IncusServerEntity, ButtonEntity):
    """Re-reads instances, pools and volumes right now (new or removed items are then reported and entities added/removed)."""
    entity_description = ButtonEntityDescription(key="rescan", name="Rescan inventory", icon="mdi:magnify-scan",
                                                 entity_category=EntityCategory.DIAGNOSTIC)

    def __init__(self, instances, volumes) -> None:
        super().__init__(instances, "button_rescan")
        self._volumes = volumes

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
        await self._volumes.async_refresh()


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    rt = entry.runtime_data
    eid = entry.entry_id

    def server_entities(data):
        yield f"{eid}_server_button_rescan", (lambda: IncusRescanButton(rt.instances, rt.volumes))

    async_add_dynamic(entry, rt.instances, async_add_entities, server_entities)

    def entities(data):
        for key in data.instances:
            for d in BUTTONS:
                yield f"{eid}_{key}_button_{d.key}", (lambda key=key, d=d: IncusInstanceButton(rt.instances, key, d))

    async_add_dynamic(entry, rt.instances, async_add_entities, entities)
