from __future__ import annotations

import os
import uuid
from urllib.parse import quote

from . import models
from ._core import Core, E, F, Q
from .args import ImageCopyArgs, ImageCreateArgs, ImageFileRequest, ImageFileResponse, ProgressData, copy_with_progress
from .errors import IncusError
from .operations import Operation, RemoteOperation


class _ProgressReader:
    """Wraps a binary file so reads report upload progress."""

    def __init__(self, f, cb):
        self._f, self._cb, self._done = f, cb, 0
        try:
            self._total = os.fstat(f.fileno()).st_size
        except (AttributeError, OSError):
            self._total = 0

    def read(self, n=-1):
        data = self._f.read(n)
        self._done += len(data)
        self._cb(ProgressData(int(self._done * 100 / self._total) if self._total else 0, self._done, 0, self._total))
        return data


class ImagesMixin(Core):
    def get_images(self) -> list[models.Image]:
        return self._get_list(models.Image, "/images", Q(("recursion", "1")))

    def get_images_all_projects(self) -> list[models.Image]:
        return self._get_list(models.Image, "/images", Q(("recursion", "1")), True)

    def get_images_with_filter(self, filters: list[str]) -> list[models.Image]:
        return self._get_list(models.Image, "/images", Q(("recursion", "1"), ("filter", F(filters))))

    def get_image_fingerprints(self) -> list[str]:
        return self._names("/images")

    def get_image(self, fingerprint: str) -> tuple[models.Image, str]:
        return self._get_e(models.Image, f"/images/{E(fingerprint)}")

    def get_private_image(self, fingerprint: str, secret: str) -> tuple[models.Image, str]:
        return self._get_e(models.Image, f"/images/{E(fingerprint)}", Q(("secret", secret)))

    def update_image(self, fingerprint: str, image: models.ImagePut, etag: str) -> None:
        self._send("PUT", f"/images/{E(fingerprint)}", image, etag)

    def delete_image(self, fingerprint: str) -> Operation:
        return self._send_op("DELETE", f"/images/{E(fingerprint)}")

    def refresh_image(self, fingerprint: str) -> Operation:
        return self._send_op("POST", f"/images/{E(fingerprint)}/refresh")

    def export_image(self, fingerprint: str, image: models.ImageExportPost) -> Operation:
        return self._send_op("POST", f"/images/{E(fingerprint)}/export", image)

    def create_image_secret(self, fingerprint: str) -> Operation:
        return self._send_op("POST", f"/images/{E(fingerprint)}/secret")

    def get_image_secret(self, fingerprint: str) -> str:
        op = self.create_image_secret(fingerprint)
        secret = (op.get().metadata or {}).get("secret")
        if not secret:
            raise IncusError("Server didn't return an image secret")
        return secret

    # ---- aliases ---------------------------------------------------------------------------
    def get_image_aliases(self) -> list[models.ImageAliasesEntry]:
        return self._get_list(models.ImageAliasesEntry, "/images/aliases", Q(("recursion", "1")))

    def get_image_alias_names(self) -> list[str]:
        return self._names("/images/aliases")

    def get_image_alias(self, name: str) -> tuple[models.ImageAliasesEntry, str]:
        return self._get_e(models.ImageAliasesEntry, f"/images/aliases/{E(name)}")

    def get_image_alias_type(self, image_type: str, name: str) -> tuple[models.ImageAliasesEntry, str]:
        return self._get_e(models.ImageAliasesEntry, f"/images/aliases/{E(name)}", Q(("type", image_type)))

    def get_image_alias_architectures(self, image_type: str, name: str) -> dict[str, models.ImageAliasesEntry]:
        alias, _ = self.get_image_alias_type(image_type, name)
        img, _ = self.get_image(alias.target)
        return {img.architecture or "": alias}

    def create_image_alias(self, alias: models.ImageAliasesPost) -> None:
        self._send("POST", "/images/aliases", alias)

    def update_image_alias(self, name: str, alias: models.ImageAliasesEntryPut, etag: str) -> None:
        self._send("PUT", f"/images/aliases/{E(name)}", alias, etag)

    def rename_image_alias(self, name: str, alias: models.ImageAliasesEntryPost) -> None:
        self._send("POST", f"/images/aliases/{E(name)}", alias)

    def delete_image_alias(self, name: str) -> None:
        self._send("DELETE", f"/images/aliases/{E(name)}")

    # ---- file transfer ---------------------------------------------------------------------
    def get_image_file(self, fingerprint: str, req: ImageFileRequest) -> ImageFileResponse:
        return self._download_image(fingerprint, "", req)

    def get_private_image_file(self, fingerprint: str, secret: str, req: ImageFileRequest) -> ImageFileResponse:
        return self._download_image(fingerprint, secret, req)

    def _download_image(self, fingerprint: str, secret: str, req: ImageFileRequest) -> ImageFileResponse:
        with self._raw_get(self._u(f"/images/{E(fingerprint)}/export", Q(("secret", secret)))) as r:
            if r.headers.get_content_type().startswith("multipart/"):
                raise NotImplementedError("Split (metadata + rootfs) image downloads are not supported; only unified tarballs are.")
            if req.meta_file is None:
                raise ValueError("ImageFileRequest.meta_file is required")
            disp = r.headers.get_filename() or ""
            total = int(r.headers.get("Content-Length") or 0) or None
            size = copy_with_progress(r, req.meta_file, total, req.progress_handler)
            return ImageFileResponse(meta_name=disp, meta_size=size)

    def create_image(self, image: models.ImagesPost, args: ImageCreateArgs | None = None) -> Operation:
        """Go: CreateImage. Direct upload (meta_file [+ rootfs_file]) or, with ``image.source`` set and no files, a server-side import."""
        if args is None or args.meta_file is None:
            if image.source is None:
                raise IncusError("Either image.source or args.meta_file must be provided")
            return self._send_op("POST", "/images", image)
        h: dict[str, str] = {}
        if image.public:
            h["X-Incus-public"] = "true"
        if image.filename:
            h["X-Incus-filename"] = image.filename
        if image.properties:
            h["X-Incus-properties"] = "&".join(f"{quote(k)}={quote(v)}" for k, v in image.properties.items())
        if image.profiles:
            h["X-Incus-profiles"] = ",".join(quote(p) for p in image.profiles)
        if args.type:
            h["X-Incus-type"] = args.type
        wrap = (lambda f: _ProgressReader(f, args.progress_handler)) if args.progress_handler else (lambda f: f)
        if args.rootfs_file is None:
            return self._send_op("POST", "/images", wrap(args.meta_file), headers=h)
        boundary = uuid.uuid4().hex
        body = _Multipart(boundary, [("metadata", args.meta_name, wrap(args.meta_file)), ("rootfs", args.rootfs_name, wrap(args.rootfs_file))])
        h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        return self._send_op("POST", "/images", body, headers=h)

    def copy_image(self, source, image: models.Image, args: ImageCopyArgs | None = None) -> RemoteOperation:
        """Go: CopyImage (pull mode: the target server downloads the image from ``source``)."""
        args = args or ImageCopyArgs()
        if args.mode != "pull":
            raise NotImplementedError("Only the 'pull' copy mode is implemented")
        info = source.get_connection_info()
        aliases = list(args.aliases or []) + (list(image.aliases or []) if args.copy_aliases else [])
        req = models.ImagesPost(
            public=args.public, auto_update=args.auto_update, aliases=aliases, profiles=args.profiles,
            source=models.ImagesPostSource(type="image", mode="pull", server=info.url, protocol=info.protocol,
                                           fingerprint=image.fingerprint, certificate=info.certificate,
                                           image_type=args.type or image.type))
        if not image.public and info.protocol == "incus":
            req.source.secret = source.get_image_secret(image.fingerprint)
        return RemoteOperation(self._send_op("POST", "/images", req))


class _Multipart:
    """Streaming multipart/form-data body (file-like) so large images aren't loaded into memory."""

    def __init__(self, boundary: str, parts: list):
        self._chunks = []
        for name, filename, f in parts:
            head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n").encode()
            self._chunks += [("b", head), ("f", f), ("b", b"\r\n")]
        self._chunks.append(("b", f"--{boundary}--\r\n".encode()))
        self._cur = None

    def read(self, n: int = -1) -> bytes:
        n = 65536 if n is None or n < 0 else n
        while self._chunks or self._cur is not None:
            if self._cur is None:
                kind, val = self._chunks.pop(0)
                self._cur = io_bytes(val) if kind == "b" else val
            data = self._cur.read(n)
            if data:
                return data
            self._cur = None
        return b""


def io_bytes(b: bytes):
    import io
    return io.BytesIO(b)
