# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only secret file loading for CLI flags
"""Load CLI secrets from owner-only files instead of argv.

A secret passed as ``--token SECRET`` is visible to every local user in the
process list; a file the CLI reads at startup is not. This module is the single
loader behind the ``*-file`` companions of secret-bearing hub flags
(``--metrics-token-file``, ``--message-auth-key-file``): it reads the file,
refuses one that other users could read, and never places file content in an
error message, so a wrongly permissioned or malformed secret can be reported and
logged without leaking what it protects.

The permission check is POSIX-only, mirroring
:mod:`~synapse_channel.core.persistence`'s owner-only discipline: on platforms
without POSIX modes the read proceeds, because refusing there would break every
Windows deployment to enforce a check the platform cannot express.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from synapse_channel.core.errors import SynapseError

_GROUP_OTHER_BITS = 0o077
"""Permission bits that grant any non-owner access to a secret file."""

_POSIX = os.name == "posix"
"""Whether this platform expresses POSIX permission modes; captured once at import."""


class SecretFileError(SynapseError, ValueError):
    """Raised when a secret file is missing, malformed, or readable by others.

    The message names the flag and the path, never the file's content.
    """

    code = "secret_file"


def _require_owner_only(path: Path, *, flag: str) -> None:
    """Refuse ``path`` when other users could read it (POSIX platforms only)."""
    if not _POSIX:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & _GROUP_OTHER_BITS:
        raise SecretFileError(
            f"{flag}: {path} is readable by other users (mode {mode:03o}); a secret "
            f"file must be owner-only — run: chmod 600 {path}"
        )


def read_secret_file(file_path: str | Path, *, flag: str) -> str:
    """Read one owner-only secret value, stripped of surrounding whitespace.

    Parameters
    ----------
    file_path : str or pathlib.Path
        File holding exactly one secret value.
    flag : str
        CLI flag being resolved (e.g. ``--metrics-token-file``), named in every
        error so the operator knows which input to fix.

    Returns
    -------
    str
        The secret with surrounding whitespace removed.

    Raises
    ------
    SecretFileError
        If the file is missing, unreadable, empty, or accessible to other
        users. The error never contains file content.
    """
    path = Path(file_path).expanduser()
    try:
        _require_owner_only(path, flag=flag)
        secret = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        # Surface the OS reason (missing, unreadable) without the file's content.
        raise SecretFileError(f"{flag}: cannot read {path}: {exc.strerror or exc}") from exc
    if not secret:
        raise SecretFileError(f"{flag}: {path} is empty; expected one secret value")
    return secret


def read_secret_lines(file_path: str | Path, *, flag: str) -> tuple[str, ...]:
    """Read owner-only secret entries, one per line, skipping blanks and comments.

    Parameters
    ----------
    file_path : str or pathlib.Path
        File holding one entry per line. Blank lines and lines starting with
        ``#`` are ignored, so a rotation file can carry dated annotations.
    flag : str
        CLI flag being resolved, named in every error.

    Returns
    -------
    tuple of str
        The entries in file order.

    Raises
    ------
    SecretFileError
        If the file is missing, unreadable, accessible to other users, or
        carries no entries. The error never contains file content.
    """
    path = Path(file_path).expanduser()
    try:
        _require_owner_only(path, flag=flag)
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SecretFileError(f"{flag}: cannot read {path}: {exc.strerror or exc}") from exc
    entries = tuple(
        stripped for line in text.splitlines() if (stripped := line.strip()) and stripped[0] != "#"
    )
    if not entries:
        raise SecretFileError(f"{flag}: {path} holds no entries; expected one per line")
    return entries
