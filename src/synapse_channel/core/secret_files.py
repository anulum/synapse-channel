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

Secure file forms are fail-closed POSIX surfaces. The loader opens one bounded
descriptor with ``O_NOFOLLOW``, validates that same descriptor as a regular file
owned by the effective service user with mode ``0600`` or stricter, then reads
only from it. Platforms that cannot prove those invariants must use a validated
native secret provider instead of these flags.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from synapse_channel.core.errors import SynapseError

_GROUP_OTHER_BITS = 0o077
"""Permission bits that grant any non-owner access to a secret file."""

DEFAULT_SECRET_FILE_LIMIT = 65_536
"""Maximum bytes accepted from one CLI secret file."""

_POSIX = os.name == "posix"
"""Whether this platform expresses POSIX permission modes; captured once at import."""


class SecretFileError(SynapseError, ValueError):
    """Raised when a secret file is missing, malformed, or readable by others.

    The message names the flag and the path, never the file's content.
    """

    code = "secret_file"


def _read_owner_only_text(
    path: Path,
    *,
    flag: str,
    limit: int = DEFAULT_SECRET_FILE_LIMIT,
) -> str:
    """Read bounded UTF-8 from one same-descriptor owner-only regular file."""
    if not _POSIX or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid"):
        raise SecretFileError(
            f"{flag}: secure owner-only file validation is unavailable on this platform"
        )
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely open {path}: {exc.strerror or exc}"
        ) from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecretFileError(f"{flag}: {path} is not a regular secret file")
        if info.st_uid != os.geteuid():
            raise SecretFileError(f"{flag}: {path} is not owned by the effective hub service user")
        mode = stat.S_IMODE(info.st_mode)
        if mode & _GROUP_OTHER_BITS:
            raise SecretFileError(
                f"{flag}: {path} is accessible by other users (mode {mode:03o}); a secret "
                f"file must be owner-only — run: chmod 600 {path}"
            )
        if info.st_size > limit:
            raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise SecretFileError(f"{flag}: {path} exceeds the {limit}-byte secret-file limit")
    except OSError as exc:
        raise SecretFileError(
            f"{flag}: cannot securely read {path}: {exc.strerror or exc}"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SecretFileError(f"{flag}: {path} is not valid UTF-8") from exc


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
    secret = _read_owner_only_text(path, flag=flag).strip()
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
    text = _read_owner_only_text(path, flag=flag)
    entries = tuple(
        stripped for line in text.splitlines() if (stripped := line.strip()) and stripped[0] != "#"
    )
    if not entries:
        raise SecretFileError(f"{flag}: {path} holds no entries; expected one per line")
    return entries
