import os
import shutil
import tempfile
import unittest

from tests import mock_server as ms
from tests import payloads as pl
from tests._loader import load

inv = load("inventory")
data = load("data")
fetch = load("fetch")
factory = load("client_factory")
models = load("incus_client").models


class InventoryModelTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual([inv.label("instances", "default/web"), inv.label("instances", "homelab/web"),
                          inv.label("pools", "default"), inv.label("volumes", "default/default/data"),
                          inv.label("volumes", "default/homelab/data")],
                         ["web", "homelab/web", "default", "data (default)", "homelab/data (default)"])

    def test_tracker_first_update_is_silent_then_reports_changes(self):
        t = inv.InventoryTracker()
        self.assertTrue(t.update(instances={"d/a", "d/b"}).is_empty)               # baseline
        ch = t.update(instances={"d/b", "d/c"})
        self.assertEqual((dict(ch.added), dict(ch.removed)), ({"instances": ("d/c",)}, {"instances": ("d/a",)}))
        self.assertTrue(t.update(instances={"d/b", "d/c"}).is_empty)                # no change, no noise
        self.assertTrue(t.update(pools={"p"}, volumes={"p/d/v"}).is_empty)          # other kinds start their own baseline
        ch = t.update(pools={"p", "q"}, volumes=set())
        self.assertEqual((dict(ch.added), dict(ch.removed)), ({"pools": ("q",)}, {"volumes": ("p/d/v",)}))
        self.assertEqual(t.current.to_dict(), {"instances": ["d/b", "d/c"], "pools": ["p", "q"], "volumes": []})

    def test_baseline_from_storage_reports_changes_on_first_update(self):
        base = inv.Inventory.from_dict({"instances": ["d/a"], "pools": ["p"], "volumes": []})
        t = inv.InventoryTracker(base)
        ch = t.update(instances={"d/a", "d/new"})
        self.assertEqual(dict(ch.added), {"instances": ("d/new",)})
        self.assertIsNone(inv.Inventory.from_dict(None))
        self.assertIsNone(inv.Inventory.from_dict({}))

    def test_describe_and_event_data(self):
        ch = inv.InventoryChange(added={"instances": ("homelab/web", "default/db"), "volumes": ("p/default/v",)}, removed={"pools": ("old",)})
        self.assertEqual(ch.describe(), "New instances: homelab/web, db\nNew custom volumes: v (p)\nRemoved storage pools: old")
        self.assertEqual(ch.to_event_data(), {"added_instances": ["homelab/web", "default/db"], "added_volumes": ["p/default/v"], "removed_pools": ["old"]})
        self.assertFalse(ch.is_empty)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            inv.InventoryTracker().update(bogus=set())

    def test_new_since(self):
        self.assertEqual(inv.new_since(None, "instances", ["a"]), set())            # never reviewed: nothing flagged
        self.assertEqual(inv.new_since({"instances": ["a"]}, "instances", ["a", "b"]), {"b"})
        self.assertEqual(inv.new_since({"instances": ["a"]}, "pools", ["p"]), {"p"})


class ExclusionTests(unittest.TestCase):
    def test_build_instances_data_keeps_server_counts_but_filters(self):
        server = models.Server.from_dict(pl.SERVER)
        insts = [models.InstanceFull.from_dict(pl.running_instance("web")), models.InstanceFull.from_dict(pl.stopped_instance("db"))]
        d = data.build_instances_data(server, insts, data.CpuTracker(), 1.0, excluded=frozenset({"homelab/db"}))
        self.assertEqual(list(d.instances), ["homelab/web"])
        self.assertEqual(d.discovered, frozenset({"homelab/web", "homelab/db"}))
        self.assertEqual((d.server.instances_total, d.server.instances_running), (2, 1))


class FetchExclusionTests(unittest.TestCase):
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
            r[("GET", f"/1.0/storage-pools/{p}/resources")] = (lambda p: lambda q: ms.sync(pl.RESOURCES.get(p) or {"space": {"used": 1, "total": 2}}))(p)
            r[("GET", f"/1.0/storage-pools/{p}/volumes")] = (lambda p: lambda q: ms.sync(pl.VOLUMES[p]))(p)
        for v, st in pl.VOLUME_STATE.items():
            r[("GET", f"/1.0/storage-pools/default/volumes/custom/{v}/state")] = (lambda st: lambda q: ms.sync(st))(st)
        self.c = factory.build_client({"connection": "unix", "socket_path": self.path})

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def routes(self):
        return [r["route"] for r in self.srv.requests]

    def test_excluded_volume_is_discovered_but_costs_no_state_request(self):
        v = fetch.fetch_volumes(self.c, ["homelab"], frozenset(), frozenset({"default/homelab/scratch"}))
        self.assertEqual(sorted(v.volumes), ["default/homelab/data"])
        self.assertEqual(v.discovered_volumes, frozenset({"default/homelab/data", "default/homelab/scratch"}))
        self.assertEqual(v.pools["default"].volume_count, 2)                      # still counts everything that exists
        self.assertNotIn("/1.0/storage-pools/default/volumes/custom/scratch/state", self.routes())
        self.assertIn("/1.0/storage-pools/default/volumes/custom/data/state", self.routes())

    def test_excluded_pool_skips_resources_and_volume_state(self):
        v = fetch.fetch_volumes(self.c, ["homelab"], frozenset({"default"}), frozenset())
        self.assertEqual((sorted(v.pools), v.volumes), (["fast"], {}))
        self.assertEqual(v.discovered_pools, frozenset({"default", "fast"}))
        self.assertEqual(v.discovered_volumes, frozenset({"default/homelab/data", "default/homelab/scratch"}))
        self.assertNotIn("/1.0/storage-pools/default/resources", self.routes())
        self.assertFalse([r for r in self.routes() if r.endswith("/state")])

    def test_scan_inventory_is_cheap_and_complete(self):
        result = fetch.scan_inventory(self.c, ["homelab"])
        self.assertEqual(sorted(result.instances), ["homelab/db", "homelab/web"])
        self.assertEqual(sorted(result.pools), ["default", "fast"])
        self.assertEqual(sorted(result.volumes), ["default/homelab/data", "default/homelab/scratch"])      # custom only
        self.assertFalse([r for r in self.routes() if r.endswith(("/state", "/resources"))])
        inst_q = [r["query"] for r in self.srv.requests if r["route"] == "/1.0/instances"]
        self.assertEqual(inst_q, ["recursion=1&project=homelab"])

    def test_scan_inventory_all_projects(self):
        fetch.scan_inventory(self.c, ["*"])
        self.assertTrue(all("all-projects=true" in r["query"] for r in self.srv.requests if r["route"].endswith(("/instances", "/volumes"))))


if __name__ == "__main__":
    unittest.main()
