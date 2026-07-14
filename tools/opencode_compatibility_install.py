# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified OpenCode release download and installation
"""Download and extract one immutable OpenCode release artifact safely."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import struct
import tarfile
import zipfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.client import HTTPMessage
from pathlib import Path
from typing import IO, BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from tools.opencode_compatibility_contract import Artifact, Compatibility

_MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024
_MAX_TAR_STREAM_BYTES = _MAX_BINARY_BYTES + 4 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 16
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_DOWNLOAD_HOSTS = frozenset({"github.com", "objects.githubusercontent.com"})
_ZIP_EOCD = b"PK\x05\x06"
_ZIP_EOCD_BYTES = 22
_ZIP_MAX_COMMENT_BYTES = 65_535


class SmokeError(RuntimeError):
    """The pinned artifact could not be installed or its ACP face failed."""


class _PinnedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        _require_download_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _BoundedReader(io.RawIOBase):
    """Expose a read-only stream with a hard expanded-byte ceiling."""

    def __init__(self, source: IO[bytes], limit: int) -> None:
        super().__init__()
        self._source = source
        self._limit = limit
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._limit - self._read
        requested = remaining + 1 if size < 0 or size > remaining else size
        data = self._source.read(requested)
        self._read += len(data)
        if self._read > self._limit:
            raise SmokeError("OpenCode tarball exceeds the expanded-stream limit")
        return data

    def readable(self) -> bool:
        return True


def _require_download_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or (
        host not in _ALLOWED_DOWNLOAD_HOSTS and not host.endswith(".githubusercontent.com")
    ):
        raise SmokeError(f"OpenCode download redirected outside approved HTTPS hosts: {url}")


def artifact_url(contract: Compatibility, artifact: Artifact) -> str:
    """Return the immutable official release URL for one manifest artifact."""
    if contract.repository != "anomalyco/opencode":
        raise SmokeError("OpenCode artifact URL requires the official repository")
    url = (
        f"https://github.com/{contract.repository}/releases/download/{contract.tag}/{artifact.name}"
    )
    _require_download_url(url)
    return url


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _is_link_like(path: Path, status: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(reparse and attributes & reparse)


def _prepare_fallback_parent(parent: Path) -> None:
    anchor = Path(parent.anchor)
    current = anchor
    for component in parent.parts[1:]:
        current /= component
        try:
            status = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            status = current.lstat()
        if _is_link_like(current, status) or not stat.S_ISDIR(status.st_mode):
            raise SmokeError(f"OpenCode output ancestor is not a real directory: {current}")


def _open_posix_parent(parent: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(parent.anchor, directory_flags)
    try:
        for component in parent.parts[1:]:
            try:
                os.mkdir(component, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(
                component,
                directory_flags | nofollow,
                dir_fd=descriptor,
            )
            status = os.fstat(child)
            if not stat.S_ISDIR(status.st_mode):
                os.close(child)
                raise SmokeError("OpenCode output ancestor is not a directory")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _exclusive_output(path: Path) -> Iterator[tuple[BinaryIO, Path]]:
    absolute = _absolute(path)
    # O_CREAT | O_EXCL already refuses an existing symlink atomically.  Do not
    # add O_NOFOLLOW here: Darwin rejects that flag combination with EINVAL.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    parent_descriptor: int | None = None
    descriptor = -1
    try:
        if os.name == "posix" and os.open in os.supports_dir_fd:
            parent_descriptor = _open_posix_parent(absolute.parent)
            descriptor = os.open(
                absolute.name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        else:
            _prepare_fallback_parent(absolute.parent)
            descriptor = os.open(absolute, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            descriptor = -1
            try:
                yield output, absolute
            except BaseException:
                if parent_descriptor is not None:
                    os.unlink(absolute.name, dir_fd=parent_descriptor)
                else:
                    absolute.unlink(missing_ok=True)
                raise
    except FileExistsError as exc:
        raise SmokeError(f"OpenCode output already exists: {absolute}") from exc
    except SmokeError:
        raise
    except OSError as exc:
        raise SmokeError(f"cannot create exclusive OpenCode output: {absolute}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(_CHUNK_BYTES), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SmokeError(f"cannot read OpenCode archive: {path}") from exc
    return digest.hexdigest()


def verify_archive(path: Path, artifact: Artifact) -> None:
    """Require a regular bounded archive with the manifest SHA-256 digest."""
    try:
        status = path.lstat()
    except OSError as exc:
        raise SmokeError(f"cannot inspect OpenCode archive: {path}") from exc
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_size == 0
        or status.st_size > _MAX_ARCHIVE_BYTES
    ):
        raise SmokeError("OpenCode archive must be a non-empty bounded regular file")
    actual = _sha256(path)
    if actual != artifact.sha256:
        raise SmokeError(
            f"OpenCode archive digest mismatch for {artifact.name}: "
            f"expected {artifact.sha256}, got {actual}"
        )


def download_archive(contract: Compatibility, artifact: Artifact, destination: Path) -> None:
    """Download one exact release asset over HTTPS and verify it before use."""
    request = Request(
        artifact_url(contract, artifact),
        headers={"Accept": "application/octet-stream", "User-Agent": "synapse-channel"},
    )
    opener = build_opener(_PinnedRedirectHandler())
    written = 0
    digest = hashlib.sha256()
    try:
        with opener.open(request, timeout=90) as response:  # nosec B310
            _require_download_url(response.geturl())
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > _MAX_ARCHIVE_BYTES:
                raise SmokeError("OpenCode release archive exceeds the download limit")
            with _exclusive_output(destination) as (output, _):
                while chunk := response.read(_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_ARCHIVE_BYTES:
                        raise SmokeError("OpenCode release archive exceeds the download limit")
                    output.write(chunk)
                    digest.update(chunk)
                if written == 0 or digest.hexdigest() != artifact.sha256:
                    raise SmokeError(
                        f"downloaded OpenCode archive failed integrity verification: "
                        f"{artifact.name}"
                    )
                output.flush()
                os.fsync(output.fileno())
    except (HTTPError, URLError, OSError, ValueError, SmokeError) as exc:
        if isinstance(exc, SmokeError):
            raise
        raise SmokeError(f"cannot download {artifact.name}: {exc}") from exc


def _copy_bounded(source: IO[bytes], destination: IO[bytes]) -> int:
    written = 0
    while chunk := source.read(_CHUNK_BYTES):
        written += len(chunk)
        if written > _MAX_BINARY_BYTES:
            raise SmokeError("OpenCode binary exceeds the extraction limit")
        destination.write(chunk)
    if written == 0:
        raise SmokeError("OpenCode archive contains an empty binary")
    return written


def _zip_declared_entries(path: Path) -> int:
    size = path.stat().st_size
    tail_bytes = min(size, _ZIP_EOCD_BYTES + _ZIP_MAX_COMMENT_BYTES)
    with path.open("rb") as source:
        source.seek(size - tail_bytes)
        tail = source.read(tail_bytes)
    offset = tail.rfind(_ZIP_EOCD)
    if offset < 0 or len(tail) - offset < _ZIP_EOCD_BYTES:
        raise SmokeError("OpenCode ZIP has no valid end-of-central-directory record")
    fields = struct.unpack_from("<4s4H2LH", tail, offset)
    _, disk, central_disk, disk_entries, total_entries, central_size, _, comment = fields
    if offset + _ZIP_EOCD_BYTES + comment != len(tail):
        raise SmokeError("OpenCode ZIP end record or comment length is malformed")
    if disk != 0 or central_disk != 0 or disk_entries != total_entries:
        raise SmokeError("OpenCode ZIP must not span multiple disks")
    if total_entries == 0xFFFF or central_size > _MAX_ARCHIVE_BYTES:
        raise SmokeError("OpenCode ZIP64 or central directory exceeds the metadata limit")
    if total_entries == 0 or total_entries > _MAX_ARCHIVE_MEMBERS:
        raise SmokeError("OpenCode ZIP member count exceeds the metadata limit")
    return int(total_entries)


def _copy_zip_binary(path: Path, artifact: Artifact, destination: IO[bytes]) -> int:
    declared_entries = _zip_declared_entries(path)
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != declared_entries or len(members) != 1:
            raise SmokeError("OpenCode ZIP must contain only the exact root binary")
        member = members[0]
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if (
            member.filename != artifact.binary
            or member.is_dir()
            or file_type not in (0, stat.S_IFREG)
            or member.file_size > _MAX_BINARY_BYTES
        ):
            raise SmokeError(
                "OpenCode ZIP binary member is not a bounded regular file at archive root"
            )
        with archive.open(member, "r") as source:
            return _copy_bounded(source, destination)


def _copy_tar_binary(path: Path, artifact: Artifact, destination: IO[bytes]) -> int:
    with gzip.open(path, "rb") as expanded:
        limited = _BoundedReader(cast(IO[bytes], expanded), _MAX_TAR_STREAM_BYTES)
        with tarfile.open(name=None, fileobj=limited, mode="r|") as archive:
            member = archive.next()
            if (
                member is None
                or member.name != artifact.binary
                or not member.isfile()
                or member.size > _MAX_BINARY_BYTES
            ):
                raise SmokeError(
                    "OpenCode tarball binary is not a bounded regular file at archive root"
                )
            source = archive.extractfile(member)
            if source is None:
                raise SmokeError("OpenCode tarball binary member could not be opened")
            with source:
                written = _copy_bounded(source, destination)
            if archive.next() is not None:
                raise SmokeError("OpenCode tarball must contain only the exact root binary")
            return written


def _copy_archive_member(path: Path, artifact: Artifact, destination: IO[bytes]) -> int:
    try:
        if artifact.name.endswith(".zip"):
            return _copy_zip_binary(path, artifact, destination)
        if artifact.name.endswith(".tar.gz"):
            return _copy_tar_binary(path, artifact, destination)
    except SmokeError:
        raise
    except (EOFError, KeyError, OSError, RuntimeError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise SmokeError(f"cannot inspect OpenCode archive: {path}") from exc
    raise SmokeError(f"unsupported OpenCode archive format: {artifact.name}")


def install_archive(archive_path: Path, artifact: Artifact, destination: Path) -> None:
    """Verify and extract only the exact root binary without following links."""
    verify_archive(archive_path, artifact)
    with _exclusive_output(destination) as (output, absolute):
        _copy_archive_member(archive_path, artifact, output)
        output.flush()
        os.fsync(output.fileno())
        if hasattr(os, "fchmod"):
            os.fchmod(output.fileno(), 0o700)
        else:
            os.chmod(absolute, 0o700)


def write_report(path: Path | None, report: Mapping[str, object]) -> None:
    """Write one private, exclusive compatibility report through a safe parent."""
    if path is None:
        return
    with _exclusive_output(path) as (output, _):
        output.write((json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
        output.flush()
        os.fsync(output.fileno())
