"""Argument/response helper types (Go: BackupFileRequest, ImageCreateArgs, InstanceExecArgs, ...)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable

from . import models


@dataclass
class ProgressData:
    """Go: ioprogress.ProgressData."""
    percentage: int = 0
    processed: int = 0
    speed: int = 0
    total: int = 0

    @property
    def text(self) -> str:
        return (f"{self.percentage}% ({self.processed}/{self.total} bytes, {self.speed}/s)" if self.total
                else f"{self.processed} bytes ({self.speed}/s)")


ProgressHandler = Callable[[ProgressData], Any]


def copy_with_progress(src: Any, dst: BinaryIO, total: int | None = None, progress: ProgressHandler | None = None,
                       chunk: int = 65536) -> int:
    """Copies src (anything with read()) to dst, reporting progress. Returns the number of bytes copied."""
    done, started = 0, time.monotonic()
    while data := src.read(chunk):
        dst.write(data)
        done += len(data)
        if progress:
            secs = max(time.monotonic() - started, 0.001)
            progress(ProgressData(int(done * 100 / total) if total else 0, done, int(done / secs), total or 0))
    return done


@dataclass
class BackupFileRequest:
    backup_file: BinaryIO
    progress_handler: ProgressHandler | None = None


@dataclass
class BackupFileResponse:
    size: int = 0


@dataclass
class ImageCreateArgs:
    meta_file: BinaryIO | None = None
    meta_name: str = ""
    rootfs_file: BinaryIO | None = None
    rootfs_name: str = ""
    progress_handler: ProgressHandler | None = None
    type: str = ""   # "container" or "virtual-machine"


@dataclass
class ImageFileRequest:
    meta_file: BinaryIO | None = None
    rootfs_file: BinaryIO | None = None
    progress_handler: ProgressHandler | None = None


@dataclass
class ImageFileResponse:
    meta_name: str = ""
    meta_size: int = 0
    rootfs_name: str = ""
    rootfs_size: int = 0


@dataclass
class ImageCopyArgs:
    aliases: list[models.ImageAlias] | None = None
    auto_update: bool = False
    copy_aliases: bool = False
    public: bool = False
    type: str = ""
    mode: str = "pull"          # only "pull" is implemented
    profiles: list[str] | None = None


@dataclass
class InstanceBackupArgs:
    backup_file: BinaryIO
    pool_name: str = ""
    name: str = ""


@dataclass
class InstanceConsoleArgs:
    terminal: Any = None        # object with read()/write(), e.g. a pty wrapper or a socket makefile
    control: Callable[[Any], Any] | None = None
    console_disconnect: threading.Event | None = None


@dataclass
class InstanceConsoleLogArgs:
    type: str = ""


@dataclass
class InstanceCopyArgs:
    name: str = ""
    live: bool = False
    instance_only: bool = False
    refresh: bool = False
    allow_inconsistent: bool = False
    mode: str = "pull"
    profiles: list[str] | None = None


@dataclass
class InstanceSnapshotCopyArgs:
    name: str = ""
    mode: str = "pull"
    live: bool = False


@dataclass
class InstanceExecArgs:
    stdin: BinaryIO | None = None
    stdout: BinaryIO | None = None
    stderr: BinaryIO | None = None
    control: Callable[[Any], Any] | None = None   # called with the control websocket in a background thread
    data_done: threading.Event = field(default_factory=threading.Event)   # set once all data streams finished (Go: DataDone)


@dataclass
class InstanceFileArgs:
    content: Any = None         # bytes or binary file-like object
    uid: int = -1
    gid: int = -1
    mode: int = -1              # e.g. 0o644; -1 = server default
    type: str = "file"          # "file", "symlink" or "directory"
    write_mode: str = "overwrite"   # or "append"


@dataclass
class InstanceFileResponse:
    uid: int = 0
    gid: int = 0
    mode: int = 0
    type: str = ""
    entries: list[str] | None = None


@dataclass
class StoragePoolBucketBackupArgs:
    backup_file: BinaryIO
    name: str = ""


@dataclass
class StoragePoolVolumeBackupArgs:
    backup_file: BinaryIO
    name: str = ""


@dataclass
class StoragePoolVolumeCopyArgs:
    name: str = ""
    mode: str = "pull"
    volume_only: bool = False
    refresh: bool = False


class StoragePoolVolumeMoveArgs(StoragePoolVolumeCopyArgs):
    pass
