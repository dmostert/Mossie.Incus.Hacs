"""Read-only simplestreams image server. Best effort: fingerprints use the ``combined_*_sha256`` fields when present
(falling back to the metadata sha256). Downloads are verified: every file's sha256 and, when both files are fetched, the
combined sha256 must match (cf. GO-2026-4882 / GO-2026-5806)."""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from typing import Any

from . import models
from .args import ImageFileRequest, ImageFileResponse, copy_with_progress
from .core_info import ConnectionInfo
from .errors import IncusError
from .shared import osarch
from .transport import ConnectionArgs, Transport

_COMBINED_KEY = {"squashfs": "combined_squashfs_sha256", "root.tar.xz": "combined_rootxz_sha256",
                 "disk-kvm.img": "combined_disk-kvm-img_sha256", "uefi1.img": "combined_uefi1-img_sha256",
                 "disk1.img": "combined_disk1-img_sha256"}
_CONTAINER_ROOTFS = ("squashfs", "root.tar.xz")
_VM_ROOTFS = ("disk-kvm.img", "uefi1.img", "disk1.img")


@dataclass
class _Entry:
    image: models.Image
    aliases: list[str]
    meta: dict
    rootfs: dict
    combined: bool


class _HashTee:
    """Write-only file-like that feeds every chunk to the given hashes and forwards to the destination."""

    def __init__(self, dst, *hashes):
        self._dst, self._hashes = dst, hashes

    def write(self, data: bytes) -> int:
        for h in self._hashes:
            h.update(data)
        return self._dst.write(data)


