"""Which instances / storage pools / custom volumes exist on the server, and what changed. Home-Assistant-independent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

KINDS = ("instances", "pools", "volumes")
_TITLES = {"instances": "instances", "pools": "storage pools", "volumes": "custom volumes"}


def label(kind: str, key: str) -> str:
    """Human-readable label for an inventory key.

    instances "project/name" -> "name" (default project) or "project/name"; volumes "pool/project/name" -> "name (pool)"."""
    if kind == "instances":
        project, _, name = key.partition("/")
        return name if project == "default" else key
    if kind == "volumes":
        pool, _, rest = key.partition("/")
        project, _, name = rest.partition("/")
        return f"{name} ({pool})" if project == "default" else f"{project}/{name} ({pool})"
    return key


@dataclass(frozen=True)
class Inventory:
    instances: frozenset[str] = frozenset()
    pools: frozenset[str] = frozenset()
    volumes: frozenset[str] = frozenset()

    def to_dict(self) -> dict[str, list[str]]:
        return {k: sorted(getattr(self, k)) for k in KINDS}

    @classmethod
    def from_dict(cls, d: Mapping[str, Iterable[str]] | None) -> "Inventory | None":
        if not d:
            return None
        return cls(**{k: frozenset(d.get(k) or ()) for k in KINDS})

    @property
    def total(self) -> int:
        return len(self.instances) + len(self.pools) + len(self.volumes)


@dataclass(frozen=True)
class InventoryChange:
    added: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    removed: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed

    def to_event_data(self) -> dict[str, list[str]]:
        """Flat payload for the bus event, e.g. {"added_instances": [...], "removed_volumes": [...]}."""
        out: dict[str, list[str]] = {}
        for prefix, d in (("added", self.added), ("removed", self.removed)):
            for kind, keys in d.items():
                out[f"{prefix}_{kind}"] = list(keys)
        return out

    def describe(self) -> str:
        lines = []
        for verb, d in (("New", self.added), ("Removed", self.removed)):
            for kind in KINDS:
                if kind in d:
                    lines.append(f"{verb} {_TITLES[kind]}: " + ", ".join(label(kind, k) for k in d[kind]))
        return "\n".join(lines)


class InventoryTracker:
    """Compares each poll's inventory with the previous one (or with a baseline restored from storage).

    A kind that has never been seen has no baseline, so its first update is silent (no "everything is new" flood)."""

    def __init__(self, baseline: Inventory | None = None) -> None:
        self._current: dict[str, frozenset[str] | None] = {k: (getattr(baseline, k) if baseline else None) for k in KINDS}

    def update(self, **kinds: Iterable[str]) -> InventoryChange:
        added: dict[str, tuple[str, ...]] = {}
        removed: dict[str, tuple[str, ...]] = {}
        for kind, new_keys in kinds.items():
            if kind not in KINDS:
                raise ValueError(kind)
            new = frozenset(new_keys)
            old = self._current[kind]
            if old is not None:
                if new - old:
                    added[kind] = tuple(sorted(new - old))
                if old - new:
                    removed[kind] = tuple(sorted(old - new))
            self._current[kind] = new
        return InventoryChange(added, removed)

    @property
    def current(self) -> Inventory:
        return Inventory(**{k: (v or frozenset()) for k, v in self._current.items()})


def new_since(reviewed: Mapping[str, Any] | None, kind: str, keys: Iterable[str]) -> set[str]:
    """Keys not present in the inventory the user last reviewed (empty if they never reviewed: nothing is flagged)."""
    if reviewed is None:
        return set()
    known = set(reviewed.get(kind) or ())
    return {k for k in keys if k not in known}
