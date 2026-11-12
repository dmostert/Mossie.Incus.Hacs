"""Config flow: connect over HTTPS (pin the server certificate, then authenticate with a trust token or an existing client
certificate) or over a local Unix socket."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector,
                                            TextSelectorConfig)

from .client_factory import (CannotConnect, InvalidToken, NotTrusted, build_client, describe, fetch_server_certificate,
                             generate_client_identity, normalize_url, register_with_token, validate_connection)
from .const import (CONF_CLIENT_CERT, CONF_CLIENT_KEY, CONF_CLIENT_NAME, CONF_CONNECTION, CONF_PROJECTS, CONF_SCAN_INTERVAL,
                    CONF_EXCLUDED_INSTANCES, CONF_EXCLUDED_POOLS, CONF_EXCLUDED_VOLUMES, CONF_MONITOR_INSTANCES,
                    CONF_MONITOR_POOLS, CONF_MONITOR_VOLUMES, CONF_NOTIFY_CHANGES, CONF_REVIEWED, DEFAULT_NOTIFY_CHANGES,
                    CONF_SERVER_CERT, CONF_SOCKET, CONF_TRUST_TOKEN, CONF_URL, CONF_VERIFY, CONF_VOLUME_SCAN_INTERVAL, CONN_HTTPS,
                    CONN_UNIX, DEFAULT_CLIENT_NAME, DEFAULT_PROJECTS, DEFAULT_SCAN_INTERVAL, DEFAULT_SOCKET,
                    DEFAULT_VOLUME_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL, MIN_VOLUME_SCAN_INTERVAL, VERIFY_PIN, VERIFY_SKIP,
                    VERIFY_SYSTEM)
from .fetch import parse_projects, scan_inventory
from .incus_client import IncusError
from .incus_client.shared import tls
from .inventory import Inventory, label, new_since

_LOGGER = logging.getLogger(__name__)
_SCAN_ERRORS = (OSError, ValueError)

_PEM = TextSelector(TextSelectorConfig(multiline=True))


class IncusConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._fingerprint = ""
        self._detail: dict[str, str] = {"error": ""}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "IncusOptionsFlow":
        return IncusOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=[CONN_HTTPS, CONN_UNIX])

    # ---- HTTPS -----------------------------------------------------------------------------
    async def async_step_https(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            self._data = {CONF_CONNECTION: CONN_HTTPS, CONF_URL: url, CONF_VERIFY: user_input[CONF_VERIFY]}
            if user_input[CONF_VERIFY] == VERIFY_PIN:
                try:
                    pem = await self.hass.async_add_executor_job(fetch_server_certificate, url)
                except CannotConnect as err:
                    errors["base"] = "cannot_connect"
                    self._detail = {"error": str(err)}
                else:
                    self._data[CONF_SERVER_CERT] = pem
                    self._fingerprint = tls.cert_fingerprint_str(pem)
            if not errors:
                return await self.async_step_auth()
        schema = vol.Schema({
            vol.Required(CONF_URL, default=(user_input or {}).get(CONF_URL, "https://")): str,
            vol.Required(CONF_VERIFY, default=(user_input or {}).get(CONF_VERIFY, VERIFY_PIN)): SelectSelector(SelectSelectorConfig(
                options=[VERIFY_PIN, VERIFY_SYSTEM, VERIFY_SKIP], translation_key="verify", mode=SelectSelectorMode.DROPDOWN)),
        })
        return self.async_show_form(step_id="https", data_schema=schema, errors=errors, description_placeholders=self._detail)

    async def async_step_auth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="auth", menu_options=["token", "existing"],
                                    description_placeholders={"fingerprint": self._fingerprint or "n/a"})

    async def async_step_token(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Generates a client certificate and registers it with a one-time trust token (``incus config trust add <name>``)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.get(CONF_CLIENT_NAME) or DEFAULT_CLIENT_NAME
            cert, key = await self.hass.async_add_executor_job(generate_client_identity)
            data = {**self._data, CONF_CLIENT_CERT: cert, CONF_CLIENT_KEY: key}
            try:
                await self.hass.async_add_executor_job(register_with_token, data, user_input[CONF_TRUST_TOKEN], name)
            except InvalidToken as err:
                errors["base"] = "invalid_token"
                self._detail = {"error": str(err)}
            except CannotConnect as err:
                errors["base"] = "cannot_connect"
                self._detail = {"error": str(err)}
            else:
                self._data = data
                result = await self._async_validate_and_create(errors)
                if result is not None:
                    return result
        schema = vol.Schema({vol.Required(CONF_TRUST_TOKEN): str, vol.Optional(CONF_CLIENT_NAME, default=DEFAULT_CLIENT_NAME): str})
        return self.async_show_form(step_id="token", data_schema=schema, errors=errors, description_placeholders=self._detail)

    async def async_step_existing(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {**self._data, CONF_CLIENT_CERT: user_input[CONF_CLIENT_CERT].strip() + "\n",
                          CONF_CLIENT_KEY: user_input[CONF_CLIENT_KEY].strip() + "\n"}
            result = await self._async_validate_and_create(errors)
            if result is not None:
                return result
        schema = vol.Schema({vol.Required(CONF_CLIENT_CERT): _PEM, vol.Required(CONF_CLIENT_KEY): _PEM})
        return self.async_show_form(step_id="existing", data_schema=schema, errors=errors, description_placeholders=self._detail)

    # ---- Unix socket -------------------------------------------------------------------------
    async def async_step_unix(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {CONF_CONNECTION: CONN_UNIX, CONF_SOCKET: user_input[CONF_SOCKET]}
            result = await self._async_validate_and_create(errors)
            if result is not None:
                return result
        schema = vol.Schema({vol.Required(CONF_SOCKET, default=DEFAULT_SOCKET): str})
        return self.async_show_form(step_id="unix", data_schema=schema, errors=errors, description_placeholders=self._detail)

    async def _async_validate_and_create(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Creates the entry, or fills ``errors`` and returns None so the caller re-shows its form."""
        try:
            info = await self.hass.async_add_executor_job(validate_connection, self._data)
        except CannotConnect as err:
            errors["base"] = "cannot_connect"
            self._detail = {"error": str(err)}
            return None
        except NotTrusted as err:
            errors["base"] = "not_trusted"
            self._detail = {"error": str(err)}
            return None
        await self.async_set_unique_id(info.fingerprint or info.name)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=f"Incus {info.name}", data=self._data)


class IncusOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["settings", "inventory"])

    # ---- intervals, projects, notifications --------------------------------------------------
    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not any(p.strip() for p in user_input[CONF_PROJECTS].split(",")):
                errors[CONF_PROJECTS] = "invalid_projects"
            else:
                return self.async_create_entry(data={**self.config_entry.options, **user_input})   # keep the exclusions
        o = self.config_entry.options
        schema = vol.Schema({
            vol.Required(CONF_PROJECTS, default=o.get(CONF_PROJECTS, DEFAULT_PROJECTS)): str,
            vol.Required(CONF_SCAN_INTERVAL, default=o.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
            vol.Required(CONF_VOLUME_SCAN_INTERVAL, default=o.get(CONF_VOLUME_SCAN_INTERVAL, DEFAULT_VOLUME_SCAN_INTERVAL)):
                vol.All(vol.Coerce(int), vol.Range(min=MIN_VOLUME_SCAN_INTERVAL, max=86400)),
            vol.Required(CONF_NOTIFY_CHANGES, default=o.get(CONF_NOTIFY_CHANGES, DEFAULT_NOTIFY_CHANGES)): bool,
        })
        return self.async_show_form(step_id="settings", data_schema=schema, errors=errors)

    # ---- rescan and choose what to monitor ---------------------------------------------------
    async def _async_scan(self) -> Inventory:
        entry = self.config_entry
        runtime = getattr(entry, "runtime_data", None)
        client = runtime.client if runtime is not None else await self.hass.async_add_executor_job(build_client, dict(entry.data))
        projects = parse_projects(entry.options.get(CONF_PROJECTS, DEFAULT_PROJECTS))
        return await self.hass.async_add_executor_job(scan_inventory, client, projects)

    async def async_step_inventory(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Re-reads the server and shows every instance / storage pool / custom volume as a checklist (checked = monitored).
        Items that appeared since the last review are marked "(new)"; unchecking excludes them (and their entities)."""
        try:
            inv = await self._async_scan()
        except (IncusError, *_SCAN_ERRORS) as err:
            _LOGGER.warning("Rescanning the Incus inventory failed: %s", describe(err))
            return self.async_abort(reason="cannot_connect", description_placeholders={"error": describe(err)})
        o = self.config_entry.options
        reviewed = o.get(CONF_REVIEWED)
        spec = (("instances", CONF_MONITOR_INSTANCES, CONF_EXCLUDED_INSTANCES),
                ("pools", CONF_MONITOR_POOLS, CONF_EXCLUDED_POOLS),
                ("volumes", CONF_MONITOR_VOLUMES, CONF_EXCLUDED_VOLUMES))
        if inv.total == 0:
            return self.async_abort(reason="nothing_to_select")
        if user_input is not None:
            new_options = {**o, CONF_REVIEWED: inv.to_dict()}
            for kind, field, excluded_key in spec:
                new_options[excluded_key] = sorted(getattr(inv, kind) - set(user_input.get(field, [])))
            return self.async_create_entry(data=new_options)

        fields: dict[Any, Any] = {}
        n_new = 0
        for kind, field, excluded_key in spec:
            keys = sorted(getattr(inv, kind))
            if not keys:
                continue
            excluded = set(o.get(excluded_key, ()))
            fresh = new_since(reviewed, kind, keys)
            n_new += len(fresh)
            options = [{"value": k, "label": label(kind, k) + (" (new)" if k in fresh else "")} for k in keys]
            fields[vol.Required(field, default=[k for k in keys if k not in excluded])] = SelectSelector(
                SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST))
        summary = (f"{len(inv.instances)} instances, {len(inv.pools)} storage pools, {len(inv.volumes)} custom volumes"
                   + (f" - {n_new} new since your last review" if n_new else ""))
        return self.async_show_form(step_id="inventory", data_schema=vol.Schema(fields), description_placeholders={"summary": summary})
