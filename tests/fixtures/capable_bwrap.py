# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Bubblewrap for fixtures that mount inside their own namespace
"""Choose the Bubblewrap executable for fixtures that need capabilities in their namespace.

A few namespace fixtures call ``mount(2)`` inside their own disposable user and
mount namespace. On Ubuntu 26.04 the shipped ``bwrap-userns-restrict`` AppArmor
profile removes every capability from children of ``/usr/bin/bwrap``, so the CI
runner installs a root-owned copy with its own profile and names it in
``SYNAPSE_TEST_CAPABLE_BWRAP``. Everywhere else the default ``/usr/bin/bwrap``
is used. This is a CI test capability only; product code never reads it.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from pathlib import Path

ENVIRONMENT = "SYNAPSE_TEST_CAPABLE_BWRAP"
"""Variable that names the capability-capable Bubblewrap on the CI runner."""

DEFAULT = "/usr/bin/bwrap"
"""Bubblewrap used when the variable is absent."""


def capable_bwrap_path(environ: Mapping[str, str]) -> str:
    """Return the configured Bubblewrap path, or the default when it is not set.

    Parameters
    ----------
    environ : Mapping[str, str]
        Environment to read.

    Returns
    -------
    str
        The unvalidated path.
    """
    return environ.get(ENVIRONMENT, DEFAULT)


def require_trusted_directory(path: Path) -> None:
    """Refuse a directory that a non-root account could change.

    Parameters
    ----------
    path : Path
        Directory to inspect without following a final symbolic link.

    Raises
    ------
    ValueError
        If the path is a symbolic link or not a directory, is not owned by root, or
        is writable by its group or by others.
    """
    status = os.lstat(path)
    if not stat.S_ISDIR(status.st_mode):
        raise ValueError(f"capable bwrap parent is not a directory: {path}")
    if status.st_uid != 0:
        raise ValueError(f"capable bwrap parent is not root-owned: {path}")
    if status.st_mode & 0o022:
        raise ValueError(f"capable bwrap parent is group- or world-writable: {path}")


def validated_capable_bwrap(value: str) -> str:
    """Return ``value`` when it names a root-owned executable that only root can change.

    Parameters
    ----------
    value : str
        Candidate Bubblewrap path.

    Returns
    -------
    str
        The same path.

    Raises
    ------
    ValueError
        If the path is relative or not normalised, missing, a symbolic link, not a
        regular file, writable by its group or others, not owned by root, or inside
        a directory that fails :func:`require_trusted_directory`.
    """
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(value) != value:
        raise ValueError(f"capable bwrap path must be absolute and normalised: {value}")
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        raise ValueError(f"capable bwrap does not exist: {value}") from None
    if stat.S_ISLNK(status.st_mode):
        raise ValueError(f"capable bwrap is a symbolic link: {value}")
    if not stat.S_ISREG(status.st_mode):
        raise ValueError(f"capable bwrap is not a regular file: {value}")
    if status.st_mode & 0o022:
        raise ValueError(f"capable bwrap is group- or world-writable: {value}")
    if status.st_uid != 0:
        raise ValueError(f"capable bwrap is not root-owned: {value}")
    for parent in path.parents:
        require_trusted_directory(parent)
    return value


def capable_bwrap() -> str:
    """Return the validated Bubblewrap for this process's environment.

    Returns
    -------
    str
        A root-owned Bubblewrap path that only root can change.
    """
    return validated_capable_bwrap(capable_bwrap_path(os.environ))
