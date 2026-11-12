"""Notices instances / storage pools / custom volumes appearing or disappearing and tells the user."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN, EVENT_INVENTORY_CHANGED
from .coordinator import IncusInstancesCoordinator, IncusVolumesCoordinator
from .inventory import Inventory, InventoryChange, InventoryTracker

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1


class IncusInventoryMonitor:
    """Compares every poll with the previous inventory (persisted across restarts, so changes made while Home Assistant was
    down are noticed too). On a change it fires ``incus_inventory_changed`` and, if enabled, a persistent notification."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, instances: IncusInstancesCoordinator,
                 volumes: IncusVolumesCoordinator, notify: bool) -> None:
        self._hass, self._entry, self._instances, self._volumes, self._notify = hass, entry, instances, volumes, notify
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_inventory")
        self._tracker = InventoryTracker()
        self._saved: dict[str, Any] | None = None

    async def async_start(self) -> None:
        stored = await self._store.async_load()
        self._saved = stored
        self._tracker = InventoryTracker(Inventory.from_dict(stored))
        self._on_instances()          # the first refresh already happened: diff it against the stored baseline now
        self._on_storage()
        self._entry.async_on_unload(self._instances.async_add_listener(self._on_instances))
        self._entry.async_on_unload(self._volumes.async_add_listener(self._on_storage))

    @callback
    def _on_instances(self) -> None:
        data = self._instances.data
        if data is not None:
            self._handle(self._tracker.update(instances=data.discovered))

    @callback
    def _on_storage(self) -> None:
        data = self._volumes.data
        if data is not None:
            self._handle(self._tracker.update(pools=data.discovered_pools, volumes=data.discovered_volumes))

    def _handle(self, change: InventoryChange) -> None:
        current = self._tracker.current.to_dict()
        if current != self._saved:                    # persist only when something changed (not on every poll)
            self._saved = current
            self._store.async_delay_save(lambda: current, 1)
        if change.is_empty:
            return
        server = self._instances.data.server.name if self._instances.data else "incus"
        _LOGGER.info("Incus inventory of %s changed: %s", server, change.describe().replace("\n", "; "))
        self._hass.bus.async_fire(EVENT_INVENTORY_CHANGED, {"entry_id": self._entry.entry_id, "server": server, **change.to_event_data()})
        if self._notify:
            persistent_notification.async_create(
                self._hass,
                message=f"{change.describe()}\n\nTo choose what to monitor: Settings → Devices & services → Incus → Configure → "
                        "*Rescan and choose what to monitor*.",
                title=f"Incus {server}: inventory changed",
                notification_id=f"{DOMAIN}_{self._entry.entry_id}_inventory")


async def async_remove_baseline(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deletes the persisted inventory when the config entry is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_inventory").async_remove()
