"""Data update coordinators for the Incus integration."""
from __future__ import annotations

import http.client
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .commands import run_instance_action
from .const import DEFAULT_ACTION_TIMEOUT
from .data import CpuTracker, InstancesData, VolumesData, build_instances_data
from .fetch import ALL, fetch_instances, fetch_server, fetch_volumes
from .incus_client import IncusError

_LOGGER = logging.getLogger(__name__)
_ERRORS = (IncusError, OSError, http.client.HTTPException)


def _default_project(projects: list[str]) -> str:
    return "default" if projects == [ALL] else projects[0]


class IncusInstancesCoordinator(DataUpdateCoordinator[InstancesData]):
    """Polls state of all instances (CPU, memory, disk, network ...) and runs power commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Any, projects: list[str], interval: int,
                 excluded: frozenset[str] = frozenset()) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"Incus instances ({entry.title})",
                         update_interval=timedelta(seconds=interval))
        self.client = client
        self.projects = projects
        self.excluded = excluded
        self._tracker = CpuTracker()

    def _fetch(self) -> InstancesData:
        server = fetch_server(self.client)
        instances = fetch_instances(self.client, self.projects)
        return build_instances_data(server, instances, self._tracker, time.monotonic(), _default_project(self.projects), self.excluded)

    async def _async_update_data(self) -> InstancesData:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except _ERRORS as err:
            raise UpdateFailed(f"Error talking to Incus: {getattr(err, 'message', None) or err}") from err

    async def async_instance_action(self, project: str, name: str, action: str, *, force: bool = False,
                                    timeout: int = DEFAULT_ACTION_TIMEOUT) -> None:
        """Runs start/stop/restart/freeze/unfreeze and refreshes. Raises HomeAssistantError with the server's message on failure."""
        try:
            await self.hass.async_add_executor_job(
                partial(run_instance_action, self.client, project, name, action, force=force, timeout=timeout))
        except _ERRORS as err:
            raise HomeAssistantError(f"Could not {action} {project}/{name}: {getattr(err, 'message', None) or err}") from err
        finally:
            await self.async_request_refresh()


class IncusVolumesCoordinator(DataUpdateCoordinator[VolumesData]):
    """Polls storage pools and custom volumes (slower than instances: usage queries are one request per volume)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Any, projects: list[str], interval: int,
                 excluded_pools: frozenset[str] = frozenset(), excluded_volumes: frozenset[str] = frozenset()) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"Incus storage ({entry.title})",
                         update_interval=timedelta(seconds=interval))
        self.client = client
        self.projects = projects
        self.excluded_pools = excluded_pools
        self.excluded_volumes = excluded_volumes

    async def _async_update_data(self) -> VolumesData:
        try:
            return await self.hass.async_add_executor_job(fetch_volumes, self.client, self.projects, self.excluded_pools, self.excluded_volumes)
        except _ERRORS as err:
            raise UpdateFailed(f"Error reading Incus storage: {getattr(err, 'message', None) or err}") from err


@dataclass
class IncusRuntimeData:
    client: Any
    instances: IncusInstancesCoordinator
    volumes: IncusVolumesCoordinator
