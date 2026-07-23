# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only directory floor for private runtime and key dirs
"""Materialise a directory that only its owner can read or write.

A predictable directory name under a shared root — ``/tmp/synapse-*``,
``$XDG_RUNTIME_DIR``, ``~/.config/synapse`` — is a clobber target on a
multi-user or CI host. A local attacker who pre-creates it as a symlink, or owns
it with group- or other-accessible mode, can redirect or read whatever the
service writes there (session logs, wake registries, identity keys).

``Path.mkdir(mode=0o700, exist_ok=True)`` does not defend this: the ``mode`` is
ignored when the directory already exists, and a pre-existing symlink or
foreign-owned directory is accepted silently. This module is the directory
analogue of :mod:`~synapse_channel.core.secret_files`. On POSIX it creates the
leaf explicitly and validates the *same descriptor* it opened (``O_NOFOLLOW |
O_DIRECTORY``) as a real directory owned by the effective user with no group or
other bits — never a symlink, never foreign-owned. A loose-but-owned directory
is re-tightened in place through that descriptor (``fchmod``). On Windows it
creates the leaf, applies an owner-only DACL, and proves the same intent via
:mod:`~synapse_channel.core.secure_path`. Anything else fails closed. Platforms
that cannot prove these invariants raise rather than pretend.

The strict floor is applied to the leaf — the predictable clobber target. When
``parents=True`` the ancestors are created owner-only under the caller's trusted
root (the same traversal-trust boundary :mod:`secret_files` relies on).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import (
    SecurePathError,
    apply_owner_only_dir,
    assert_owner_only_dir_path,
    owner_only_floor_available,
)

_GROUP_OTHER_BITS = 0o077
"""Permission bits that grant any non-owner access to a directory."""

_POSIX = os.name == "posix"
"""Whether this platform expresses POSIX permission modes; captured once at import."""

_WINDOWS = os.name == "nt"
"""Whether this platform uses NT security descriptors for the owner-only floor."""


class PrivateDirError(SynapseError, ValueError):
    """Raised when a private directory cannot be created or proven owner-only.

    The message names the directory's purpose and path, so an operator can fix
    a hijacked or loosely permissioned path without guessing which one failed.
    """

    code = "private_dir"


def _validate_private_dir(target: Path, *, purpose: str) -> Path:
    """Validate ``target`` through a single ``O_NOFOLLOW`` directory descriptor."""
    flags = (
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        # ``O_NOFOLLOW`` rejects a symlink at the leaf (ELOOP); ``O_DIRECTORY``
        # rejects a regular file (ENOTDIR). Either is a clobbered path.
        raise PrivateDirError(
            f"{purpose}: cannot securely open {target}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise PrivateDirError(f"{purpose}: {target} is not a directory")
        if info.st_uid != os.geteuid():
            raise PrivateDirError(f"{purpose}: {target} is not owned by the effective user")
        mode = stat.S_IMODE(info.st_mode)
        if mode & _GROUP_OTHER_BITS:
            # Owned by us but loose: re-tighten in place through the descriptor,
            # so no window exists where another user could open the path.
            try:
                os.fchmod(descriptor, 0o700)
            except OSError as exc:
                raise PrivateDirError(
                    f"{purpose}: {target} is accessible by other users (mode {mode:03o}) "
                    f"and cannot be tightened: {exc.strerror or exc}"
                ) from exc
            if stat.S_IMODE(os.fstat(descriptor).st_mode) & _GROUP_OTHER_BITS:
                raise PrivateDirError(
                    f"{purpose}: {target} remains accessible by other users after tightening"
                )
    finally:
        os.close(descriptor)
    return target


def ensure_private_dir(
    path: str | Path,
    *,
    parents: bool = False,
    purpose: str = "private directory",
) -> Path:
    """Return ``path`` as a directory only its owner can read or write.

    Creates the leaf ``0700`` when absent and validates it (whether freshly
    created or pre-existing) as a real, owner-owned directory with no group or
    other access. A pre-existing symlink, foreign-owned directory, or
    non-directory fails closed; a loose-but-owned directory is re-tightened.

    Parameters
    ----------
    path : str or pathlib.Path
        The private directory to materialise.
    parents : bool, optional
        Create missing ancestors owner-only first. The strict floor still
        applies only to the leaf — the predictable clobber target — with
        ancestors trusted under the caller's root, as :mod:`secret_files`
        trusts parent traversal.
    purpose : str, optional
        Human name for the directory, placed in every error (e.g. ``"machine
        identity directory"``).

    Returns
    -------
    pathlib.Path
        The validated directory.

    Raises
    ------
    PrivateDirError
        When the directory cannot be created, is a symlink or non-directory,
        is owned by another user, or cannot be made owner-only. The platform
        must be able to prove the owner-only floor (POSIX modes or Windows DACL).
    """
    target = Path(path)
    if not owner_only_floor_available():
        raise PrivateDirError(
            f"{purpose}: owner-only directory validation is unavailable on this platform"
        )
    if _WINDOWS:
        return _ensure_private_dir_windows(target, parents=parents, purpose=purpose)
    if not _POSIX or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid"):
        raise PrivateDirError(
            f"{purpose}: owner-only directory validation is unavailable on this platform"
        )
    if parents and target.parent not in (target, Path(target.anchor)):
        try:
            os.makedirs(target.parent, mode=0o700, exist_ok=True)
        except OSError as exc:
            raise PrivateDirError(
                f"{purpose}: cannot create parents of {target}: {exc.strerror or exc}"
            ) from exc
    try:
        os.mkdir(target, 0o700)
    except FileExistsError:
        # A pre-existing path is validated (not trusted) below.
        pass
    except OSError as exc:
        raise PrivateDirError(f"{purpose}: cannot create {target}: {exc.strerror or exc}") from exc
    return _validate_private_dir(target, purpose=purpose)


def _ensure_private_dir_windows(
    target: Path,
    *,
    parents: bool,
    purpose: str,
) -> Path:
    """Create and prove an owner-only directory under the NT security model."""
    if parents and target.parent not in (target, Path(target.anchor)):
        try:
            # Ancestors are a trusted root (same boundary as the POSIX path);
            # the strict owner-only floor still applies only to the leaf.
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PrivateDirError(
                f"{purpose}: cannot create parents of {target}: {exc.strerror or exc}"
            ) from exc
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError:
        pass
    except OSError as exc:
        raise PrivateDirError(f"{purpose}: cannot create {target}: {exc.strerror or exc}") from exc
    try:
        apply_owner_only_dir(target)
        assert_owner_only_dir_path(target, purpose=purpose)
    except SecurePathError as exc:
        message = str(exc)
        if "symlink" in message:
            raise PrivateDirError(
                f"{purpose}: cannot securely open {target}: symlink refused"
            ) from exc
        if "not a directory" in message:
            raise PrivateDirError(f"{purpose}: {target} is not a directory") from exc
        if "not owned" in message or "effective user" in message:
            raise PrivateDirError(
                f"{purpose}: {target} is not owned by the effective user"
            ) from exc
        if "accessible by other" in message or "NULL DACL" in message or "ACE for" in message:
            raise PrivateDirError(
                f"{purpose}: {target} is accessible by other users and cannot be proven "
                f"owner-only: {message}"
            ) from exc
        if "unavailable" in message:
            raise PrivateDirError(
                f"{purpose}: owner-only directory validation is unavailable on this platform"
            ) from exc
        raise PrivateDirError(f"{purpose}: {message}") from exc
    return target