class ProtocolSimpleStreams:
    def __init__(self, transport: Transport, args: ConnectionArgs):
        self._t, self._args = transport, args
        self._cache: list[_Entry] | None = None

    # ---- Server interface -----------------------------------------------------------------
    def disconnect(self) -> None:
        pass

    def get_connection_info(self) -> ConnectionInfo:
        return ConnectionInfo(addresses=[f"{self._t.host}:{self._t.port}"], certificate=self._args.tls_server_cert,
                              protocol="simplestreams", url=self._t.base_url)

    def _fetch(self, path: str) -> bytes:
        status, reason, _, data = self._t.request("GET", self._t.path_prefix + "/" + path.lstrip("/"))
        if status != 200:
            raise IncusError(f"Failed to fetch {path}: {status} {reason}", status)
        return data

    def _load(self) -> list[_Entry]:
        if self._cache is not None:
            return self._cache
        index = json.loads(self._fetch("streams/v1/index.json"))
        path = next((e["path"] for e in index["index"].values() if e.get("datatype") == "image-downloads"), None)
        if path is None:
            raise IncusError("The simplestreams index has no image-downloads entry")
        products = json.loads(self._fetch(path)).get("products", {})
        out: list[_Entry] = []
        for p in products.values():
            versions = p.get("versions", {})
            latest = max(versions) if versions else None
            for serial, ver in versions.items():
                items = list(ver.get("items", {}).values())
                meta = next((i for i in items if i.get("ftype") in ("incus.tar.xz", "lxd.tar.xz")), None)
                root = (next((i for i in items if i.get("ftype") in _CONTAINER_ROOTFS), None)
                        or next((i for i in items if i.get("ftype") in _VM_ROOTFS), None))
                if meta is None or root is None:
                    continue
                is_vm = root["ftype"] in _VM_ROOTFS
                fp = meta.get(_COMBINED_KEY.get(root["ftype"], ""))
                combined = fp is not None
                fp = fp or meta.get("sha256")
                arch = p.get("arch", "")
                try:
                    arch_name = osarch.normalise(arch)
                except ValueError:
                    arch_name = arch
                aliases = [a.strip() for a in (p.get("aliases") or "").split(",") if a.strip()] if serial == latest else []
                img = models.Image(
                    fingerprint=fp, architecture=arch_name, type="virtual-machine" if is_vm else "container", public=True,
                    filename=os.path.basename(meta.get("path", "")), size=int(meta.get("size", 0)) + int(root.get("size", 0)),
                    properties={"os": p.get("os", ""), "release": p.get("release", ""), "variant": p.get("variant", ""),
                                "architecture": arch, "serial": serial,
                                "description": f"{p.get('os')} {p.get('release_title') or p.get('release')} ({arch}) ({serial})"},
                    aliases=[models.ImageAlias(name=a) for a in aliases])
                out.append(_Entry(img, aliases, meta, root, combined))
        self._cache = out
        return out

    # ---- ImageServer interface -------------------------------------------------------------
    def get_images(self) -> list[models.Image]:
        return [e.image for e in self._load()]

    def get_images_all_projects(self) -> list[models.Image]:
        return self.get_images()

    def get_image_fingerprints(self) -> list[str]:
        return list(dict.fromkeys(e.image.fingerprint for e in self._load()))

    def get_images_with_filter(self, filters: list[str]):
        raise NotImplementedError("Filtering is not supported by simplestreams servers")

    def get_image(self, fingerprint: str) -> tuple[models.Image, str]:
        entries = self._load()
        m = next((e for e in entries if e.image.fingerprint == fingerprint), None) or \
            next((e for e in entries if (e.image.fingerprint or "").startswith(fingerprint)), None)
        if m is None:
            raise IncusError("Image not found", 404)
        return m.image, ""

    def get_image_aliases(self) -> list[models.ImageAliasesEntry]:
        return [models.ImageAliasesEntry(name=a, target=e.image.fingerprint, type=e.image.type) for e in self._load() for a in e.aliases]

    def get_image_alias_names(self) -> list[str]:
        return list(dict.fromkeys(a.name for a in self.get_image_aliases()))

    def get_image_alias(self, name: str) -> tuple[models.ImageAliasesEntry, str]:
        return self.get_image_alias_type("", name)

    def get_image_alias_type(self, image_type: str, name: str) -> tuple[models.ImageAliasesEntry, str]:
        archs = self.get_image_alias_architectures(image_type, name)
        return archs.get(osarch.get_local()) or next(iter(archs.values())), ""

    def get_image_alias_architectures(self, image_type: str, name: str) -> dict[str, models.ImageAliasesEntry]:
        result = {e.image.architecture or "": models.ImageAliasesEntry(name=name, target=e.image.fingerprint, type=e.image.type)
                  for e in self._load() if name in e.aliases and (not image_type or e.image.type == image_type)}
        if not result:
            raise IncusError("Alias not found", 404)
        return result

    def get_image_file(self, fingerprint: str, req: ImageFileRequest) -> ImageFileResponse:
        e = next((x for x in self._load() if x.image.fingerprint == fingerprint), None)
        if e is None:
            raise IncusError("Image not found", 404)
        resp = ImageFileResponse()
        combined = hashlib.sha256()

        def fetch(item: dict, dst) -> int:
            expected = item.get("sha256")
            if not expected:
                raise IncusError(f"No sha256 published for {item.get('path')}; refusing to download unverified data")
            h = hashlib.sha256()
            with self._t.request_stream("GET", self._t.path_prefix + "/" + item["path"].lstrip("/")) as r:
                if r.status != 200:
                    raise IncusError(f"Failed to download {item['path']}: {r.status}", r.status)
                total = int(r.headers.get("Content-Length") or item.get("size") or 0) or None
                n = copy_with_progress(r, _HashTee(dst, h, combined), total, req.progress_handler)
            if h.hexdigest().lower() != expected.lower():
                raise IncusError(f"Hash mismatch for {item['path']}: expected {expected}, got {h.hexdigest()}")
            return n

        if req.meta_file is not None:
            resp.meta_name, resp.meta_size = os.path.basename(e.meta["path"]), fetch(e.meta, req.meta_file)
        if req.rootfs_file is not None:
            resp.rootfs_name, resp.rootfs_size = os.path.basename(e.rootfs["path"]), fetch(e.rootfs, req.rootfs_file)
        if req.meta_file is not None and req.rootfs_file is not None and e.combined:
            if combined.hexdigest() != e.image.fingerprint:
                raise IncusError(f"Combined hash mismatch: expected {e.image.fingerprint}, got {combined.hexdigest()}")
        return resp

    def get_image_secret(self, fingerprint: str) -> str:
        raise NotImplementedError("Not supported by simplestreams servers")

    def get_private_image(self, fingerprint: str, secret: str):
        raise NotImplementedError("Not supported by simplestreams servers")

    def get_private_image_file(self, fingerprint: str, secret: str, req: ImageFileRequest):
        raise NotImplementedError("Not supported by simplestreams servers")

    def export_image(self, fingerprint: str, image: models.ImageExportPost):
        raise NotImplementedError("Not supported by simplestreams servers")
