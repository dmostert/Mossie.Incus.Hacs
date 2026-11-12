"""MINIMAL stand-ins for the Home Assistant APIs this integration uses, so its glue code can be exercised without Home Assistant.

They mirror the *documented* behaviour only; they do NOT prove the real HA API matches. Always also test inside a real HA."""
from __future__ import annotations

import asyncio
import enum
import sys
import types
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

T = TypeVar("T")


def _module(name: str, **attrs: Any) -> types.ModuleType:
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, m)
    return m


class AbortFlow(Exception):
    def __init__(self, reason):
        self.reason = reason


class HomeAssistantError(Exception):
    pass


class ConfigEntryNotReady(HomeAssistantError):
    pass


class ServiceValidationError(HomeAssistantError):
    pass


class UpdateFailed(Exception):
    pass


class _StrEnum(str, enum.Enum):
    pass


def install() -> None:
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_is_stub", False):
        return
    ha = _module("homeassistant", _is_stub=True)
    _module("homeassistant.helpers")
    _module("homeassistant.components")

    # ---- voluptuous (schemas are only constructed, never validated, in these tests) -------------------
    class _Marker:
        def __init__(self, key, default=None):
            self.key, self.default = key, default

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, o):
            return getattr(o, "key", o) == self.key
    vol = _module("voluptuous", Schema=lambda s: s, Required=lambda k, default=None: _Marker(k, default),
                  Optional=lambda k, default=None: _Marker(k, default), All=lambda *a: a, Coerce=lambda t: t,
                  Range=lambda **k: k, Any=lambda *a: a)

    # ---- core / const / exceptions ------------------------------------------------------------------
    class HomeAssistant:
        def __init__(self):
            self.services = types.SimpleNamespace(registered={}, async_register=lambda d, s, h, schema=None: self.services.registered.__setitem__((d, s), h))
            self.fired = []
            self.notifications = []
            self.bus = types.SimpleNamespace(async_fire=lambda event, data=None: self.fired.append((event, data)))
            self.config_entries = _ConfigEntries(self)
            self.device_registry = _DeviceRegistry()
            self.entity_registry = _EntityRegistry()

        async def async_add_executor_job(self, fn, *args):
            return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    @dataclass
    class ServiceCall:
        data: dict

    _module("homeassistant.core", HomeAssistant=HomeAssistant, ServiceCall=ServiceCall, callback=lambda f: f)
    _module("homeassistant.exceptions", HomeAssistantError=HomeAssistantError, ConfigEntryNotReady=ConfigEntryNotReady,
            ServiceValidationError=ServiceValidationError)

    class Platform(_StrEnum):
        SENSOR, BINARY_SENSOR, BUTTON, SWITCH = "sensor", "binary_sensor", "button", "switch"

    class EntityCategory(_StrEnum):
        DIAGNOSTIC, CONFIG = "diagnostic", "config"

    class UnitOfInformation(_StrEnum):
        BYTES, MEGABYTES, GIGABYTES = "B", "MB", "GB"

    class UnitOfTime(_StrEnum):
        SECONDS = "s"
    _module("homeassistant.const", Platform=Platform, PERCENTAGE="%", EntityCategory=EntityCategory,
            UnitOfInformation=UnitOfInformation, UnitOfTime=UnitOfTime)

    # ---- registries -----------------------------------------------------------------------------
    class _Device:
        def __init__(self, id_, identifiers, config_entry_id):
            self.id, self.identifiers, self.config_entry_ids = id_, set(identifiers), {config_entry_id}

    class _DeviceRegistry:
        def __init__(self):
            self.devices: dict[str, _Device] = {}
            self._n = 0

        def async_get_or_create(self, config_entry_id, identifiers, **kw):
            for d in self.devices.values():
                if d.identifiers == set(identifiers):
                    return d
            self._n += 1
            d = _Device(f"dev{self._n}", identifiers, config_entry_id)
            d.info = kw
            self.devices[d.id] = d
            return d

        def async_get(self, device_id):
            return self.devices.get(device_id)

        def async_update_device(self, device_id, remove_config_entry_id=None):
            self.devices.pop(device_id, None)

    class _EntityRegistry:
        def __init__(self):
            self.entities: dict[str, Any] = {}

        def async_get(self, entity_id):
            return self.entities.get(entity_id)

    holder = {"hass": None}
    _module("homeassistant.helpers.device_registry",
            DeviceInfo=lambda **kw: dict(kw), async_get=lambda hass: hass.device_registry,
            async_entries_for_config_entry=lambda reg, eid: [d for d in reg.devices.values() if eid in d.config_entry_ids])
    _module("homeassistant.helpers.entity_registry", async_get=lambda hass: hass.entity_registry)
    _module("homeassistant.helpers.config_validation", ensure_list=lambda x: x, string=str, entity_ids=list, boolean=bool,
            config_entry_only_config_schema=lambda d: {})
    ha.helpers.__dict__.update(config_validation=sys.modules["homeassistant.helpers.config_validation"],
                               device_registry=sys.modules["homeassistant.helpers.device_registry"],
                               entity_registry=sys.modules["homeassistant.helpers.entity_registry"])

    # ---- config entries / flows ----------------------------------------------------------------------
    class ConfigEntry:
        _n = 0

        def __init__(self, data=None, options=None, title="Incus"):
            ConfigEntry._n += 1
            self.entry_id = f"ENTRY{ConfigEntry._n}"
            self.data, self.options, self.title = data or {}, options or {}, title
            self.runtime_data = None
            self.unload_callbacks = []
            self.update_listeners = []

        def async_on_unload(self, cb):
            self.unload_callbacks.append(cb)

        def add_update_listener(self, cb):
            self.update_listeners.append(cb)
            return lambda: None

    class _ConfigEntries:
        def __init__(self, hass):
            self.hass, self.entries, self.added = hass, {}, {}

        def async_get_entry(self, entry_id):
            return self.entries.get(entry_id)

        def async_entries(self, domain=None):
            return list(self.entries.values())

        async def async_forward_entry_setups(self, entry, platforms):
            for p in platforms:
                mod = sys.modules[f"custom_components.incus.{p.value}"]
                added = self.added.setdefault((entry.entry_id, p.value), [])

                def add(ents, a=added, entry=entry):
                    a.extend(ents)
                    for e in ents:    # real HA creates the device from the entity's device_info
                        if e.device_info:
                            self.hass.device_registry.async_get_or_create(entry.entry_id, e.device_info["identifiers"])
                await mod.async_setup_entry(self.hass, entry, add)

        async def async_unload_platforms(self, entry, platforms):
            return True

        async def async_reload(self, entry_id):
            pass

    class ConfigFlow:
        def __init_subclass__(cls, domain=None, **kw):
            super().__init_subclass__(**kw)
            cls.domain = domain

        hass: Any = None
        _unique_ids: set = set()

        def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {}, "schema": data_schema, "placeholders": description_placeholders}

        def async_show_menu(self, step_id, menu_options, description_placeholders=None):
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options, "placeholders": description_placeholders}

        def async_create_entry(self, title, data):
            return {"type": "create_entry", "title": title, "data": data, "unique_id": self._uid}

        async def async_set_unique_id(self, uid):
            self._uid = uid

        def _abort_if_unique_id_configured(self):
            if self._uid in ConfigFlow._unique_ids:
                raise AbortFlow("already_configured")

    class OptionsFlow:
        config_entry: Any = None
        hass: Any = None

        def async_show_menu(self, step_id, menu_options, description_placeholders=None):
            return {"type": "menu", "step_id": step_id, "menu_options": menu_options}

        def async_abort(self, reason, description_placeholders=None):
            return {"type": "abort", "reason": reason, "placeholders": description_placeholders}

        def async_show_form(self, step_id, data_schema=None, errors=None, description_placeholders=None):
            return {"type": "form", "step_id": step_id, "errors": errors or {}, "schema": data_schema, "placeholders": description_placeholders}

        def async_create_entry(self, data):
            return {"type": "create_entry", "data": data}

    _module("homeassistant.config_entries", ConfigEntry=ConfigEntry, ConfigFlow=ConfigFlow, ConfigFlowResult=dict, OptionsFlow=OptionsFlow)

    class _Sel:
        def __init__(self, *a, **k):
            self.a, self.k = a, k

    class SelectSelectorMode(_StrEnum):
        DROPDOWN, LIST = "dropdown", "list"
    _module("homeassistant.helpers.selector", SelectSelector=_Sel, SelectSelectorConfig=_Sel, SelectSelectorMode=SelectSelectorMode,
            TextSelector=_Sel, TextSelectorConfig=_Sel)

    # ---- storage / notifications ----------------------------------------------------------------------
    persisted: dict = {}

    class Store:
        def __init__(self, hass, version, key):
            self.key = key

        async def async_load(self):
            return persisted.get(self.key)

        def async_delay_save(self, data_func, delay=0):
            persisted[self.key] = data_func()

        async def async_remove(self):
            persisted.pop(self.key, None)
    _module("homeassistant.helpers.storage", Store=Store)
    ha.helpers.__dict__["storage"] = sys.modules["homeassistant.helpers.storage"]
    ha.__dict__["_persisted"] = persisted

    def async_create(hass, message, title=None, notification_id=None):
        hass.notifications.append({"message": message, "title": title, "id": notification_id})
    _module("homeassistant.components.persistent_notification", async_create=async_create)
    sys.modules["homeassistant.components"].persistent_notification = sys.modules["homeassistant.components.persistent_notification"]

    # ---- coordinator & entities -----------------------------------------------------------------------
    class DataUpdateCoordinator(Generic[T]):
        def __init__(self, hass, logger, *, config_entry=None, name=None, update_interval=None):
            self.hass, self.config_entry, self.name, self.update_interval = hass, config_entry, name, update_interval
            self.data, self.last_update_success, self._listeners = None, True, []

        async def _async_update_data(self):
            raise NotImplementedError

        async def async_refresh(self):
            try:
                self.data = await self._async_update_data()
                self.last_update_success = True
            except UpdateFailed:
                self.last_update_success = False
            for cb in list(self._listeners):
                cb()

        async def async_config_entry_first_refresh(self):
            await self.async_refresh()
            if not self.last_update_success:
                raise ConfigEntryNotReady("first refresh failed")

        async def async_request_refresh(self):
            await self.async_refresh()

        def async_add_listener(self, cb):
            self._listeners.append(cb)
            return lambda: self._listeners.remove(cb)

    class Entity:
        _attr_has_entity_name = False
        _attr_unique_id = None
        _attr_device_info = None
        entity_description = None

        @property
        def unique_id(self):
            return self._attr_unique_id

        @property
        def device_info(self):
            return self._attr_device_info

        @property
        def name(self):
            return getattr(self.entity_description, "name", None)

        @property
        def available(self):
            return True

        @property
        def extra_state_attributes(self):
            return None

    class CoordinatorEntity(Entity, Generic[T]):
        def __init__(self, coordinator):
            self.coordinator = coordinator

        @property
        def available(self):
            return self.coordinator.last_update_success

    _module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator, UpdateFailed=UpdateFailed,
            CoordinatorEntity=CoordinatorEntity)

    def _desc(name, **fields):
        base_fields = dict(key=None, name=None, device_class=None, native_unit_of_measurement=None, state_class=None,
                           suggested_display_precision=None, suggested_unit_of_measurement=None, entity_category=None,
                           entity_registry_enabled_default=True, icon=None, translation_key=None)
        base_fields.update(fields)
        ns = {"__annotations__": {k: Any for k in base_fields}}
        ns.update(base_fields)
        return dataclass(frozen=True, kw_only=True)(type(name, (), ns))

    class SensorDeviceClass(_StrEnum):
        DATA_SIZE, DURATION, TIMESTAMP = "data_size", "duration", "timestamp"

    class SensorStateClass(_StrEnum):
        MEASUREMENT, TOTAL_INCREASING = "measurement", "total_increasing"

    class SensorEntity(Entity):
        @property
        def native_value(self):
            return None
    _module("homeassistant.components.sensor", SensorEntity=SensorEntity, SensorEntityDescription=_desc("SensorEntityDescription"),
            SensorDeviceClass=SensorDeviceClass, SensorStateClass=SensorStateClass)

    class BinarySensorDeviceClass(_StrEnum):
        RUNNING = "running"

    class BinarySensorEntity(Entity):
        pass
    _module("homeassistant.components.binary_sensor", BinarySensorEntity=BinarySensorEntity, BinarySensorDeviceClass=BinarySensorDeviceClass,
            BinarySensorEntityDescription=_desc("BinarySensorEntityDescription"))

    class ButtonDeviceClass(_StrEnum):
        RESTART = "restart"

    class ButtonEntity(Entity):
        pass
    _module("homeassistant.components.button", ButtonEntity=ButtonEntity, ButtonDeviceClass=ButtonDeviceClass,
            ButtonEntityDescription=_desc("ButtonEntityDescription"))

    class SwitchDeviceClass(_StrEnum):
        SWITCH = "switch"

    class SwitchEntity(Entity):
        pass
    _module("homeassistant.components.switch", SwitchEntity=SwitchEntity, SwitchDeviceClass=SwitchDeviceClass,
            SwitchEntityDescription=_desc("SwitchEntityDescription"))
