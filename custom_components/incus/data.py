"""Home-Assistant-independent data layer: turns Incus API models into flat snapshots the entities read from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

from .incus_client import models
from .incus_client.helpers import StatusCode

RUNNING = StatusCode.RUNNING
FROZEN = StatusCode.FROZEN


def instance_key(project: str, name: str) -> str:
    return f"{project}/{name}"


def parse_used_by(urls: Iterable[str] | None) -> list[str]:
    """["/1.0/instances/web?project=p", "/1.0/profiles/x"] -> ["p/web"] (instances only)."""
    out = []
    for u in urls or []:
        path, _, query = u.partition("?")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 3 and parts[1] == "instances":
            project = "default"
            for kv in query.split("&"):
                if kv.startswith("project="):
                    project = kv[8:]
            out.append(instance_key(project, parts[2]))
    return out


@dataclass(frozen=True)
class NicSnapshot:
    name: str
    hwaddr: str | None = None
    state: str | None = None
    type: str | None = None
    mtu: int | None = None
    host_name: str | None = None
    rx_bytes: int = 0
    tx_bytes: int = 0
    rx_packets: int = 0
    tx_packets: int = 0
    ipv4: str | None = None
    ipv6: str | None = None
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiskSnapshot:
    name: str
    usage: int | None      # bytes; None when the server reports -1 (unknown)
    total: int = 0


@dataclass(frozen=True)
class InstanceSnapshot:
    project: str
    name: str
    type: str
    status: str
    status_code: int
    architecture: str | None = None
    location: str | None = None
    description: str | None = None
    image: str | None = None
    profiles: tuple[str, ...] = ()
    cpu_usage_ns: int | None = None
    cpu_allocated_ns: int | None = None
    cpu_percent: float | None = None            # % of ONE core since the previous poll (can exceed 100)
    memory_usage: int | None = None
    memory_peak: int | None = None
    memory_total: int | None = None
    swap_usage: int | None = None
    processes: int | None = None
    pid: int | None = None
    started_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime | None = None
    disks: dict[str, DiskSnapshot] = field(default_factory=dict)
    networks: dict[str, NicSnapshot] = field(default_factory=dict)
    ipv4: str | None = None
    ipv6: str | None = None
    limits: dict[str, str] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return instance_key(self.project, self.name)

    @property
    def is_running(self) -> bool:
        return self.status_code == RUNNING

    @property
    def is_frozen(self) -> bool:
        return self.status_code == FROZEN

    @property
    def display_name(self) -> str:
        return self.name if self.project == "default" else f"{self.project}/{self.name}"

    @property
    def memory_percent(self) -> float | None:
        if self.memory_usage is None or not self.memory_total:
            return None
        return round(self.memory_usage * 100 / self.memory_total, 2)


@dataclass(frozen=True)
class ServerSnapshot:
    name: str
    version: str | None
    api_version: str | None
    clustered: bool
    auth: str | None
    instances_total: int = 0
    instances_running: int = 0


@dataclass(frozen=True)
class InstancesData:
    server: ServerSnapshot
    instances: dict[str, InstanceSnapshot]                       # monitored instances only
    discovered: frozenset[str] = frozenset()                     # every instance key on the server (incl. ones the user excluded)


class CpuTracker:
    """Turns the cumulative CPU time counter into a usage percentage between two polls."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[int, float]] = {}

    def percent(self, key: str, cpu_ns: int | None, now: float, running: bool) -> float | None:
        if not running or cpu_ns is None:
            self._prev.pop(key, None)
            return None
        prev = self._prev.get(key)
        self._prev[key] = (cpu_ns, now)
        if prev is None or now <= prev[1] or cpu_ns < prev[0]:
            return None
        return round((cpu_ns - prev[0]) / ((now - prev[1]) * 1e9) * 100, 2)

    def forget_missing(self, keys: Iterable[str]) -> None:
        for k in set(self._prev) - set(keys):
            del self._prev[k]


def _valid_time(t: datetime | None) -> datetime | None:
    return t if t is not None and t.year > 1 else None   # Go's zero time is year 1


def parse_instance(inst: models.InstanceFull, default_project: str = "default", cpu_percent: float | None = None) -> InstanceSnapshot:
    st = inst.state
    nics: dict[str, NicSnapshot] = {}
    ipv4 = ipv6 = None
    if st is not None and st.network:
        for nic_name, n in st.network.items():
            addrs = n.addresses or []
            nic_v4 = next((a.address for a in addrs if a.family == "inet" and a.scope == "global"), None)
            nic_v6 = next((a.address for a in addrs if a.family == "inet6" and a.scope == "global"), None)
            c = n.counters
            nics[nic_name] = NicSnapshot(
                name=nic_name, hwaddr=n.hwaddr or None, state=n.state, type=n.type, mtu=n.mtu, host_name=n.host_name or None,
                rx_bytes=c.bytes_received if c else 0, tx_bytes=c.bytes_sent if c else 0,
                rx_packets=c.packets_received if c else 0, tx_packets=c.packets_sent if c else 0,
                ipv4=nic_v4, ipv6=nic_v6, addresses=tuple(f"{a.address}/{a.netmask}" for a in addrs))
            if nic_name != "lo" and n.type != "loopback":
                ipv4 = ipv4 or nic_v4
                ipv6 = ipv6 or nic_v6
    disks = {name: DiskSnapshot(name, d.usage if d.usage >= 0 else None, d.total)
             for name, d in ((st.disk or {}).items() if st else [])}
    cfg = inst.expanded_config or inst.config or {}
    mem = st.memory if st else None
    return InstanceSnapshot(
        project=inst.project or default_project, name=inst.name or "", type=inst.type or "container",
        status=inst.status or (st.status if st else "") or "Unknown", status_code=inst.status_code or (st.status_code if st else 0),
        architecture=inst.architecture, location=inst.location, description=inst.description or None,
        image=cfg.get("image.description"), profiles=tuple(inst.profiles or ()),
        cpu_usage_ns=st.cpu.usage if st and st.cpu else None, cpu_allocated_ns=st.cpu.allocated_time if st and st.cpu else None,
        cpu_percent=cpu_percent,
        memory_usage=mem.usage if mem else None, memory_peak=mem.usage_peak if mem else None,
        memory_total=mem.total if mem else None, swap_usage=mem.swap_usage if mem else None,
        processes=st.processes if st else None, pid=(st.pid or None) if st else None,
        started_at=_valid_time(st.started_at) if st else None, last_used_at=_valid_time(inst.last_used_at),
        created_at=_valid_time(inst.created_at), disks=disks, networks=nics, ipv4=ipv4, ipv6=ipv6,
        limits={k: v for k, v in cfg.items() if k.startswith("limits.")})


