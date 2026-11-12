from __future__ import annotations

import io
import json
import threading
from typing import Any

from . import models
from ._core import Core, E, F, Q
from .args import *  # noqa: F401,F403
from .args import (BackupFileRequest, BackupFileResponse, InstanceBackupArgs, InstanceConsoleArgs, InstanceConsoleLogArgs,
                   InstanceCopyArgs, InstanceExecArgs, InstanceFileArgs, InstanceFileResponse, InstanceSnapshotCopyArgs,
                   copy_with_progress)
from .errors import IncusError
from .operations import Operation, RemoteOperation
from .transport import HTTPStream


def _ip(name: str) -> str:
    return f"/instances/{E(name)}"


def _mode(s: str) -> int:
    return 0 if not s else (int(s, 8) if len(s) > 1 and s[0] == "0" else int(s))


def _secrets(op: Operation) -> dict[str, str]:
    return {k: v for k, v in (op.get().metadata or {}).items() if isinstance(v, str)}


class InstancesMixin(Core):
    # ---- listing ---------------------------------------------------------------------------
    def get_instance_names(self, instance_type: str = "") -> list[str]:
        return self._names("/instances", Q(("instance-type", instance_type)))

    def get_instance_names_all_projects(self, instance_type: str = "") -> dict[str, list[str]]:
        return self._names_all_projects("/instances", Q(("instance-type", instance_type)))

    def get_instances(self, instance_type: str = "") -> list[models.Instance]:
        return self._get_list(models.Instance, "/instances", Q(("recursion", "1"), ("instance-type", instance_type)))

    def get_instances_all_projects(self, instance_type: str = "") -> list[models.Instance]:
        return self._get_list(models.Instance, "/instances", Q(("recursion", "1"), ("instance-type", instance_type)), True)

    def get_instances_with_filter(self, instance_type: str, filters: list[str]) -> list[models.Instance]:
        return self._get_list(models.Instance, "/instances", Q(("recursion", "1"), ("instance-type", instance_type), ("filter", F(filters))))

    def get_instances_all_projects_with_filter(self, instance_type: str, filters: list[str]) -> list[models.Instance]:
        return self._get_list(models.Instance, "/instances", Q(("recursion", "1"), ("instance-type", instance_type), ("filter", F(filters))), True)

    def get_instances_full(self, instance_type: str = "") -> list[models.InstanceFull]:
        return self._get_list(models.InstanceFull, "/instances", Q(("recursion", "2"), ("instance-type", instance_type)))

    def get_instances_full_all_projects(self, instance_type: str = "") -> list[models.InstanceFull]:
        return self._get_list(models.InstanceFull, "/instances", Q(("recursion", "2"), ("instance-type", instance_type)), True)

    def get_instances_full_with_filter(self, instance_type: str, filters: list[str]) -> list[models.InstanceFull]:
        return self._get_list(models.InstanceFull, "/instances", Q(("recursion", "2"), ("instance-type", instance_type), ("filter", F(filters))))

    def get_instances_full_all_projects_with_filter(self, instance_type: str, filters: list[str]) -> list[models.InstanceFull]:
        return self._get_list(models.InstanceFull, "/instances", Q(("recursion", "2"), ("instance-type", instance_type), ("filter", F(filters))), True)

    # ---- single instance -------------------------------------------------------------------
    def get_instance(self, name: str) -> tuple[models.Instance, str]:
        return self._get_e(models.Instance, _ip(name))

    def get_instance_full(self, name: str) -> tuple[models.InstanceFull, str]:
        return self._get_e(models.InstanceFull, _ip(name), Q(("recursion", "1")))

    def create_instance(self, instance: models.InstancesPost) -> Operation:
        return self._send_op("POST", "/instances", instance)

    def update_instance(self, name: str, instance: models.InstancePut, etag: str) -> Operation:
        return self._send_op("PUT", _ip(name), instance, etag)

    def update_instances(self, state: models.InstancesPut, etag: str) -> Operation:
        return self._send_op("PUT", "/instances", state, etag)

    def delete_instance(self, name: str) -> Operation:
        return self._send_op("DELETE", _ip(name))

    def rebuild_instance(self, instance_name: str, instance: models.InstanceRebuildPost) -> Operation:
        return self._send_op("POST", _ip(instance_name) + "/rebuild", instance)

    def rename_instance(self, name: str, instance: models.InstancePost) -> Operation:
        if instance.migration:
            raise IncusError("Can't ask for a migration through rename_instance")
        return self._send_op("POST", _ip(name), instance)

    def migrate_instance(self, name: str, instance: models.InstancePost) -> Operation:
        if not instance.migration:
            raise IncusError("Can't ask for a rename through migrate_instance")
        return self._send_op("POST", _ip(name), instance)

    def get_instance_state(self, name: str) -> tuple[models.InstanceState, str]:
        return self._get_e(models.InstanceState, _ip(name) + "/state")

    def update_instance_state(self, name: str, state: models.InstanceStatePut, etag: str) -> Operation:
        return self._send_op("PUT", _ip(name) + "/state", state, etag)

    # ---- snapshots -------------------------------------------------------------------------
    def get_instance_snapshot_names(self, instance_name: str) -> list[str]:
        return self._names(_ip(instance_name) + "/snapshots")

    def get_instance_snapshots(self, instance_name: str) -> list[models.InstanceSnapshot]:
        return self._get_list(models.InstanceSnapshot, _ip(instance_name) + "/snapshots", Q(("recursion", "1")))

    def get_instance_snapshot(self, instance_name: str, name: str) -> tuple[models.InstanceSnapshot, str]:
        return self._get_e(models.InstanceSnapshot, f"{_ip(instance_name)}/snapshots/{E(name)}")

    def create_instance_snapshot(self, instance_name: str, snapshot: models.InstanceSnapshotsPost) -> Operation:
        return self._send_op("POST", _ip(instance_name) + "/snapshots", snapshot)

    def rename_instance_snapshot(self, instance_name: str, name: str, instance: models.InstanceSnapshotPost) -> Operation:
        if instance.migration:
            raise IncusError("Can't ask for a migration through rename_instance_snapshot")
        return self._send_op("POST", f"{_ip(instance_name)}/snapshots/{E(name)}", instance)

    def migrate_instance_snapshot(self, instance_name: str, name: str, instance: models.InstanceSnapshotPost) -> Operation:
        if not instance.migration:
            raise IncusError("Can't ask for a rename through migrate_instance_snapshot")
        return self._send_op("POST", f"{_ip(instance_name)}/snapshots/{E(name)}", instance)

    def update_instance_snapshot(self, instance_name: str, name: str, instance: models.InstanceSnapshotPut, etag: str) -> Operation:
        return self._send_op("PUT", f"{_ip(instance_name)}/snapshots/{E(name)}", instance, etag)

    def delete_instance_snapshot(self, instance_name: str, name: str) -> Operation:
        return self._send_op("DELETE", f"{_ip(instance_name)}/snapshots/{E(name)}")

    # ---- backups ---------------------------------------------------------------------------
    def get_instance_backup_names(self, instance_name: str) -> list[str]:
        return self._names(_ip(instance_name) + "/backups")

    def get_instance_backups(self, instance_name: str) -> list[models.InstanceBackup]:
        return self._get_list(models.InstanceBackup, _ip(instance_name) + "/backups", Q(("recursion", "1")))

    def get_instance_backup(self, instance_name: str, name: str) -> tuple[models.InstanceBackup, str]:
        return self._get_e(models.InstanceBackup, f"{_ip(instance_name)}/backups/{E(name)}")

    def create_instance_backup(self, instance_name: str, backup: models.InstanceBackupsPost) -> Operation:
        return self._send_op("POST", _ip(instance_name) + "/backups", backup)

    def rename_instance_backup(self, instance_name: str, name: str, backup: models.InstanceBackupPost) -> Operation:
        return self._send_op("POST", f"{_ip(instance_name)}/backups/{E(name)}", backup)

    def delete_instance_backup(self, instance_name: str, name: str) -> Operation:
        return self._send_op("DELETE", f"{_ip(instance_name)}/backups/{E(name)}")

    def get_instance_backup_file(self, instance_name: str, name: str, req: BackupFileRequest) -> BackupFileResponse:
        return self._download_backup(f"{_ip(instance_name)}/backups/{E(name)}/export", req)

    def create_instance_from_backup(self, args: InstanceBackupArgs) -> Operation:
        h = {}
        if args.pool_name:
            h["X-Incus-pool"] = args.pool_name
        if args.name:
            h["X-Incus-name"] = args.name
        return self._send_op("POST", "/instances", args.backup_file, headers=h)

    def _download_backup(self, rel: str, req: BackupFileRequest) -> BackupFileResponse:
        with self._raw_get(self._u(rel)) as r:
            total = int(r.headers.get("Content-Length") or 0) or None
            return BackupFileResponse(size=copy_with_progress(r, req.backup_file, total, req.progress_handler))

    # ---- logs and console log --------------------------------------------------------------
    def get_instance_logfiles(self, name: str) -> list[str]:
        return self._names(_ip(name) + "/logs")

    def get_instance_logfile(self, name: str, filename: str) -> HTTPStream:
        return self._raw_stream(f"{_ip(name)}/logs/{E(filename)}")

    def delete_instance_logfile(self, name: str, filename: str) -> None:
        self._send("DELETE", f"{_ip(name)}/logs/{E(filename)}")

    def get_instance_console_log(self, instance_name: str, args: InstanceConsoleLogArgs | None = None) -> HTTPStream:
        return self._raw_stream(_ip(instance_name) + "/console", Q(("type", args.type if args else "")))

    def delete_instance_console_log(self, instance_name: str, args: InstanceConsoleLogArgs | None = None) -> None:
        self._send("DELETE", _ip(instance_name) + "/console", q=Q(("type", args.type if args else "")))

    # ---- metadata and templates ------------------------------------------------------------
    def get_instance_metadata(self, name: str) -> tuple[models.ImageMetadata, str]:
        return self._get_e(models.ImageMetadata, _ip(name) + "/metadata")

    def update_instance_metadata(self, name: str, metadata: models.ImageMetadata, etag: str) -> None:
        self._send("PUT", _ip(name) + "/metadata", metadata, etag)

    def get_instance_template_files(self, instance_name: str) -> list[str]:
        return self._get_list(str, _ip(instance_name) + "/metadata/templates")

    def get_instance_template_file(self, instance_name: str, template_name: str) -> HTTPStream:
        return self._raw_stream(_ip(instance_name) + "/metadata/templates", Q(("path", template_name)))

    def create_instance_template_file(self, instance_name: str, template_name: str, content: Any) -> None:
        self._send("POST", _ip(instance_name) + "/metadata/templates", content, q=Q(("path", template_name)))

    def delete_instance_template_file(self, name: str, template_name: str) -> None:
        self._send("DELETE", _ip(name) + "/metadata/templates", q=Q(("path", template_name)))

    # ---- files -----------------------------------------------------------------------------
    def get_instance_file(self, instance_name: str, file_path: str) -> tuple[Any, InstanceFileResponse]:
        """Returns (stream, info). The stream must be closed; for directories it is empty and ``info.entries`` is filled."""
        r = self._raw_get(self._u(_ip(instance_name) + "/files", Q(("path", file_path))))
        h = r.headers
        info = InstanceFileResponse(uid=int(h.get("X-Incus-uid", -1)), gid=int(h.get("X-Incus-gid", -1)),
                                    mode=_mode(h.get("X-Incus-mode", "")), type=h.get("X-Incus-type", ""))
        if info.type == "directory":
            with r:
                info.entries = (json.loads(r.read() or b"{}").get("metadata") or [])
            return io.BytesIO(b""), info
        return r, info

    def create_instance_file(self, instance_name: str, file_path: str, args: InstanceFileArgs) -> None:
        h = {"X-Incus-type": args.type or "file", "X-Incus-write": args.write_mode or "overwrite"}
        if args.mode >= 0:
            h["X-Incus-mode"] = f"{args.mode:04o}"
        if args.uid >= 0:
            h["X-Incus-uid"] = str(args.uid)
        if args.gid >= 0:
            h["X-Incus-gid"] = str(args.gid)
        body = None if args.type == "directory" else args.content
        self._send("POST", _ip(instance_name) + "/files", body, q=Q(("path", file_path)), headers=h)

    def delete_instance_file(self, instance_name: str, file_path: str) -> None:
        self._send("DELETE", _ip(instance_name) + "/files", q=Q(("path", file_path)))

    def get_instance_file_sftp_conn(self, instance_name: str):
        """Go: GetInstanceFileSFTPConn. Returns the raw upgraded socket speaking SFTP (use it with an SFTP library)."""
        self.check_extension("instance_sftp")
        return self._s.transport.upgrade(self._u(_ip(instance_name) + "/sftp"), "sftp")

    # ---- exec and console ------------------------------------------------------------------
    def exec_instance(self, instance_name: str, exec_: models.InstanceExecPost, args: InstanceExecArgs | None = None) -> Operation:
        """Go: ExecInstance. With ``args=None`` only the operation is created; otherwise the websockets are attached to the
        given streams and ``args.data_done`` is set when all data has been transferred."""
        if args is None:
            return self._send_op("POST", _ip(instance_name) + "/exec", exec_)
        exec_.wait_for_websocket = True
        op = self._send_op("POST", _ip(instance_name) + "/exec", exec_)
        fds = _fds(op)
        from ._core import pump
        threads = []
        if "control" in fds and args.control is not None:
            cws = op.get_websocket(fds["control"])
            threading.Thread(target=args.control, args=(cws,), daemon=True).start()
        if exec_.interactive:
            threads.append(pump(op.get_websocket(fds["0"]), args.stdin, args.stdout, close_on_eof=False))
        else:
            threads.append(pump(op.get_websocket(fds["0"]), args.stdin, None))
            threads.append(pump(op.get_websocket(fds["1"]), None, args.stdout))
            threads.append(pump(op.get_websocket(fds["2"]), None, args.stderr))

        def finish() -> None:
            for t in threads:
                t.join()
            args.data_done.set()
        threading.Thread(target=finish, daemon=True).start()
        return op

    def console_instance(self, instance_name: str, console: models.InstanceConsolePost, args: InstanceConsoleArgs) -> Operation:
        op, attach = self.console_instance_dynamic(instance_name, console, args)
        if args.terminal is not None:
            attach(args.terminal)
        return op

    def console_instance_dynamic(self, instance_name: str, console: models.InstanceConsolePost, args: InstanceConsoleArgs):
        """Go: ConsoleInstanceDynamic. Returns (operation, attach) - call ``attach(terminal)`` to start copying data
        (blocks until the console closes)."""
        from ._core import pump
        op = self._send_op("POST", _ip(instance_name) + "/console", console)
        fds = _fds(op)
        if "control" in fds and args.control is not None:
            cws = op.get_websocket(fds["control"])
            threading.Thread(target=args.control, args=(cws,), daemon=True).start()
        ws = op.get_websocket(fds["0"])

        def attach(terminal: Any) -> None:
            pump(ws, terminal, terminal, close_on_eof=False).join()
        return op, attach

    # ---- create from image / rebuild / copy ------------------------------------------------
    def create_instance_from_image(self, source, image: models.Image, req: models.InstancesPost) -> RemoteOperation:
        if req.source is None:
            req.source = models.InstanceSource()
        self._fill_image_source(source, image, req.source, req.type)
        return RemoteOperation(self.create_instance(req))

    def rebuild_instance_from_image(self, source, image: models.Image, instance_name: str,
                                    req: models.InstanceRebuildPost) -> RemoteOperation:
        if req.source is None:
            req.source = models.InstanceSource()
        self._fill_image_source(source, image, req.source, req.source.image_type)
        return RemoteOperation(self.rebuild_instance(instance_name, req))

    def _fill_image_source(self, source, image: models.Image, dst: models.InstanceSource, type_: str | None) -> None:
        info = source.get_connection_info()
        dst.type, dst.fingerprint = "image", image.fingerprint
        if hasattr(source, "_project") and self._same_server(info) and source._project == self._project:
            return   # local image
        dst.mode, dst.server, dst.protocol, dst.certificate, dst.image_type = "pull", info.url, info.protocol, info.certificate, type_
        if not image.public and info.protocol == "incus":
            dst.secret = source.get_image_secret(image.fingerprint)

    def copy_instance(self, source, instance: models.Instance, args: InstanceCopyArgs | None = None) -> RemoteOperation:
        """Go: CopyInstance. Same-server copies use "copy"; cross-server copies use a pull-mode migration."""
        args = args or InstanceCopyArgs()
        if args.mode != "pull":
            raise NotImplementedError("Only the 'pull' copy mode is implemented")
        req = models.InstancesPost(
            name=args.name or instance.name, type=instance.type, architecture=instance.architecture,
            config=dict(instance.config) if instance.config is not None else None, devices=instance.devices,
            ephemeral=instance.ephemeral, profiles=args.profiles or instance.profiles, description=instance.description,
            stateful=instance.stateful,
            source=models.InstanceSource(live=args.live, instance_only=args.instance_only, refresh=args.refresh,
                                         allow_inconsistent=args.allow_inconsistent))
        if instance.config and "volatile.base_image" in instance.config:
            req.source.base_image = instance.config["volatile.base_image"]
        info = source.get_connection_info()
        source_op = None
        if hasattr(source, "_project") and self._same_server(info) and source._project == self._project:
            req.source.type, req.source.source = "copy", instance.name
        else:
            source_op = source.migrate_instance(instance.name, models.InstancePost(
                migration=True, live=args.live, instance_only=args.instance_only, allow_inconsistent=args.allow_inconsistent))
            req.source.type, req.source.mode = "migration", "pull"
            req.source.operation = f"{info.url}/1.0/operations/{source_op.get().id}"
            req.source.secrets = _secrets(source_op)
            req.source.certificate = info.certificate
        return RemoteOperation(self.create_instance(req), source_op)

    def copy_instance_snapshot(self, source, instance_name: str, snapshot: models.InstanceSnapshot,
                               args: InstanceSnapshotCopyArgs | None = None) -> RemoteOperation:
        """Go: CopyInstanceSnapshot (pull-mode migration of a snapshot into a new instance)."""
        args = args or InstanceSnapshotCopyArgs()
        if args.mode != "pull":
            raise NotImplementedError("Only the 'pull' copy mode is implemented")
        short = (snapshot.name or "").rsplit("/", 1)[-1]
        req = models.InstancesPost(
            name=args.name or instance_name, architecture=snapshot.architecture, config=snapshot.config, devices=snapshot.devices,
            ephemeral=snapshot.ephemeral, profiles=snapshot.profiles, stateful=snapshot.stateful,
            source=models.InstanceSource(live=args.live))
        info = source.get_connection_info()
        source_op = source.migrate_instance_snapshot(instance_name, short, models.InstanceSnapshotPost(migration=True, live=args.live))
        req.source.type, req.source.mode = "migration", "pull"
        req.source.operation = f"{info.url}/1.0/operations/{source_op.get().id}"
        req.source.secrets = _secrets(source_op)
        req.source.certificate = info.certificate
        return RemoteOperation(self.create_instance(req), source_op)


def _fds(op: Operation) -> dict[str, str]:
    fds = (op.get().metadata or {}).get("fds")
    if not isinstance(fds, dict):
        raise IncusError("Server didn't return websocket file descriptors")
    return fds
