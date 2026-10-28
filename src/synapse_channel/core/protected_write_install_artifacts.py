# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — root-owned pinned installation artifact capture
"""Read exact installation bytes through root-owned non-writable descriptor ancestry."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProtectedInstallArtifact:
    """Pinned artifact bytes and inode, not continued installation custody.

    Attributes
    ----------
    path : str
        Exact absolute path whose root-owned ancestry was inspected.
    device, inode : int
        Actual filesystem identity observed through the opened descriptor.
    mode : int
        Permission and special mode bits at capture, without the file-type bits.
    sha256 : str
        Lowercase raw-byte digest matching the independent operator pin.
    content : bytes
        Immutable captured bytes, omitted from the representation. Callers must
        keep private content out of diagnostics and public evidence.
    """

    path: str
    device: int
    inode: int
    mode: int
    sha256: str
    content: bytes = field(repr=False)


def _validate_install_artifact_enrollment(
    path: str, expected_sha256: str, max_bytes: int, require_private: bool
) -> None:
    """Validate the exact artifact declaration before descriptor ancestry is opened."""
    if (
        type(path) is not str
        or not path.startswith("/")
        or any(part in {"", ".", ".."} for part in path.split("/")[1:])
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or len(path.encode("utf-8")) > 4096
        or type(expected_sha256) is not str
        or re.fullmatch("[0-9a-f]{64}", expected_sha256) is None
        or type(max_bytes) is not int
        or not 0 < max_bytes <= 64 * 1024 * 1024
        or type(require_private) is not bool
    ):
        raise ValueError("invalid pinned installation artifact enrollment")


def capture_protected_install_artifact(
    path: str,
    *,
    expected_sha256: str,
    max_bytes: int,
    require_private: bool,
) -> ProtectedInstallArtifact:
    """Read one digest-pinned root artifact through retained descriptor ancestry.

    Parameters
    ----------
    path:
        Exact absolute operator-enrolled path without symlinks or dot traversal.
    expected_sha256:
        Independently reviewed lowercase raw-byte SHA256 of the artifact.
    max_bytes:
        Positive exact read budget, at most 64 MiB.
    require_private:
        Require no group/other access for config, credentials or trust secrets.
        Readable executable and wheel artifacts use False.

    Returns
    -------
    ProtectedInstallArtifact
        Immutable actual bytes and retained inode metadata. Content is omitted
        from the object's representation and must never enter public diagnostics.

    Raises
    ------
    ValueError
        On invalid enrollment, writable/non-root ancestry or artifact, nonregular
        file, shared private artifact, oversize content or a digest mismatch.
    OSError
        On symlinks, inaccessible root/private bytes or unavailable descriptors.

    Notes
    -----
    Performs no installation, account, ownership, permission or mount changes.
    This checks one read's exact bytes and ancestry, not a complete interpreter
    dependency manifest, syscall policy, launch, namespace or ongoing custody.
    The privileged supervisor must retain a sealed installation and enroll every
    import/config/credential path independently before granting writer authority.
    Never deserialize a caller artifact object as proof of those prerequisites.
    """
    _validate_install_artifact_enrollment(path, expected_sha256, max_bytes, require_private)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("protected installation capture requires POSIX no-follow descriptors")
    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        parts = path.split("/")[1:]
        for part in parts[:-1]:
            _require_root_directory(current)
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        _require_root_directory(current)
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current
        )
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or info.st_gid != 0
                or info.st_mode & 0o022
            ):
                raise ValueError("installation artifact is not a root-owned immutable regular file")
            if require_private and info.st_mode & 0o077:
                raise ValueError("private installation artifact is accessible to another principal")
            content = bytearray()
            while True:
                chunk = os.read(descriptor, min(262144, max_bytes + 1 - len(content)))
                if not chunk:
                    break
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise ValueError("installation artifact exceeds read budget")
            actual = hashlib.sha256(content).hexdigest()
            if actual != expected_sha256:
                raise ValueError("installation artifact differs from reviewed digest")
            return ProtectedInstallArtifact(
                path, info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), actual, bytes(content)
            )
        finally:
            os.close(descriptor)
    finally:
        os.close(current)


def _require_root_directory(descriptor: int) -> None:
    """Refuse installation ancestry writable by a non-root principal."""
    info = os.fstat(descriptor)
    if info.st_uid != 0 or info.st_gid != 0 or info.st_mode & 0o022:
        raise ValueError("installation artifact ancestry is not root-owned and sealed")
