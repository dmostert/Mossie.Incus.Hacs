"""Incus integration: state of every instance (CPU, memory, disk, network ...), start/stop/restart commands, and storage volumes."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .client_factory import build_client
from .const import (ATTR_CONFIG_ENTRY, ATTR_FORCE, ATTR_INSTANCE, ATTR_PROJECT, ATTR_TIMEOUT, CONF_PROJECTS, CONF_SCAN_INTERVAL,
                    CONF_VOLUME_SCAN_INTERVAL, CONF_EXCLUDED_INSTANCES, CONF_EXCLUDED_POOLS, CONF_EXCLUDED_VOLUMES,
                    CONF_NOTIFY_CHANGES, DEFAULT_ACTION_TIMEOUT, DEFAULT_NOTIFY_CHANGES, DEFAULT_PROJECTS, DEFAULT_SCAN_INTERVAL,
                    DEFAULT_VOLUME_SCAN_INTERVAL, DOMAIN, SERVICE_ACTIONS, SERVICE_RESCAN)
from .coordinator import IncusInstancesCoordinator, IncusRuntimeData, IncusVolumesCoordinator
from .entity import instance_identifier, pool_identifier, server_device_info, volume_identifier
from .fetch import parse_projects
from .inventory_monitor import IncusInventoryMonitor, async_remove_baseline
from .incus_client import IncusError

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SWITCH]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ACTION_SCHEMA = vol.Schema({
    vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("entity_id"): cv.entity_ids,
    vol.Optional(ATTR_INSTANCE): cv.string,
    vol.Optional(ATTR_PROJECT, default="default"): cv.string,
    vol.Optional(ATTR_CONFIG_ENTRY): cv.string,
    vol.Optional(ATTR_FORCE, default=False): cv.boolean,
    vol.Optional(ATTR_TIMEOUT, default=DEFAULT_ACTION_TIMEOUT): vol.All(vol.Coerce(int), vol.Range(min=-1, max=3600)),
})


def resolve_targets(hass: HomeAssistant, data: dict[str, Any]) -> list[tuple[IncusRuntimeData, str, str]]:
    """(runtime data, project, instance) for every instance selected by device/entity targets or by explicit instance name."""
    found: dict[tuple[str, str, str], IncusRuntimeData] = {}
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    device_ids = set(data.get("device_id") or [])
    for entity_id in data.get("entity_id") or []:
        reg = ent_reg.async_get(entity_id)
        if reg is not None and reg.device_id:
            device_ids.add(reg.device_id)
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        if device is None:
            continue
        for domain, ident in device.identifiers:
            if domain != DOMAIN or ":inst:" not in ident:
                continue
            entry_id, _, rest = ident.partition(":inst:")
            project, _, name = rest.partition("/")
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and getattr(entry, "runtime_data", None) is not None:
                found[(entry_id, project, name)] = entry.runtime_data
    if data.get(ATTR_INSTANCE):
        wanted = data.get(ATTR_CONFIG_ENTRY)
        entries = [e for e in hass.config_entries.async_entries(DOMAIN)
                   if getattr(e, "runtime_data", None) is not None and (wanted is None or e.entry_id == wanted)]
        if not entries:
            raise ServiceValidationError("No loaded Incus server found")
        project = data.get(ATTR_PROJECT, "default")
        matches = [e for e in entries if instance_known(e.runtime_data, project, data[ATTR_INSTANCE])] or entries
        if len(matches) > 1:
            raise ServiceValidationError("Several Incus servers are configured: set config_entry to choose one")
        found[(matches[0].entry_id, project, data[ATTR_INSTANCE])] = matches[0].runtime_data
    if not found:
        raise ServiceValidationError("Select an instance device/entity, or provide 'instance' (and optionally 'project')")
    return [(rt, project, name) for (_, project, name), rt in found.items()]


def instance_known(rt: IncusRuntimeData, project: str, name: str) -> bool:
    return rt.instances.data is not None and f"{project}/{name}" in rt.instances.data.instances


async def _async_setup_services(hass: HomeAssistant) -> None:
    def make_handler(action: str):
        async def handler(call: ServiceCall) -> None:
            for rt, project, name in resolve_targets(hass, dict(call.data)):
                await rt.instances.async_instance_action(project, name, action, force=call.data.get(ATTR_FORCE, False),
                                                         timeout=call.data.get(ATTR_TIMEOUT, DEFAULT_ACTION_TIMEOUT))
        return handler

    for service, action in SERVICE_ACTIONS.items():
        hass.services.async_register(DOMAIN, service, make_handler(action), schema=ACTION_SCHEMA)

    async def rescan(call: ServiceCall) -> None:
        """Re-reads instances, pools and volumes now; new/removed items are reported by the inventory monitor."""
        wanted = call.data.get(ATTR_CONFIG_ENTRY)
        entries = [e for e in hass.config_entries.async_entries(DOMAIN)
                   if getattr(e, "runtime_data", None) is not None and (wanted is None or e.entry_id == wanted)]
        if not entries:
            raise ServiceValidationError("No loaded Incus server found")
        for e in entries:
            await e.runtime_data.instances.async_refresh()
            await e.runtime_data.volumes.async_refresh()

    hass.services.async_register(DOMAIN, SERVICE_RESCAN, rescan, schema=vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY): cv.string}))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    await _async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        client = await hass.async_add_executor_job(build_client, dict(entry.data))
    except (IncusError, OSError) as err:
        raise ConfigEntryNotReady(f"Cannot connect to Incus: {getattr(err, 'message', None) or err}") from err

    projects = parse_projects(entry.options.get(CONF_PROJECTS, DEFAULT_PROJECTS))
    instances = IncusInstancesCoordinator(hass, entry, client, projects, entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                                          frozenset(entry.options.get(CONF_EXCLUDED_INSTANCES, ())))
    volumes = IncusVolumesCoordinator(hass, entry, client, projects, entry.options.get(CONF_VOLUME_SCAN_INTERVAL, DEFAULT_VOLUME_SCAN_INTERVAL),
                                      frozenset(entry.options.get(CONF_EXCLUDED_POOLS, ())), frozenset(entry.options.get(CONF_EXCLUDED_VOLUMES, ())))
    await instances.async_config_entry_first_refresh()
    await volumes.async_refresh()          # storage problems must not stop instance monitoring; its entities just appear later
    entry.runtime_data = IncusRuntimeData(client=client, instances=instances, volumes=volumes)
    await IncusInventoryMonitor(hass, entry, instances, volumes, entry.options.get(CONF_NOTIFY_CHANGES, DEFAULT_NOTIFY_CHANGES)).async_start()

    # The server device must exist before instance/pool devices can reference it with via_device.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **server_device_info(entry.entry_id, instances.data.server))

    @callback
    def _prune_instances() -> None:
        _prune(hass, entry, f"{entry.entry_id}:inst:", {instance_identifier(entry.entry_id, k) for k in instances.data.instances})

    @callback
    def _prune_storage() -> None:
        if volumes.data is None:
            return
        valid = {pool_identifier(entry.entry_id, p) for p in volumes.data.pools} | \
                {volume_identifier(entry.entry_id, k) for k in volumes.data.volumes}
        _prune(hass, entry, f"{entry.entry_id}:pool:", valid)
        _prune(hass, entry, f"{entry.entry_id}:vol:", valid)

    entry.async_on_unload(instances.async_add_listener(_prune_instances))
    entry.async_on_unload(volumes.async_add_listener(_prune_storage))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _prune(hass: HomeAssistant, entry: ConfigEntry, prefix: str, valid: set[str]) -> None:
    """Removes devices of instances/pools/volumes that no longer exist on the server."""
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, ident in device.identifiers:
            if domain == DOMAIN and ident.startswith(prefix) and ident not in valid:
                dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)
                break


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_remove_baseline(hass, entry)
