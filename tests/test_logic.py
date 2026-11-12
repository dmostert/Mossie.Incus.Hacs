import asyncio  # noqa: F401
import json
import os
import shutil
import tempfile
import unittest

from tests import mock_server as ms
from tests import payloads as pl
from tests._loader import load

data = load("data")
fetch = load("fetch")
commands = load("commands")
factory = load("client_factory")
models = load("incus_client").models
IncusError = load("incus_client").IncusError


def full(d):
    return models.InstanceFull.from_dict(d)


class DataLayerTests(unittest.TestCase):
    def test_running_instance_snapshot(self):
        d = pl.running_instance()
        d["expanded_config"]["image.description"] = "docker.io/valkey/valkey (OCI)"
        s = data.parse_instance(full(d), cpu_percent=12.5)
        self.assertEqual((s.key, s.display_name, s.is_running, s.status), ("homelab/web", "homelab/web", True, "Running"))
        self.assertEqual((s.cpu_usage_ns, s.cpu_allocated_ns, s.cpu_percent), (153_597_000, 16_000_000_000, 12.5))
        self.assertEqual((s.memory_usage, s.memory_total, s.swap_usage, s.memory_percent), (33_075_200, 30_565_136_000, 0, 0.11))
        self.assertEqual((s.processes, s.pid), (10, 17240))
        self.assertEqual(s.started_at.utcoffset().total_seconds(), 7200)
        self.assertEqual(sorted(s.networks), ["eth0", "lo"])
        self.assertEqual(s.networks["eth0"].tx_bytes, 2151)
        self.assertEqual(s.networks["eth0"].hwaddr, "00:16:3e:aa:bb:cc")
        self.assertIsNone(s.ipv4)                       # only a link-local v6 address in the sample
        self.assertIsNone(s.ipv6)
        self.assertEqual(s.image, "docker.io/valkey/valkey (OCI)")
        self.assertEqual(s.disks["root"].usage, None)   # -1 -> unknown

    def test_ip_selection_ignores_loopback_and_link_local(self):
        d = pl.running_instance()
        d["state"]["network"]["eth0"]["addresses"] += [
            {"address": "10.1.2.3", "family": "inet", "netmask": "24", "scope": "global"},
            {"address": "fd42::5", "family": "inet6", "netmask": "64", "scope": "global"}]
        d["state"]["network"]["lo"]["addresses"].append({"address": "10.9.9.9", "family": "inet", "netmask": "8", "scope": "global"})
        s = data.parse_instance(full(d))
        self.assertEqual((s.ipv4, s.ipv6), ("10.1.2.3", "fd42::5"))

    def test_stopped_instance_snapshot(self):
        s = data.parse_instance(full(pl.stopped_instance()))
        self.assertEqual((s.status, s.is_running, s.started_at, s.networks, s.ipv4, s.memory_percent), ("Stopped", False, None, {}, None, None))
        self.assertEqual((s.limits, s.profiles), ({}, ("default",)))
        self.assertEqual(s.created_at.year, 2026)

    def test_default_project_display_name(self):
        d = pl.stopped_instance(project="default")
        self.assertEqual(data.parse_instance(full(d)).display_name, "my-container")

    def test_limits_from_expanded_config(self):
        d = pl.stopped_instance()
        d["expanded_config"]["limits.cpu"] = "2"
        d["expanded_config"]["limits.memory"] = "1GiB"
        self.assertEqual(data.parse_instance(full(d)).limits, {"limits.cpu": "2", "limits.memory": "1GiB"})

    def test_cpu_tracker(self):
        t = data.CpuTracker()
        self.assertIsNone(t.percent("a", 1_000_000_000, 100.0, True))             # first sample
        self.assertEqual(t.percent("a", 2_000_000_000, 102.0, True), 50.0)        # 1s of CPU in 2s
        self.assertEqual(t.percent("a", 6_000_000_000, 103.0, True), 400.0)       # multi-core
        self.assertIsNone(t.percent("a", 100, 104.0, True))                       # counter reset (restart)
        self.assertEqual(t.percent("a", 1_000_000_100, 105.0, True), 100.0)
        self.assertIsNone(t.percent("a", 5, 106.0, False))                        # stopped -> forget
        self.assertIsNone(t.percent("a", 10, 107.0, True))                        # first sample again
        t.forget_missing([])
        self.assertIsNone(t.percent("a", 20, 108.0, True))

    def test_build_instances_data(self):
        tracker = data.CpuTracker()
        insts = [full(pl.running_instance("web")), full(pl.stopped_instance("db"))]
        server = models.Server.from_dict(pl.SERVER)
        d1 = data.build_instances_data(server, insts, tracker, 100.0)
        self.assertEqual((d1.server.name, d1.server.version, d1.server.instances_total, d1.server.instances_running), ("incus1", "6.5", 2, 1))
        self.assertIsNone(d1.instances["homelab/web"].cpu_percent)
        d2 = data.build_instances_data(server, [full(pl.running_instance("web", cpu_ns=153_597_000 + 500_000_000))], tracker, 102.0)
        self.assertEqual(d2.instances["homelab/web"].cpu_percent, 25.0)
        self.assertEqual(list(d2.instances), ["homelab/web"])

    def test_parse_used_by(self):
        self.assertEqual(data.parse_used_by(["/1.0/instances/web?project=p", "/1.0/instances/db", "/1.0/profiles/x", "/1.0/images/abc"]),
                         ["p/web", "default/db"])
        self.assertEqual(data.parse_used_by(None), [])

    def test_volume_builders(self):
        vol = models.StorageVolume.from_dict(pl.VOLUMES["default"][0])
        st = models.StorageVolumeState.from_dict(pl.VOLUME_STATE["data"])
        v = data.build_volume("default", vol, st)
        self.assertEqual((v.key, v.used, v.quota, v.percent, v.free), ("default/homelab/data", 3_000_000_000, 10_737_418_240, 27.94, 7_737_418_240))
        self.assertEqual((v.used_by, v.location, v.created_at.year), (("homelab/web",), None, 2026))
        no_state = data.build_volume("default", vol, None)       # quota falls back to config "size"
        self.assertEqual((no_state.used, no_state.quota, no_state.percent), (None, 10 * 1024 ** 3, None))
        unlimited = data.build_volume("default", models.StorageVolume.from_dict(pl.VOLUMES["default"][3]),
                                      models.StorageVolumeState.from_dict(pl.VOLUME_STATE["scratch"]))
        self.assertEqual((unlimited.used, unlimited.quota, unlimited.percent, unlimited.free), (5, None, None, None))

    def test_pool_builder(self):
        p = data.build_pool(models.StoragePool.from_dict(pl.POOLS[0]), models.ResourcesStoragePool.from_dict(pl.RESOURCES["default"]), 2)
        self.assertEqual((p.space_used, p.space_total, p.space_free, p.space_percent, p.volume_count, p.driver), (20e9, 100e9, 80e9, 20.0, 2, "zfs"))
        none = data.build_pool(models.StoragePool.from_dict(pl.POOLS[1]), None, 0)
        self.assertEqual((none.space_used, none.space_free, none.space_percent), (None, None, None))


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "u.sock")
        self.srv = ms.UnixServer(self.path)
        ms.serve(self.srv)
        r = self.srv.routes
        r[("GET", "/1.0")] = lambda q: ms.sync(pl.SERVER)
        r[("GET", "/1.0/instances")] = lambda q: ms.sync([pl.running_instance("web"), pl.stopped_instance("db")])
        r[("GET", "/1.0/storage-pools")] = lambda q: ms.sync(pl.POOLS)
        for p in ("default", "fast"):
            r[("GET", f"/1.0/storage-pools/{p}/resources")] = (lambda p: lambda q: ms.sync(pl.RESOURCES[p]) if p in pl.RESOURCES else ms.error("no", 500))(p)
            r[("GET", f"/1.0/storage-pools/{p}/volumes")] = (lambda p: lambda q: ms.sync(pl.VOLUMES[p]))(p)
        for v, st in pl.VOLUME_STATE.items():
            r[("GET", f"/1.0/storage-pools/default/volumes/custom/{v}/state")] = (lambda st: lambda q: ms.sync(st))(st)
        self.c = factory.build_client({"connection": "unix", "socket_path": self.path})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_parse_projects(self):
        p = fetch.parse_projects
        self.assertEqual([p(None), p(""), p("a, b ,a"), p("default,*"), p(["x", " y "])], [["default"], ["default"], ["a", "b"], ["*"], ["x", "y"]])

    def test_fetch_instances_per_project_and_all(self):
        out = fetch.fetch_instances(self.c, ["default", "homelab"])
        self.assertEqual(len(out), 4)
        q = [r["query"] for r in self.srv.requests if r["route"] == "/1.0/instances"]
        self.assertEqual(q, ["recursion=2&project=default", "recursion=2&project=homelab"])
        fetch.fetch_instances(self.c, ["*"])
        self.assertEqual(self.srv.requests[-1]["query"], "recursion=2&all-projects=true")

    def test_fetch_volumes_custom_only_with_usage(self):
        v = fetch.fetch_volumes(self.c, ["homelab"])
        self.assertEqual(sorted(v.volumes), ["default/homelab/data", "default/homelab/scratch"])      # container/image volumes skipped
        self.assertEqual(v.volumes["default/homelab/data"].used, 3_000_000_000)
        self.assertEqual(v.pools["default"].volume_count, 2)
        self.assertEqual(v.pools["default"].space_percent, 20.0)
        self.assertIsNone(v.pools["fast"].space_used)                                                  # resources call failed -> None
        state_reqs = [r for r in self.srv.requests if r["route"].endswith("/state")]
        self.assertTrue(all("project=homelab" in r["query"] for r in state_reqs))

    def test_fetch_volumes_all_projects(self):
        fetch.fetch_volumes(self.c, ["*"])
        lists = [r for r in self.srv.requests if r["route"].endswith("/volumes")]
        self.assertTrue(all("all-projects=true" in r["query"] for r in lists))

    def test_volume_state_failure_is_tolerated(self):
        self.srv.routes[("GET", "/1.0/storage-pools/default/volumes/custom/data/state")] = lambda q: ms.error("boom", 500)
        v = fetch.fetch_volumes(self.c, ["homelab"])
        self.assertIsNone(v.volumes["default/homelab/data"].used)
        self.assertEqual(v.volumes["default/homelab/data"].quota, 10 * 1024 ** 3)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "u.sock")
        self.srv = ms.UnixServer(self.path)
        ms.serve(self.srv)
        self.srv.routes[("GET", "/1.0")] = lambda q: ms.sync(pl.SERVER)
        self.c = factory.build_client({"connection": "unix", "socket_path": self.path})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_actions_send_expected_requests(self):
        self.srv.routes[("PUT", "/1.0/instances/web/state")] = lambda q: ms.async_op("a1")
        self.srv.routes[("GET", "/1.0/operations/a1/wait")] = lambda q: ms.sync({"id": "a1", "status_code": 200})
        for action, force, timeout in (("start", False, 30), ("stop", True, 5), ("restart", False, 60)):
            commands.run_instance_action(self.c, "homelab", "web", action, force=force, timeout=timeout)
            put = [r for r in self.srv.requests if r["method"] == "PUT"][-1]
            self.assertEqual(json.loads(put["body"]), {"action": action, "timeout": timeout, "force": force, "stateful": False})
            self.assertEqual(put["query"], "project=homelab")

    def test_server_error_is_raised_with_message(self):
        self.srv.routes[("PUT", "/1.0/instances/web/state")] = lambda q: ms.error("The instance is already running", 400)
        with self.assertRaisesRegex(IncusError, "already running"):
            commands.run_instance_action(self.c, "default", "web", "start")

    def test_failed_operation_is_raised(self):
        self.srv.routes[("PUT", "/1.0/instances/web/state")] = lambda q: ms.async_op("a2")
        self.srv.routes[("GET", "/1.0/operations/a2/wait")] = lambda q: ms.sync({"id": "a2", "status_code": 400, "err": "Failed to stop"})
        with self.assertRaisesRegex(IncusError, "Failed to stop"):
            commands.run_instance_action(self.c, "default", "web", "stop")

    def test_no_wait_and_bad_action(self):
        self.srv.routes[("PUT", "/1.0/instances/web/state")] = lambda q: ms.async_op("a3")
        commands.run_instance_action(self.c, "default", "web", "freeze", wait=False)
        self.assertFalse([r for r in self.srv.requests if r["route"].endswith("/wait")])
        with self.assertRaises(ValueError):
            commands.run_instance_action(self.c, "default", "web", "explode")


