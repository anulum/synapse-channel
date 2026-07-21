# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — portable POSIX tool resolution for cross-OS tests
"""Portable no-op executable resolution for the MCP launch and trust test suites.

The MCP secure-launch and trust tests need a real, absolute, owner-executable
POSIX binary to copy, hash, and (attempt to) launch. They historically hardcoded
``/bin/true`` (and ``/bin/echo``/``/bin/false``), which exist on Linux CI but not
always at that path on macOS — which ships ``/usr/bin/true`` — and not at all on
Windows. This module resolves a named POSIX tool through :func:`shutil.which` and
copies its *bytes only* — never its metadata, since macOS ``copystat`` raises
``PermissionError`` on the sandboxed temporary tree — so the same fixture works on
every POSIX runner and skips cleanly where no such binary exists.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest


def resolve_posix_tool(name: str) -> str:
    """Return the absolute path to POSIX tool ``name``, skipping when absent.

    Parameters
    ----------
    name : str
        Executable name to resolve on ``PATH`` (e.g. ``"true"``).

    Returns
    -------
    str
        Absolute path to the resolved executable.
    """
    resolved = shutil.which(name)
    if resolved is None:
        pytest.skip(f"no POSIX {name!r} executable on this platform")
    return resolved


def install_posix_tool(path: Path, tool: str = "true", *, mode: int = 0o700) -> tuple[Path, str]:
    """Copy POSIX ``tool`` to ``path`` and return ``(path, sha256)``.

    The bytes are copied with :func:`shutil.copyfile` rather than
    :func:`shutil.copy2`: the executable content is all the launch and trust
    checks inspect, and copying metadata triggers ``PermissionError`` from
    ``copystat`` on macOS runners.

    Parameters
    ----------
    path : Path
        Destination path for the copied executable.
    tool : str, optional
        POSIX tool name to copy; defaults to ``"true"``.
    mode : int, optional
        Permission bits applied after the copy; defaults to ``0o700``.

    Returns
    -------
    tuple[Path, str]
        The destination ``path`` and the hex SHA-256 of its bytes.
    """
    src = resolve_posix_tool(tool)
    shutil.copyfile(src, path)
    path.chmod(mode)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()
