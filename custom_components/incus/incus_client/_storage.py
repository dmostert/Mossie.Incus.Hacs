from __future__ import annotations

from . import models
from ._core import Core, E, F, Q
from .args import (BackupFileRequest, BackupFileResponse, StoragePoolBucketBackupArgs, StoragePoolVolumeBackupArgs,
                   StoragePoolVolumeCopyArgs, StoragePoolVolumeMoveArgs, copy_with_progress)
from .errors import IncusError
from .operations import Operation, RemoteOperation


def _sp(pool: str) -> str:
    return f"/storage-pools/{E(pool)}"


def _vp(pool: str, vol_type: str, name: str) -> str:
    return f"{_sp(pool)}/volumes/{E(vol_type)}/{E(name)}"


def _bp(pool: str, bucket: str) -> str:
    return f"{_sp(pool)}/buckets/{E(bucket)}"


class StorageMixin(Core):
    # ---- pools -----------------------------------------------------------------------------
    def get_storage_pool_names(self) -> list[str]:
        return self._names("/storage-pools")

    def get_storage_pools(self) -> list[models.StoragePool]:
        return self._get_list(models.StoragePool, "/storage-pools", Q(("recursion", "1")))

    def get_storage_pool(self, name: str) -> tuple[models.StoragePool, str]:
        return self._get_e(models.StoragePool, _sp(name))

    def get_storage_pool_resources(self, name: str) -> models.ResourcesStoragePool:
        return self._get(models.ResourcesStoragePool, _sp(name) + "/resources")

    def create_storage_pool(self, pool: models.StoragePoolsPost) -> None:
        self._send("POST", "/storage-pools", pool)

    def update_storage_pool(self, name: str, pool: models.StoragePoolPut, etag: str) -> None:
        self._send("PUT", _sp(name), pool, etag)

    def delete_storage_pool(self, name: str) -> None:
        self._send("DELETE", _sp(name))

    # ---- volumes ---------------------------------------------------------------------------
    def get_storage_pool_volume_names(self, pool: str) -> list[str]:
        """Names are returned as "type/name" (as Go does)."""
        return self._names(_sp(pool) + "/volumes")

    def get_storage_pool_volume_names_all_projects(self, pool: str) -> dict[str, list[str]]:
        return self._names_all_projects(_sp(pool) + "/volumes")

    def get_storage_pool_volumes(self, pool: str) -> list[models.StorageVolume]:
        return self._get_list(models.StorageVolume, _sp(pool) + "/volumes", Q(("recursion", "1")))

    def get_storage_pool_volumes_all_projects(self, pool: str) -> list[models.StorageVolume]:
        return self._get_list(models.StorageVolume, _sp(pool) + "/volumes", Q(("recursion", "1")), True)

    def get_storage_pool_volumes_with_filter(self, pool: str, filters: list[str]) -> list[models.StorageVolume]:
        return self._get_list(models.StorageVolume, _sp(pool) + "/volumes", Q(("recursion", "1"), ("filter", F(filters))))

    def get_storage_pool_volumes_with_filter_all_projects(self, pool: str, filters: list[str]) -> list[models.StorageVolume]:
        return self._get_list(models.StorageVolume, _sp(pool) + "/volumes", Q(("recursion", "1"), ("filter", F(filters))), True)

    def get_storage_pool_volume(self, pool: str, vol_type: str, name: str) -> tuple[models.StorageVolume, str]:
        return self._get_e(models.StorageVolume, _vp(pool, vol_type, name))

    def get_storage_pool_volume_state(self, pool: str, vol_type: str, name: str) -> models.StorageVolumeState:
        return self._get(models.StorageVolumeState, _vp(pool, vol_type, name) + "/state")

    def create_storage_pool_volume(self, pool: str, volume: models.StorageVolumesPost) -> None:
        self._send("POST", f"{_sp(pool)}/volumes/{E(volume.type or 'custom')}", volume)

    def update_storage_pool_volume(self, pool: str, vol_type: str, name: str, volume: models.StorageVolumePut, etag: str) -> None:
        self._send("PUT", _vp(pool, vol_type, name), volume, etag)

    def rename_storage_pool_volume(self, pool: str, vol_type: str, name: str, volume: models.StorageVolumePost) -> None:
        self._send("POST", _vp(pool, vol_type, name), volume)

    def delete_storage_pool_volume(self, pool: str, vol_type: str, name: str) -> None:
        self._send("DELETE", _vp(pool, vol_type, name))

    def migrate_storage_pool_volume(self, pool: str, volume: models.StorageVolumePost) -> Operation:
        if not volume.migration:
            raise IncusError("Can't ask for a rename through migrate_storage_pool_volume")
        if not volume.name:
            raise IncusError("volume.name (the source volume) is required")
        return self._send_op("POST", _vp(pool, "custom", volume.name), volume)

    def create_storage_pool_volume_from_backup(self, pool: str, args: StoragePoolVolumeBackupArgs) -> Operation:
        return self._send_op("POST", _sp(pool) + "/volumes/custom", args.backup_file,
                             headers={"X-Incus-name": args.name} if args.name else None)

    def create_storage_pool_volume_from_iso(self, pool: str, args: StoragePoolVolumeBackupArgs) -> Operation:
        h = {"X-Incus-type": "iso"}
        if args.name:
            h["X-Incus-name"] = args.name
        return self._send_op("POST", _sp(pool) + "/volumes/custom", args.backup_file, headers=h)

    # ---- volume snapshots ------------------------------------------------------------------
    def get_storage_pool_volume_snapshot_names(self, pool: str, volume_type: str, volume_name: str) -> list[str]:
        return self._names(_vp(pool, volume_type, volume_name) + "/snapshots")

    def get_storage_pool_volume_snapshots(self, pool: str, volume_type: str, volume_name: str) -> list[models.StorageVolumeSnapshot]:
        return self._get_list(models.StorageVolumeSnapshot, _vp(pool, volume_type, volume_name) + "/snapshots", Q(("recursion", "1")))

    def get_storage_pool_volume_snapshot(self, pool: str, volume_type: str, volume_name: str, snapshot_name: str) -> tuple[models.StorageVolumeSnapshot, str]:
        return self._get_e(models.StorageVolumeSnapshot, f"{_vp(pool, volume_type, volume_name)}/snapshots/{E(snapshot_name)}")

    def create_storage_pool_volume_snapshot(self, pool: str, volume_type: str, volume_name: str, snapshot: models.StorageVolumeSnapshotsPost) -> Operation:
        return self._send_op("POST", _vp(pool, volume_type, volume_name) + "/snapshots", snapshot)

    def rename_storage_pool_volume_snapshot(self, pool: str, volume_type: str, volume_name: str, snapshot_name: str, snapshot: models.StorageVolumeSnapshotPost) -> Operation:
        return self._send_op("POST", f"{_vp(pool, volume_type, volume_name)}/snapshots/{E(snapshot_name)}", snapshot)

    def update_storage_pool_volume_snapshot(self, pool: str, volume_type: str, volume_name: str, snapshot_name: str, volume: models.StorageVolumeSnapshotPut, etag: str) -> None:
        self._send("PUT", f"{_vp(pool, volume_type, volume_name)}/snapshots/{E(snapshot_name)}", volume, etag)

    def delete_storage_pool_volume_snapshot(self, pool: str, volume_type: str, volume_name: str, snapshot_name: str) -> Operation:
        return self._send_op("DELETE", f"{_vp(pool, volume_type, volume_name)}/snapshots/{E(snapshot_name)}")

    # ---- volume backups --------------------------------------------------------------------
    def get_storage_pool_volume_backup_names(self, pool: str, vol_name: str) -> list[str]:
        return self._names(_vp(pool, "custom", vol_name) + "/backups")

    def get_storage_pool_volume_backups(self, pool: str, vol_name: str) -> list[models.StoragePoolVolumeBackup]:
        return self._get_list(models.StoragePoolVolumeBackup, _vp(pool, "custom", vol_name) + "/backups", Q(("recursion", "1")))

    def get_storage_pool_volume_backup(self, pool: str, vol_name: str, name: str) -> tuple[models.StoragePoolVolumeBackup, str]:
        return self._get_e(models.StoragePoolVolumeBackup, f"{_vp(pool, 'custom', vol_name)}/backups/{E(name)}")

    def create_storage_pool_volume_backup(self, pool: str, vol_name: str, backup: models.StoragePoolVolumeBackupsPost) -> Operation:
        return self._send_op("POST", _vp(pool, "custom", vol_name) + "/backups", backup)

    def rename_storage_pool_volume_backup(self, pool: str, vol_name: str, name: str, backup: models.StoragePoolVolumeBackupPost) -> Operation:
        return self._send_op("POST", f"{_vp(pool, 'custom', vol_name)}/backups/{E(name)}", backup)

    def delete_storage_pool_volume_backup(self, pool: str, vol_name: str, name: str) -> Operation:
        return self._send_op("DELETE", f"{_vp(pool, 'custom', vol_name)}/backups/{E(name)}")

    def get_storage_pool_volume_backup_file(self, pool: str, vol_name: str, name: str, req: BackupFileRequest) -> BackupFileResponse:
        return self._download_backup(f"{_vp(pool, 'custom', vol_name)}/backups/{E(name)}/export", req)

    # ---- buckets ---------------------------------------------------------------------------
    def get_storage_pool_bucket_names(self, pool_name: str) -> list[str]:
        return self._names(_sp(pool_name) + "/buckets")

    def get_storage_pool_buckets(self, pool_name: str) -> list[models.StorageBucket]:
        return self._get_list(models.StorageBucket, _sp(pool_name) + "/buckets", Q(("recursion", "1")))

    def get_storage_pool_bucket(self, pool_name: str, bucket_name: str) -> tuple[models.StorageBucket, str]:
        return self._get_e(models.StorageBucket, _bp(pool_name, bucket_name))

    def create_storage_pool_bucket(self, pool_name: str, bucket: models.StorageBucketsPost) -> models.StorageBucketKey | None:
        return self._send("POST", _sp(pool_name) + "/buckets", bucket).get_metadata(models.StorageBucketKey)

    def update_storage_pool_bucket(self, pool_name: str, bucket_name: str, bucket: models.StorageBucketPut, etag: str) -> None:
        self._send("PUT", _bp(pool_name, bucket_name), bucket, etag)

    def delete_storage_pool_bucket(self, pool_name: str, bucket_name: str) -> None:
        self._send("DELETE", _bp(pool_name, bucket_name))

    def get_storage_pool_bucket_key_names(self, pool_name: str, bucket_name: str) -> list[str]:
        return self._names(_bp(pool_name, bucket_name) + "/keys")

    def get_storage_pool_bucket_keys(self, pool_name: str, bucket_name: str) -> list[models.StorageBucketKey]:
        return self._get_list(models.StorageBucketKey, _bp(pool_name, bucket_name) + "/keys", Q(("recursion", "1")))

    def get_storage_pool_bucket_key(self, pool_name: str, bucket_name: str, key_name: str) -> tuple[models.StorageBucketKey, str]:
        return self._get_e(models.StorageBucketKey, f"{_bp(pool_name, bucket_name)}/keys/{E(key_name)}")

    def create_storage_pool_bucket_key(self, pool_name: str, bucket_name: str, key: models.StorageBucketKeysPost) -> models.StorageBucketKey | None:
        return self._send("POST", _bp(pool_name, bucket_name) + "/keys", key).get_metadata(models.StorageBucketKey)

    def update_storage_pool_bucket_key(self, pool_name: str, bucket_name: str, key_name: str, key: models.StorageBucketKeyPut, etag: str) -> None:
        self._send("PUT", f"{_bp(pool_name, bucket_name)}/keys/{E(key_name)}", key, etag)

    def delete_storage_pool_bucket_key(self, pool_name: str, bucket_name: str, key_name: str) -> None:
        self._send("DELETE", f"{_bp(pool_name, bucket_name)}/keys/{E(key_name)}")

    def create_storage_pool_bucket_backup(self, pool_name: str, bucket_name: str, backup: models.StorageBucketBackupsPost) -> Operation:
        return self._send_op("POST", _bp(pool_name, bucket_name) + "/backups", backup)

    def delete_storage_pool_bucket_backup(self, pool: str, bucket_name: str, name: str) -> Operation:
        return self._send_op("DELETE", f"{_bp(pool, bucket_name)}/backups/{E(name)}")

    def get_storage_pool_bucket_backup_file(self, pool: str, bucket_name: str, name: str, req: BackupFileRequest) -> BackupFileResponse:
        return self._download_backup(f"{_bp(pool, bucket_name)}/backups/{E(name)}/export", req)

    def create_storage_pool_bucket_from_backup(self, pool: str, args: StoragePoolBucketBackupArgs) -> Operation:
        return self._send_op("POST", _sp(pool) + "/buckets", args.backup_file, headers={"X-Incus-name": args.name} if args.name else None)

    # ---- cross-server volume copy/move -----------------------------------------------------
    def copy_storage_pool_volume(self, pool: str, source, source_pool: str, volume: models.StorageVolume,
                                 args: StoragePoolVolumeCopyArgs | None = None) -> RemoteOperation:
        """Go: CopyStoragePoolVolume. Same-server copies use "copy"; cross-server copies use a pull-mode migration."""
        args = args or StoragePoolVolumeCopyArgs()
        if args.mode != "pull":
            raise NotImplementedError("Only the 'pull' copy mode is implemented")
        req = models.StorageVolumesPost(
            name=args.name or volume.name, type=volume.type, content_type=volume.content_type, config=volume.config,
            description=volume.description,
            source=models.StorageVolumeSource(pool=source_pool, volume_only=args.volume_only, refresh=args.refresh))
        info = source.get_connection_info()
        source_op = None
        if hasattr(source, "_project") and self._same_server(info) and source._project == self._project:
            req.source.type, req.source.name = "copy", volume.name
        else:
            source_op = source.migrate_storage_pool_volume(source_pool, models.StorageVolumePost(
                name=volume.name, migration=True, volume_only=args.volume_only))
            req.source.type, req.source.mode = "migration", "pull"
            req.source.operation = f"{info.url}/1.0/operations/{source_op.get().id}"
            req.source.secrets = {k: v for k, v in (source_op.get().metadata or {}).items() if isinstance(v, str)}
            req.source.certificate = info.certificate
        target = self._send_op("POST", f"{_sp(pool)}/volumes/{E(volume.type or 'custom')}", req)
        return RemoteOperation(target, source_op)

    def move_storage_pool_volume(self, pool: str, source, source_pool: str, volume: models.StorageVolume,
                                 args: StoragePoolVolumeMoveArgs | None = None) -> RemoteOperation:
        """Go: MoveStoragePoolVolume. Within one server this migrates in place; across servers it copies then deletes the source."""
        args = args or StoragePoolVolumeMoveArgs()
        info = source.get_connection_info()
        if hasattr(source, "_project") and self._same_server(info) and source._project == self._project:
            post = models.StorageVolumePost(name=args.name or volume.name, pool=pool, volume_only=args.volume_only)
            op = self._send_op("POST", _vp(source_pool, volume.type or "custom", volume.name), post)
            return RemoteOperation(op)
        rop = self.copy_storage_pool_volume(pool, source, source_pool, volume, args)
        rop.wait()
        source.delete_storage_pool_volume(source_pool, volume.type or "custom", volume.name)
        return rop
