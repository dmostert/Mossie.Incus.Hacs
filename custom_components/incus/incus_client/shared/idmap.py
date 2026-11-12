"""Go: shared/idmap (subset: parsing, shifting and LXC output)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IdmapEntry:
    is_uid: bool = False
    is_gid: bool = False
    host_id: int = 0
    ns_id: int = 0
    map_range: int = 0

    def host_ids_intersect(self, o: "IdmapEntry") -> bool:
        return ((self.is_uid and o.is_uid) or (self.is_gid and o.is_gid)) and self.host_id < o.host_id + o.map_range and o.host_id < self.host_id + self.map_range

    def ns_ids_intersect(self, o: "IdmapEntry") -> bool:
        return ((self.is_uid and o.is_uid) or (self.is_gid and o.is_gid)) and self.ns_id < o.ns_id + o.map_range and o.ns_id < self.ns_id + self.map_range

    def to_lxc_lines(self) -> list[str]:
        lines = []
        if self.is_uid:
            lines.append(f"u {self.ns_id} {self.host_id} {self.map_range}")
        if self.is_gid:
            lines.append(f"g {self.ns_id} {self.host_id} {self.map_range}")
        return lines


def _range(s: str) -> tuple[int, int]:
    a, _, b = s.partition("-")
    return int(a), int(b or a)


@dataclass
class IdmapSet:
    entries: list[IdmapEntry] = field(default_factory=list)

    @classmethod
    def parse_raw(cls, raw: str) -> "IdmapSet":
        """Go: ParseRawIdmap. One entry per line: ``<uid|gid|both> <host>[-<end>] [<container>[-<end>]]``
        (grammar written from memory of raw.idmap; the container id defaults to the host id)."""
        s = cls()
        for line in (l.strip() for l in raw.split("\n")):
            if not line:
                continue
            f = line.split()
            if not 2 <= len(f) <= 3 or f[0] not in ("uid", "gid", "both"):
                raise ValueError(f"Invalid raw.idmap line: {line}")
            hs, he = _range(f[1])
            ns, ne = _range(f[2]) if len(f) == 3 else (hs, he)
            if he - hs != ne - ns:
                raise ValueError(f"Host and container ranges differ in size: {line}")
            s.entries.append(IdmapEntry(f[0] != "gid", f[0] != "uid", hs, ns, he - hs + 1))
        return s

    def _map(self, id_: int, is_uid: bool, into_ns: bool) -> int:
        for e in self.entries:
            if not (e.is_uid if is_uid else e.is_gid):
                continue
            src, dst = (e.host_id, e.ns_id) if into_ns else (e.ns_id, e.host_id)
            if src <= id_ < src + e.map_range:
                return dst + (id_ - src)
        return -1

    def shift_into_ns(self, uid: int, gid: int) -> tuple[int, int]:
        """Go: ShiftIntoNs. -1 for an id that isn't mapped."""
        return self._map(uid, True, True), self._map(gid, False, True)

    def shift_from_ns(self, uid: int, gid: int) -> tuple[int, int]:
        return self._map(uid, True, False), self._map(gid, False, False)

    def to_lxc_lines(self) -> list[str]:
        return [l for e in self.entries for l in e.to_lxc_lines()]
