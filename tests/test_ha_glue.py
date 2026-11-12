"""Exercises coordinators, entities, services and the config flow against the mock Incus server, using the HA stubs."""
import asyncio  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

from tests import ha_stubs
from tests import mock_server as ms
from tests import payloads as pl

ha_stubs.install()
from tests._loader import load, install_namespace  # noqa: E402

install_namespace()
integration = None  # the package __init__ is loaded explicitly below (it needs the stubs)


def load_package_init():
    import importlib.util
    import sys
    from tests._loader import PKG_DIR
    spec = importlib.util.spec_from_file_location("custom_components.incus", os.path.join(PKG_DIR, "__init__.py"), submodule_search_locations=[PKG_DIR])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["custom_components.incus"] = mod
    spec.loader.exec_module(mod)
    return mod


integration = load_package_init()
for m in ("sensor", "binary_sensor", "button", "switch", "config_flow", "coordinator", "entity", "const"):
    load(m)
from custom_components.incus import config_flow as cf  # noqa: E402
from custom_components.incus import const as c  # noqa: E402
from custom_components.incus.entity import instance_identifier  # noqa: E402
from custom_components.incus.incus_client.shared import tls  # noqa: E402
from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, ServiceCall  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError  # noqa: E402


def make_server(path):
    srv = ms.UnixServer(path)
    ms.serve(srv)
    r = srv.routes
    r[("GET", "/1.0")] = lambda q: ms.sync(pl.SERVER)
    srv.instances = [pl.running_instance("web"), pl.stopped_instance("db")]
    r[("GET", "/1.0/instances")] = lambda q: ms.sync(srv.instances)
    r[("GET", "/1.0/storage-pools")] = lambda q: ms.sync(pl.POOLS)
    for p in ("default", "fast"):
        r[("GET", f"/1.0/storage-pools/{p}/resources")] = (lambda p: lambda q: ms.sync(pl.RESOURCES.get(p) or {"space": {"used": 1, "total": 2}}))(p)
        r[("GET", f"/1.0/storage-pools/{p}/volumes")] = (lambda p: lambda q: ms.sync(srv.volumes[p]))(p)
    srv.volumes = __import__("copy").deepcopy(pl.VOLUMES)
    for v, st in pl.VOLUME_STATE.items():
        r[("GET", f"/1.0/storage-pools/default/volumes/custom/{v}/state")] = (lambda st: lambda q: ms.sync(st))(st)
    r[("PUT", "/1.0/instances/web/state")] = lambda q: ms.async_op("w1")
    r[("PUT", "/1.0/instances/db/state")] = lambda q: ms.async_op("d1")
    for op in ("w1", "d1"):
        r[("GET", f"/1.0/operations/{op}/wait")] = (lambda op: lambda q: ms.sync({"id": op, "status_code": 200}))(op)
    return srv


class GlueBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "u.sock")
        self.srv = make_server(self.path)
        import sys
        sys.modules["homeassistant"]._persisted.clear()
        self.hass = HomeAssistant()
        self.entry = ConfigEntry(data={c.CONF_CONNECTION: c.CONN_UNIX, c.CONF_SOCKET: self.path}, options={c.CONF_PROJECTS: "homelab"})
        self.hass.config_entries.entries[self.entry.entry_id] = self.entry

    async def asyncTearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    async def setup_entry(self):
        self.assertTrue(await integration.async_setup_entry(self.hass, self.entry))
        self.added = {p: self.hass.config_entries.added.get((self.entry.entry_id, p), []) for p in ("sensor", "binary_sensor", "button", "switch")}
        return self.entry.runtime_data

    def find(self, platform, uid_suffix):
        hits = [e for e in self.added[platform] if e.unique_id.endswith(uid_suffix)]
        self.assertEqual(len(hits), 1, f"{uid_suffix}: {[e.unique_id for e in self.added[platform]]}")
        return hits[0]


