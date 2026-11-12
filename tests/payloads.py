"""Realistic API payloads built from the real samples in fixtures/."""
import copy
import json
import os

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(n):
    with open(os.path.join(FIX, n)) as f:
        return json.load(f)


STOPPED = _load("instance_full.json")            # my-container, project homelab, Stopped
RUNNING_STATE = _load("instance_state.json")


def running_instance(name="web", project="homelab", cpu_ns=153_597_000):
    d = copy.deepcopy(STOPPED)
    d.update(name=name, project=project, status="Running", status_code=103)
    d["state"] = copy.deepcopy(RUNNING_STATE)
    d["state"]["cpu"]["usage"] = cpu_ns
    return d


def stopped_instance(name="my-container", project="homelab"):
    d = copy.deepcopy(STOPPED)
    d.update(name=name, project=project)
    return d


SERVER = {"api_extensions": ["instance_sftp"], "api_version": "1.0", "auth": "trusted", "public": False,
          "environment": {"server_name": "incus1", "server_version": "6.5", "server_clustered": False, "certificate": None}}

POOLS = [{"name": "default", "driver": "zfs", "status": "Created", "description": "main pool"},
         {"name": "fast", "driver": "dir", "status": "Created"}]
RESOURCES = {"default": {"space": {"used": 20_000_000_000, "total": 100_000_000_000}, "inodes": {"used": 10, "total": 1000}}}

VOLUMES = {
    "default": [
        {"name": "data", "type": "custom", "content_type": "filesystem", "project": "homelab", "location": "none", "description": "app data",
         "config": {"size": "10GiB"}, "created_at": "2026-10-01T10:00:00.123456789Z",
         "used_by": ["/1.0/instances/web?project=homelab", "/1.0/profiles/default"]},
        {"name": "web", "type": "container", "project": "homelab"},                         # instance volume: must be ignored
        {"name": "abc123", "type": "image", "project": "homelab"},                          # image volume: must be ignored
        {"name": "scratch", "type": "custom", "content_type": "block", "project": "homelab", "config": {}, "used_by": []},
    ],
    "fast": [],
}
VOLUME_STATE = {"data": {"usage": {"used": 3_000_000_000, "total": 10_737_418_240}}, "scratch": {"usage": {"used": 5, "total": 0}}}