class FactoryTests(unittest.TestCase):
    def test_normalize_url(self):
        n = factory.normalize_url
        self.assertEqual([n("host:8443"), n(" https://h:1/ "), n("10.0.0.2")], ["https://host:8443", "https://h:1", "https://10.0.0.2"])

    def test_error_messages_name_the_real_problem(self):
        with self.assertRaisesRegex(factory.CannotConnect, "Only https"):
            factory.validate_connection({"connection": "https", "url": "http://x:8443", "verify": "skip"})
        with self.assertRaises(factory.CannotConnect) as cm:                         # malformed pinned certificate
            factory.validate_connection({"connection": "https", "url": "https://127.0.0.1:1", "verify": "pin", "server_cert": "garbage"})
        self.assertIn("ValueError", str(cm.exception))
        with self.assertRaises(factory.CannotConnect) as cm:                         # malformed client certificate/key
            factory.validate_connection({"connection": "https", "url": "https://127.0.0.1:1", "verify": "skip",
                                         "client_cert": "garbage", "client_key": "garbage"})
        self.assertTrue(str(cm.exception))
        with self.assertRaises(factory.CannotConnect) as cm:                         # nothing listening
            factory.validate_connection({"connection": "https", "url": "https://127.0.0.1:1", "verify": "skip"})
        self.assertRegex(str(cm.exception), r"refused|Errno|Connection")

    def test_unix_validation(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "u.sock")
        srv = ms.UnixServer(path)
        ms.serve(srv)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync(pl.SERVER)
        info = factory.validate_connection({"connection": "unix", "socket_path": path})
        self.assertEqual((info.name, info.version, info.auth), ("incus1", "6.5", "trusted"))
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync({"auth": "untrusted", "environment": {}})
        with self.assertRaises(factory.NotTrusted):
            factory.validate_connection({"connection": "unix", "socket_path": path})
        with self.assertRaises(factory.CannotConnect):
            factory.validate_connection({"connection": "unix", "socket_path": os.path.join(d, "missing.sock")})

    def test_tls_pin_fetch_validate_and_token_registration(self):
        cert, key = factory.tls.generate_mem_cert(client=False, add_hosts=True)
        srv = ms.TlsServer(cert, key)
        ms.serve(srv)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        srv.routes[("GET", "/1.0")] = lambda q: ms.sync({"auth": "trusted", "environment": {"server_name": "n", "certificate": cert}})

        pem = factory.fetch_server_certificate(srv.url)
        self.assertEqual(factory.tls.cert_fingerprint_str(pem), factory.tls.cert_fingerprint_str(cert))
        ccert, ckey = factory.generate_client_identity()
        base = {"connection": "https", "url": srv.url, "verify": "pin", "server_cert": pem, "client_cert": ccert, "client_key": ckey}
        info = factory.validate_connection(base)
        self.assertEqual((info.name, info.fingerprint), ("n", factory.tls.cert_fingerprint_str(cert)))

        srv.routes[("POST", "/1.0/certificates")] = lambda q: ms.sync(None)
        factory.register_with_token(base, " tok123 ", "home-assistant")
        body = json.loads([r for r in srv.requests if r["method"] == "POST"][-1]["body"])
        self.assertEqual((body["name"], body["type"], body["trust_token"]), ("home-assistant", "client", "tok123"))

        srv.routes[("POST", "/1.0/certificates")] = lambda q: ms.error("Invalid token", 403)
        with self.assertRaises(factory.InvalidToken):
            factory.register_with_token(base, "bad", "x")

        other, _ = factory.tls.generate_mem_cert(client=False, add_hosts=False)
        with self.assertRaises(factory.CannotConnect):
            factory.validate_connection({**base, "server_cert": other})
        self.assertEqual(factory.connection_args({**base, "verify": "skip"}).tls_server_cert, "")
        self.assertTrue(factory.connection_args({**base, "verify": "skip"}).insecure_skip_verify)


if __name__ == "__main__":
    unittest.main()
