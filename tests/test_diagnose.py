import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests import mock_server as ms
from tests import payloads as pl

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "diagnose.py")


def run(*args):
    p = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=60)
    return p.returncode, p.stdout


class DiagnoseTests(unittest.TestCase):
    def test_unix_success_and_failures(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "u.sock")
        srv = ms.UnixServer(path)
        ms.serve(srv)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        r = srv.routes
        r[("GET", "/1.0")] = lambda q: ms.sync(pl.SERVER)
        r[("GET", "/1.0/instances")] = lambda q: ms.sync([pl.running_instance("web")])
        r[("GET", "/1.0/storage-pools")] = lambda q: ms.sync([])
        code, out = run("--socket", path)
        self.assertEqual(code, 0, out)
        self.assertIn("auth='trusted'", out)
        self.assertIn("instances: 1 found", out)
        self.assertIn("All checks passed", out)

        r[("GET", "/1.0")] = lambda q: ms.sync({"auth": "untrusted", "environment": {}})
        code, out = run("--socket", path)
        self.assertEqual(code, 1)
        self.assertIn("incus config trust add", out)

        code, out = run("--socket", os.path.join(d, "missing.sock"))
        self.assertEqual(code, 1)
        self.assertIn("does not exist", out)

    def test_refused_connection_hint(self):
        code, out = run("https://127.0.0.1:1")
        self.assertEqual(code, 1)
        self.assertIn("core.https_address", out)

    def test_wrong_scheme(self):
        code, out = run("http://127.0.0.1:8443")
        self.assertEqual(code, 1)
        self.assertIn("HTTPS only", out)


if __name__ == "__main__":
    unittest.main()
