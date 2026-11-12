from __future__ import annotations

from ._cluster import ClusterMixin
from ._images import ImagesMixin
from ._instances import InstancesMixin
from ._network import NetworkMixin
from ._storage import StorageMixin


class ProtocolIncus(InstancesMixin, ImagesMixin, NetworkMixin, StorageMixin, ClusterMixin):
    """Go: ProtocolIncus (InstanceServer + ImageServer). Create one with the ``connect_*`` functions.

    Go's ``(value, ETag, err)`` returns become ``(value, etag)`` tuples and errors raise :class:`IncusError`.
    ``use_project()`` / ``use_target()`` return scoped views that share the same connection settings.
    """