class EntityTests(GlueBase):
    async def test_entities_and_values(self):
        rt = await self.setup_entry()
        eid = self.entry.entry_id
        # server device first, then instance + pool + volume devices
        self.assertIn(f"{eid}:server", [i for d in self.hass.device_registry.devices.values() for _, i in d.identifiers])

        web = lambda s: self.find("sensor", f"homelab/web_{s}")  # noqa: E731
        self.assertEqual(web("status").native_value, "Running")
        self.assertEqual(web("cpu_time").native_value, 0.154)
        self.assertIsNone(web("cpu_usage").native_value)                      # first poll: no delta yet
        self.assertEqual(web("memory_usage").native_value, 33_075_200)
        self.assertEqual(web("memory_percent").native_value, 0.11)
        self.assertEqual(web("processes").native_value, 10)
        self.assertEqual(web("uptime").native_value.utcoffset().total_seconds(), 7200)
        self.assertEqual(web("net_eth0_tx").native_value, 2151)
        self.assertEqual(web("net_eth0_rx").extra_state_attributes["mac"], "00:16:3e:aa:bb:cc")
        self.assertIsNone(web("disk_root").native_value)                       # -1 -> unknown
        self.assertEqual(web("status").extra_state_attributes["project"], "homelab")
        self.assertEqual(web("status").device_info["model"], "Container")
        self.assertEqual(web("status").device_info["name"], "homelab/web")
        self.assertEqual(web("status").entity_description.name, "Status")
        self.assertEqual(self.find("sensor", "homelab/db_status").native_value, "Stopped")
        self.assertIsNone(self.find("sensor", "homelab/db_uptime").native_value)

        # no per-NIC sensors for loopback
        self.assertFalse([e for e in self.added["sensor"] if "net_lo" in e.unique_id])

        # server summary
        self.assertEqual(self.find("sensor", "server_instances_running").native_value, 1)
        self.assertEqual(self.find("sensor", "server_instances_total").native_value, 2)
        self.assertEqual(self.find("sensor", "server_version").native_value, "6.5")

        # binary / switch / buttons
        self.assertTrue(self.find("binary_sensor", "homelab/web_running").is_on)
        self.assertFalse(self.find("binary_sensor", "homelab/db_running").is_on)
        self.assertTrue(self.find("switch", "homelab/web_switch_power").is_on)
        self.assertFalse(self.find("switch", "homelab/db_switch_power").is_on)
        self.assertEqual(len([e for e in self.added["button"] if "homelab/web" in e.unique_id]), 4)
        self.assertFalse(self.find("button", "homelab/web_button_force_stop").entity_description.entity_registry_enabled_default)

    async def test_storage_entities(self):
        await self.setup_entry()
        pool = lambda s: self.find("sensor", f"pool_default_{s}")  # noqa: E731
        self.assertEqual((pool("space_used").native_value, pool("space_total").native_value, pool("space_free").native_value,
                          pool("space_percent").native_value, pool("volume_count").native_value), (20e9, 100e9, 80e9, 20.0, 2))
        self.assertEqual(pool("space_used").extra_state_attributes["driver"], "zfs")
        vol = lambda s: self.find("sensor", f"vol_default/homelab/data_{s}")  # noqa: E731
        self.assertEqual((vol("used").native_value, vol("quota").native_value, vol("percent").native_value, vol("free").native_value),
                         (3_000_000_000, 10_737_418_240, 27.94, 7_737_418_240))
        attrs = vol("used").extra_state_attributes
        self.assertEqual((attrs["used_by"], attrs["pool"], attrs["content_type"], attrs["config"]), (["homelab/web"], "default", "filesystem", {"size": "10GiB"}))
        self.assertTrue(self.find("binary_sensor", "vol_default/homelab/data_in_use").is_on)
        self.assertFalse(self.find("binary_sensor", "vol_default/homelab/scratch_in_use").is_on)
        self.assertIsNone(self.find("sensor", "vol_default/homelab/scratch_percent").native_value)
        self.assertEqual(self.find("sensor", "vol_default/homelab/data_used").device_info["via_device"], ("incus", f"{self.entry.entry_id}:pool:default"))
        self.assertFalse([e for e in self.added["sensor"] if "vol_default/homelab/web" in e.unique_id])       # instance volume ignored

    async def test_unique_ids_are_unique(self):
        await self.setup_entry()
        ids = [e.unique_id for plat in self.added.values() for e in plat]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(len(ids), 60)

    async def test_cpu_percent_after_second_poll(self):
        rt = await self.setup_entry()
        self.srv.instances = [pl.running_instance("web", cpu_ns=153_597_000 + 1_000_000_000), pl.stopped_instance("db")]
        await asyncio.sleep(0.5)
        await rt.instances.async_refresh()
        pct = self.find("sensor", "homelab/web_cpu_usage").native_value
        self.assertIsNotNone(pct)
        self.assertGreater(pct, 0)

    async def test_dynamic_entities_and_pruning(self):
        rt = await self.setup_entry()
        before = sum(len(v) for v in self.added.values())
        new = pl.running_instance("cache")
        new["state"]["network"]["eth1"] = {**new["state"]["network"]["eth0"], "host_name": "veth9"}
        self.srv.instances = [pl.running_instance("web"), new]          # db disappears, cache appears
        await rt.instances.async_refresh()
        self.find("sensor", "homelab/cache_status")
        self.find("sensor", "homelab/cache_net_eth1_rx")
        await rt.instances.async_refresh()                               # no duplicates on later refreshes
        ids = [e.unique_id for plat in self.added.values() for e in plat]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreater(sum(len(v) for v in self.added.values()), before)
        idents = [i for d in self.hass.device_registry.devices.values() for _, i in d.identifiers]
        self.assertNotIn(instance_identifier(self.entry.entry_id, "homelab/db"), idents)       # pruned
        self.assertIn(instance_identifier(self.entry.entry_id, "homelab/web"), idents)
        self.assertFalse(self.find("sensor", "homelab/db_status").available is True and rt.instances.data.instances.get("homelab/db"))

    async def test_unavailable_when_server_fails(self):
        rt = await self.setup_entry()
        self.srv.routes[("GET", "/1.0/instances")] = lambda q: ms.error("down", 500)
        await rt.instances.async_refresh()
        self.assertFalse(rt.instances.last_update_success)
        self.assertFalse(self.find("sensor", "homelab/web_status").available)

    async def test_volume_failure_does_not_block_setup(self):
        self.srv.routes[("GET", "/1.0/storage-pools")] = lambda q: ms.error("no storage", 500)
        await self.setup_entry()
        self.find("sensor", "homelab/web_status")
        self.assertFalse([e for e in self.added["sensor"] if "_pool_" in e.unique_id])

    async def test_setup_not_ready_when_unreachable(self):
        self.entry.data = {c.CONF_CONNECTION: c.CONN_UNIX, c.CONF_SOCKET: os.path.join(self.dir, "nope.sock")}
        with self.assertRaises(ConfigEntryNotReady):
            await integration.async_setup_entry(self.hass, self.entry)
        self.entry.data = {c.CONF_CONNECTION: c.CONN_UNIX, c.CONF_SOCKET: self.path}
        self.srv.routes[("GET", "/1.0/instances")] = lambda q: ms.error("boom", 500)
        with self.assertRaises(ConfigEntryNotReady):
            await integration.async_setup_entry(self.hass, self.entry)

    async def test_all_projects_option(self):
        self.entry.options = {c.CONF_PROJECTS: "*"}
        await self.setup_entry()
        inst_reqs = [r for r in self.srv.requests if r["route"] == "/1.0/instances"]
        self.assertIn("all-projects=true", inst_reqs[0]["query"])


