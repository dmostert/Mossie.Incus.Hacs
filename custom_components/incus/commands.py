"""Instance power commands. Blocking; run in an executor. Home-Assistant-independent."""
from __future__ import annotations

from .incus_client import IncusError, models

ACTIONS = ("start", "stop", "restart", "freeze", "unfreeze")


def run_instance_action(client, project: str, name: str, action: str, *, force: bool = False, timeout: int = 30,
                        wait: bool = True) -> None:
    """Start/stop/restart/freeze/unfreeze one instance and (optionally) wait for the operation to finish.

    ``timeout`` is the server-side graceful-shutdown timeout in seconds (stop/restart); ``force`` kills instead of shutting down.
    Raises IncusError with the server's message on failure (e.g. "The instance is already running")."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action!r}")
    put = models.InstanceStatePut(action=action, timeout=timeout, force=force, stateful=False)
    op = client.use_project(project).update_instance_state(name, put, "")
    if wait:
        op.wait(timeout=max(timeout, 0) + 30)