def build_instances_data(server: models.Server, instances: Iterable[models.InstanceFull], tracker: CpuTracker, now: float,
                         default_project: str = "default", excluded: frozenset[str] = frozenset()) -> InstancesData:
    snaps: dict[str, InstanceSnapshot] = {}
    for inst in instances:
        project = inst.project or default_project
        key = instance_key(project, inst.name or "")
        cpu_ns = inst.state.cpu.usage if inst.state and inst.state.cpu else None
        pct = tracker.percent(key, cpu_ns, now, inst.status_code == RUNNING)
        snaps[key] = parse_instance(inst, default_project, pct)
    tracker.forget_missing(snaps)
    env = server.environment
    return InstancesData(
        server=ServerSnapshot(
            name=(env.server_name if env and env.server_name else None) or "incus", version=env.server_version if env else None,
            api_version=server.api_version, clustered=bool(env and env.server_clustered), auth=server.auth,
            instances_total=len(snaps), instances_running=sum(1 for s in snaps.values() if s.is_running)),
        instances={k: v for k, v in snaps.items() if k not in excluded}, discovered=frozenset(snaps))


# ---- storage ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class PoolSnapshot:
    name: str
    driver: str | None
    status: str | None
    description: str | None
    space_used: int | None
    space_total: int | None
    inodes_used: int | None
    inodes_total: int | None
    volume_count: int = 0

    @property
    def space_free(self) -> int | None:
        return None if self.space_used is None or self.space_total is None else max(self.space_total - self.space_used, 0)

    @property
    def space_percent(self) -> float | None:
        if self.space_used is None or not self.space_total:
            return None
        return round(self.space_used * 100 / self.space_total, 2)


@dataclass(frozen=True)
class VolumeSnapshot:
    pool: str
    project: str
    name: str
    content_type: str | None
    description: str | None
    location: str | None
    created_at: datetime | None
    used: int | None            # bytes in use, None if the driver can't report it
    quota: int | None           # configured size / quota in bytes (usage.total), None if unlimited/unknown
    config: dict[str, str]
    used_by: tuple[str, ...]    # instance keys "project/name"

    @property
    def key(self) -> str:
        return f"{self.pool}/{self.project}/{self.name}"

    @property
    def percent(self) -> float | None:
        return None if self.used is None or not self.quota or self.quota <= 0 else round(self.used * 100 / self.quota, 2)

    @property
    def free(self) -> int | None:
        return None if self.used is None or not self.quota or self.quota <= 0 else max(self.quota - self.used, 0)


@dataclass(frozen=True)
class VolumesData:
    pools: dict[str, PoolSnapshot]                               # monitored pools only
    volumes: dict[str, VolumeSnapshot]                           # monitored custom volumes only
    discovered_pools: frozenset[str] = frozenset()               # everything that exists on the server
    discovered_volumes: frozenset[str] = frozenset()


def build_pool(pool: models.StoragePool, resources: models.ResourcesStoragePool | None, volume_count: int) -> PoolSnapshot:
    sp = resources.space if resources else None
    ino = resources.inodes if resources else None
    return PoolSnapshot(name=pool.name or "", driver=pool.driver, status=pool.status, description=pool.description or None,
                        space_used=sp.used if sp else None, space_total=sp.total if sp else None,
                        inodes_used=ino.used if ino else None, inodes_total=ino.total if ino else None, volume_count=volume_count)


def build_volume(pool: str, vol: models.StorageVolume, state: models.StorageVolumeState | None, default_project: str = "default") -> VolumeSnapshot:
    used = quota = None
    if state is not None and state.usage is not None:
        used = state.usage.used
        quota = state.usage.total if state.usage.total and state.usage.total > 0 else (state.total if state.total and state.total > 0 else None)
    cfg = dict(vol.config or {})
    if quota is None and cfg.get("size"):
        try:
            from .incus_client.shared.units import parse_byte_size_string
            quota = parse_byte_size_string(cfg["size"]) or None
        except ValueError:
            pass
    return VolumeSnapshot(pool=pool, project=vol.project or default_project, name=vol.name or "", content_type=vol.content_type,
                          description=vol.description or None, location=vol.location if vol.location not in ("", "none") else None,
                          created_at=_valid_time(vol.created_at), used=used, quota=quota, config=cfg,
                          used_by=tuple(parse_used_by(vol.used_by)))