class CommandTests(GlueBase):
    def puts(self):
        return [r for r in self.srv.requests if r["method"] == "PUT"]

    async def test_buttons_and_switch_send_commands(self):
        await self.setup_entry()
        await self.find("button", "homelab/db_button_start").async_press()
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "start")
        self.assertEqual((self.puts()[-1]["route"], self.puts()[-1]["query"]), ("/1.0/instances/db/state", "project=homelab"))
        await self.find("button", "homelab/web_button_stop").async_press()
        self.assertEqual(json.loads(self.puts()[-1]["body"]), {"action": "stop", "timeout": 30, "force": False, "stateful": False})
        await self.find("button", "homelab/web_button_restart").async_press()
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "restart")
        await self.find("button", "homelab/web_button_force_stop").async_press()
        self.assertEqual((json.loads(self.puts()[-1]["body"])["action"], json.loads(self.puts()[-1]["body"])["force"]), ("stop", True))
        await self.find("switch", "homelab/db_switch_power").async_turn_on()
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "start")
        await self.find("switch", "homelab/web_switch_power").async_turn_off()
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "stop")

    async def test_switch_on_unfreezes_a_frozen_instance(self):
        frozen = pl.running_instance("web")
        frozen["status"], frozen["status_code"] = "Frozen", 110
        self.srv.instances = [frozen]
        await self.setup_entry()
        sw = self.find("switch", "homelab/web_switch_power")
        self.assertTrue(sw.is_on)
        await sw.async_turn_on()
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "unfreeze")

    async def test_server_error_becomes_homeassistant_error_and_refreshes(self):
        rt = await self.setup_entry()
        self.srv.routes[("PUT", "/1.0/instances/web/state")] = lambda q: ms.error("The instance is already running", 400)
        n = len([r for r in self.srv.requests if r["route"] == "/1.0/instances"])
        with self.assertRaisesRegex(HomeAssistantError, "already running"):
            await self.find("button", "homelab/web_button_start").async_press()
        self.assertGreater(len([r for r in self.srv.requests if r["route"] == "/1.0/instances"]), n)       # refreshed anyway

    async def test_services_by_instance_name_and_by_device(self):
        await self.setup_entry()
        reg = self.hass.services.registered
        self.assertEqual(sorted(s for d, s in reg), [])                  # registered in async_setup, not setup_entry
        await integration.async_setup(self.hass, {})
        self.assertEqual(sorted(s for d, s in reg), ["freeze_instance", "rescan_inventory", "restart_instance", "start_instance", "stop_instance", "unfreeze_instance"])

        await reg[("incus", "stop_instance")](ServiceCall({"instance": "web", "project": "homelab", "force": True, "timeout": 5}))
        self.assertEqual(json.loads(self.puts()[-1]["body"]), {"action": "stop", "timeout": 5, "force": True, "stateful": False})

        dev = next(d for d in self.hass.device_registry.devices.values()
                   if (c.DOMAIN, instance_identifier(self.entry.entry_id, "homelab/db")) in d.identifiers)
        await reg[("incus", "start_instance")](ServiceCall({"device_id": [dev.id]}))
        self.assertEqual((self.puts()[-1]["route"], json.loads(self.puts()[-1]["body"])["action"]), ("/1.0/instances/db/state", "start"))

        self.hass.entity_registry.entities["button.db_start"] = type("R", (), {"device_id": dev.id})()
        await reg[("incus", "restart_instance")](ServiceCall({"entity_id": ["button.db_start"]}))
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "restart")

        await reg[("incus", "freeze_instance")](ServiceCall({"instance": "web", "project": "homelab"}))
        self.assertEqual(json.loads(self.puts()[-1]["body"])["action"], "freeze")

    async def test_service_validation(self):
        await self.setup_entry()
        await integration.async_setup(self.hass, {})
        handler = self.hass.services.registered[("incus", "start_instance")]
        with self.assertRaises(ServiceValidationError):
            await handler(ServiceCall({}))
        second = ConfigEntry(data=self.entry.data, options=self.entry.options)
        second.runtime_data = self.entry.runtime_data
        self.hass.config_entries.entries[second.entry_id] = second
        with self.assertRaisesRegex(ServiceValidationError, "Several"):
            await handler(ServiceCall({"instance": "nonexistent", "project": "homelab"}))
        await handler(ServiceCall({"instance": "web", "project": "homelab", "config_entry": self.entry.entry_id}))


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.mkdtemp()
        self.hass = HomeAssistant()
        cf.ConfigFlow._unique_ids.clear() if hasattr(cf.ConfigFlow, "_unique_ids") else None
        from homeassistant.config_entries import ConfigFlow
        ConfigFlow._unique_ids = set()

    async def asyncTearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def flow(self):
        f = cf.IncusConfigFlow()
        f.hass = self.hass
        return f

    async def test_unix_flow(self):
        path = os.path.join(self.dir, "u.sock")
        srv = make_server(path)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        f = self.flow()
        self.assertEqual((await f.async_step_user())["menu_options"], ["https", "unix"])
        self.assertEqual((await f.async_step_unix())["type"], "form")
        res = await f.async_step_unix({c.CONF_SOCKET: path})
        self.assertEqual((res["type"], res["title"], res["data"][c.CONF_SOCKET]), ("create_entry", "Incus incus1", path))
        with self.assertLogs("custom_components.incus.client_factory", "WARNING") as logs:
            bad = await self.flow().async_step_unix({c.CONF_SOCKET: os.path.join(self.dir, "missing.sock")})
        self.assertEqual((bad["type"], bad["errors"]), ("form", {"base": "cannot_connect"}))
        self.assertIn("missing.sock", logs.output[0])                                      # the real reason is logged...
        self.assertRegex(bad["placeholders"]["error"], r"No such file|FileNotFoundError")  # ...and shown in the form
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync({"auth": "untrusted", "environment": {}})
        untrusted = await self.flow().async_step_unix({c.CONF_SOCKET: path})
        self.assertEqual(untrusted["errors"], {"base": "not_trusted"})

    async def test_https_token_flow(self):
        cert, key = tls.generate_mem_cert(client=False, add_hosts=True)
        srv = ms.TlsServer(cert, key)
        ms.serve(srv)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync({"auth": "trusted", "environment": {"server_name": "tls1", "certificate": cert}})
        srv.routes[("POST", "/1.0/certificates")] = lambda q: ms.sync(None)

        f = self.flow()
        auth = await f.async_step_https({c.CONF_URL: srv.url.replace("https://", ""), c.CONF_VERIFY: c.VERIFY_PIN})
        self.assertEqual((auth["step_id"], auth["menu_options"]), ("auth", ["token", "existing"]))
        self.assertEqual(auth["placeholders"]["fingerprint"], tls.cert_fingerprint_str(cert))
        self.assertEqual((await f.async_step_token())["step_id"], "token")
        done = await f.async_step_token({c.CONF_TRUST_TOKEN: "tok", c.CONF_CLIENT_NAME: "ha-test"})
        self.assertEqual((done["type"], done["title"]), ("create_entry", "Incus tls1"))
        d = done["data"]
        self.assertEqual((d[c.CONF_URL], d[c.CONF_VERIFY], d[c.CONF_SERVER_CERT] == cert or "BEGIN CERT" in d[c.CONF_SERVER_CERT]), (srv.url, "pin", True))
        self.assertIn("BEGIN CERTIFICATE", d[c.CONF_CLIENT_CERT])
        self.assertIn("PRIVATE KEY", d[c.CONF_CLIENT_KEY])
        post = json.loads([r for r in srv.requests if r["method"] == "POST"][-1]["body"])
        self.assertEqual((post["name"], post["type"], post["trust_token"]), ("ha-test", "client", "tok"))

        srv.routes[("POST", "/1.0/certificates")] = lambda q: ms.error("bad token", 403)
        f2 = self.flow()
        await f2.async_step_https({c.CONF_URL: srv.url, c.CONF_VERIFY: c.VERIFY_PIN})
        res = await f2.async_step_token({c.CONF_TRUST_TOKEN: "nope"})
        self.assertEqual((res["type"], res["errors"]), ("form", {"base": "invalid_token"}))

    async def test_https_existing_cert_and_duplicate_abort(self):
        cert, key = tls.generate_mem_cert(client=False, add_hosts=True)
        srv = ms.TlsServer(cert, key)
        ms.serve(srv)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync({"auth": "trusted", "environment": {"server_name": "tls1", "certificate": cert}})
        ccert, ckey = tls.generate_mem_cert(client=True, add_hosts=False)
        f = self.flow()
        await f.async_step_https({c.CONF_URL: srv.url, c.CONF_VERIFY: c.VERIFY_PIN})
        res = await f.async_step_existing({c.CONF_CLIENT_CERT: ccert, c.CONF_CLIENT_KEY: ckey})
        self.assertEqual(res["type"], "create_entry")
        from homeassistant.config_entries import ConfigFlow
        ConfigFlow._unique_ids.add(res["unique_id"])
        f2 = self.flow()
        await f2.async_step_https({c.CONF_URL: srv.url, c.CONF_VERIFY: c.VERIFY_PIN})
        from tests.ha_stubs import AbortFlow
        with self.assertRaises(AbortFlow):
            await f2.async_step_existing({c.CONF_CLIENT_CERT: ccert, c.CONF_CLIENT_KEY: ckey})

    async def test_https_unreachable_shows_error(self):
        res = await self.flow().async_step_https({c.CONF_URL: "https://127.0.0.1:1", c.CONF_VERIFY: c.VERIFY_PIN})
        self.assertEqual((res["type"], res["errors"]), ("form", {"base": "cannot_connect"}))
        self.assertRegex(res["placeholders"]["error"], r"refused|Connection|reset|Errno")

    async def test_options_menu_and_settings_keep_exclusions(self):
        entry = ConfigEntry(options={c.CONF_PROJECTS: "a", c.CONF_EXCLUDED_INSTANCES: ["a/x"], c.CONF_REVIEWED: {"instances": ["a/x"]}})
        flow = cf.IncusConfigFlow.async_get_options_flow(entry)
        flow.config_entry = entry
        self.assertEqual((await flow.async_step_init())["menu_options"], ["settings", "inventory"])
        self.assertEqual((await flow.async_step_settings())["step_id"], "settings")
        ok = await flow.async_step_settings({c.CONF_PROJECTS: "a,b", c.CONF_SCAN_INTERVAL: 20, c.CONF_VOLUME_SCAN_INTERVAL: 120, c.CONF_NOTIFY_CHANGES: False})
        self.assertEqual((ok["data"][c.CONF_PROJECTS], ok["data"][c.CONF_NOTIFY_CHANGES]), ("a,b", False))
        self.assertEqual(ok["data"][c.CONF_EXCLUDED_INSTANCES], ["a/x"])                      # exclusions survive a settings change
        self.assertEqual(ok["data"][c.CONF_REVIEWED], {"instances": ["a/x"]})
        bad = await flow.async_step_settings({c.CONF_PROJECTS: " , ", c.CONF_SCAN_INTERVAL: 20, c.CONF_VOLUME_SCAN_INTERVAL: 120, c.CONF_NOTIFY_CHANGES: True})
        self.assertEqual(bad["errors"], {c.CONF_PROJECTS: "invalid_projects"})


class InventoryGlueTests(GlueBase):
    def storage_events(self):
        return [d for e, d in self.hass.fired if any(k.endswith(("volumes", "pools")) for k in d)]

    async def test_exclusions_remove_entities_and_devices_but_not_server_counts(self):
        self.entry.options = {c.CONF_PROJECTS: "homelab", c.CONF_EXCLUDED_INSTANCES: ["homelab/db"],
                              c.CONF_EXCLUDED_VOLUMES: ["default/homelab/scratch"], c.CONF_EXCLUDED_POOLS: ["fast"]}
        await self.setup_entry()
        uids = [e.unique_id for plat in self.added.values() for e in plat]
        self.assertTrue([u for u in uids if "homelab/web_" in u])
        self.assertFalse([u for u in uids if "homelab/db_" in u])
        self.assertFalse([u for u in uids if "scratch" in u or "pool_fast" in u])
        self.assertTrue([u for u in uids if "vol_default/homelab/data" in u])
        self.assertEqual(self.find("sensor", "server_instances_total").native_value, 2)          # the server still has 2
        idents = [i for d in self.hass.device_registry.devices.values() for _, i in d.identifiers]
        self.assertNotIn(instance_identifier(self.entry.entry_id, "homelab/db"), idents)

    async def test_first_setup_is_silent_and_saves_a_baseline(self):
        await self.setup_entry()
        self.assertEqual((self.hass.fired, self.hass.notifications), ([], []))
        import sys
        saved = sys.modules["homeassistant"]._persisted[f"incus_{self.entry.entry_id}_inventory"]
        self.assertEqual(saved["instances"], ["homelab/db", "homelab/web"])
        self.assertEqual((saved["pools"], saved["volumes"]), (["default", "fast"], ["default/homelab/data", "default/homelab/scratch"]))

    async def test_changes_fire_event_and_notification_once(self):
        rt = await self.setup_entry()
        self.srv.instances = [pl.running_instance("web"), pl.running_instance("cache")]          # db removed, cache added
        self.srv.volumes["default"] = [v for v in self.srv.volumes["default"] if v["name"] != "scratch"] + \
            [{"name": "fresh", "type": "custom", "project": "homelab", "config": {}, "used_by": []}]
        self.srv.routes[("GET", "/1.0/storage-pools/default/volumes/custom/fresh/state")] = lambda q: ms.sync({"usage": {"used": 1, "total": 0}})
        await rt.instances.async_refresh()
        await rt.volumes.async_refresh()

        events = dict((tuple(sorted(d)), d) for _, d in self.hass.fired)
        inst = next(d for d in events.values() if "added_instances" in d)
        self.assertEqual((inst["added_instances"], inst["removed_instances"], inst["server"], inst["entry_id"]),
                         (["homelab/cache"], ["homelab/db"], "incus1", self.entry.entry_id))
        sto = next(d for d in events.values() if "added_volumes" in d)
        self.assertEqual((sto["added_volumes"], sto["removed_volumes"]), (["default/homelab/fresh"], ["default/homelab/scratch"]))
        self.assertTrue(all(e == "incus_inventory_changed" for e, _ in self.hass.fired))
        msgs = " | ".join(n["message"] for n in self.hass.notifications)
        self.assertIn("New instances: homelab/cache", msgs)
        self.assertIn("Removed instances: homelab/db", msgs)
        self.assertIn("New custom volumes: homelab/fresh (default)", msgs)
        self.assertIn("Rescan and choose what to monitor", msgs)
        self.assertEqual({n["id"] for n in self.hass.notifications}, {f"incus_{self.entry.entry_id}_inventory"})
        self.find("sensor", "homelab/cache_status")                                              # entities for the new instance appeared
        self.find("sensor", "vol_default/homelab/fresh_used")

        fired, notes = len(self.hass.fired), len(self.hass.notifications)
        await rt.instances.async_refresh()                                                       # unchanged: quiet
        await rt.volumes.async_refresh()
        self.assertEqual((len(self.hass.fired), len(self.hass.notifications)), (fired, notes))

    async def test_notifications_can_be_switched_off_event_still_fires(self):
        self.entry.options = {c.CONF_PROJECTS: "homelab", c.CONF_NOTIFY_CHANGES: False}
        rt = await self.setup_entry()
        self.srv.instances.append(pl.stopped_instance("extra"))
        await rt.instances.async_refresh()
        self.assertEqual(len(self.hass.fired), 1)
        self.assertEqual(self.hass.notifications, [])

    async def test_changes_while_ha_was_down_are_reported_after_restart(self):
        await self.setup_entry()                                                                 # baseline saved
        self.srv.instances.append(pl.stopped_instance("while-down"))
        hass2 = HomeAssistant()
        entry2 = ConfigEntry(data=self.entry.data, options=self.entry.options)
        entry2.entry_id = self.entry.entry_id                                                    # same entry -> same stored baseline
        hass2.config_entries.entries[entry2.entry_id] = entry2
        self.assertTrue(await integration.async_setup_entry(hass2, entry2))
        self.assertEqual([d["added_instances"] for _, d in hass2.fired if "added_instances" in d], [["homelab/while-down"]])
        self.assertTrue(hass2.notifications)

    async def test_removing_the_entry_deletes_the_stored_baseline(self):
        import sys
        await self.setup_entry()
        store = sys.modules["homeassistant"]._persisted
        self.assertIn(f"incus_{self.entry.entry_id}_inventory", store)
        await integration.async_remove_entry(self.hass, self.entry)
        self.assertNotIn(f"incus_{self.entry.entry_id}_inventory", store)

    async def test_rescan_button_and_service(self):
        await self.setup_entry()
        self.srv.instances.append(pl.stopped_instance("late"))
        n = len([r for r in self.srv.requests if r["route"] == "/1.0/instances"])
        await self.find("button", "server_button_rescan").async_press()
        self.assertGreater(len([r for r in self.srv.requests if r["route"] == "/1.0/instances"]), n)
        self.find("sensor", "homelab/late_status")
        self.assertTrue([d for _, d in self.hass.fired if d.get("added_instances") == ["homelab/late"]])

        await integration.async_setup(self.hass, {})
        self.srv.instances.append(pl.stopped_instance("later"))
        await self.hass.services.registered[("incus", "rescan_inventory")](ServiceCall({}))
        self.find("sensor", "homelab/later_status")
        with self.assertRaises(ServiceValidationError):
            await self.hass.services.registered[("incus", "rescan_inventory")](ServiceCall({"config_entry": "nope"}))


class OptionsInventoryTests(GlueBase):
    async def flow(self, loaded=True):
        if loaded:
            await self.setup_entry()
        f = cf.IncusConfigFlow.async_get_options_flow(self.entry)
        f.config_entry, f.hass = self.entry, self.hass
        return f

    def fields(self, form):
        return {k.key: (k.default, [(o["value"], o["label"]) for o in v.a[0].k["options"]]) for k, v in form["schema"].items()}

    async def test_checklist_lists_everything_checked_by_default(self):
        f = await self.flow()
        form = await f.async_step_inventory()
        self.assertEqual(form["step_id"], "inventory")
        fields = self.fields(form)
        self.assertEqual(fields[c.CONF_MONITOR_INSTANCES][0], ["homelab/db", "homelab/web"])
        self.assertEqual([lab for _, lab in fields[c.CONF_MONITOR_INSTANCES][1]], ["homelab/db", "homelab/web"])
        self.assertEqual(fields[c.CONF_MONITOR_POOLS][0], ["default", "fast"])
        self.assertEqual(fields[c.CONF_MONITOR_VOLUMES][0], ["default/homelab/data", "default/homelab/scratch"])
        self.assertEqual([lab for _, lab in fields[c.CONF_MONITOR_VOLUMES][1]], ["homelab/data (default)", "homelab/scratch (default)"])
        self.assertIn("2 instances, 2 storage pools, 2 custom volumes", form["placeholders"]["summary"])
        self.assertNotIn("new since", form["placeholders"]["summary"])

    async def test_new_items_are_flagged_and_exclusions_prechecked_off(self):
        self.entry.options = {c.CONF_PROJECTS: "homelab", c.CONF_EXCLUDED_INSTANCES: ["homelab/db"],
                              c.CONF_REVIEWED: {"instances": ["homelab/web"], "pools": ["default", "fast"], "volumes": ["default/homelab/data"]}}
        f = await self.flow()
        form = await f.async_step_inventory()
        fields = self.fields(form)
        self.assertEqual(fields[c.CONF_MONITOR_INSTANCES][0], ["homelab/web"])                     # db excluded -> unchecked
        labels = dict(fields[c.CONF_MONITOR_INSTANCES][1])
        self.assertEqual((labels["homelab/db"], labels["homelab/web"]), ("homelab/db (new)", "homelab/web"))
        self.assertEqual(dict(fields[c.CONF_MONITOR_VOLUMES][1])["default/homelab/scratch"], "homelab/scratch (default) (new)")
        self.assertIn("2 new since your last review", form["placeholders"]["summary"])

    async def test_saving_stores_exclusions_and_reviewed_inventory(self):
        f = await self.flow()
        res = await f.async_step_inventory({c.CONF_MONITOR_INSTANCES: ["homelab/web"], c.CONF_MONITOR_POOLS: ["default"],
                                            c.CONF_MONITOR_VOLUMES: ["default/homelab/data"]})
        o = res["data"]
        self.assertEqual((o[c.CONF_EXCLUDED_INSTANCES], o[c.CONF_EXCLUDED_POOLS], o[c.CONF_EXCLUDED_VOLUMES]),
                         (["homelab/db"], ["fast"], ["default/homelab/scratch"]))
        self.assertEqual(o[c.CONF_REVIEWED]["instances"], ["homelab/db", "homelab/web"])
        self.assertEqual(o[c.CONF_PROJECTS], "homelab")                                             # other options preserved

    async def test_works_without_a_loaded_entry_and_reports_failures(self):
        f = await self.flow(loaded=False)                                                          # no runtime_data: builds its own client
        self.assertEqual((await f.async_step_inventory())["step_id"], "inventory")
        self.srv.routes[("GET", "/1.0/instances")] = lambda q: ms.error("boom", 500)
        res = await f.async_step_inventory()
        self.assertEqual((res["type"], res["reason"]), ("abort", "cannot_connect"))
        self.assertIn("boom", res["placeholders"]["error"])

    async def test_empty_server_aborts_and_pool_less_server_hides_empty_fields(self):
        self.srv.instances = []
        self.srv.routes[("GET", "/1.0/storage-pools")] = lambda q: ms.sync([])
        f = await self.flow(loaded=False)
        self.assertEqual((await f.async_step_inventory())["reason"], "nothing_to_select")
        self.srv.instances = [pl.stopped_instance("only")]
        form = await f.async_step_inventory()
        self.assertEqual(list(self.fields(form)), [c.CONF_MONITOR_INSTANCES])

    async def test_unchecking_removes_entities_after_reload(self):
        f = await self.flow()
        res = await f.async_step_inventory({c.CONF_MONITOR_INSTANCES: ["homelab/web"], c.CONF_MONITOR_POOLS: ["default", "fast"],
                                            c.CONF_MONITOR_VOLUMES: ["default/homelab/data", "default/homelab/scratch"]})
        self.entry.options = res["data"]                      # HA stores the options, then the update listener reloads the entry
        self.hass.config_entries.added.clear()
        self.hass.device_registry.devices.clear()
        await self.setup_entry()
        self.assertFalse([e for e in self.added["sensor"] if "homelab/db_" in e.unique_id])
        self.assertTrue([e for e in self.added["sensor"] if "homelab/web_" in e.unique_id])


if __name__ == "__main__":
    unittest.main()
