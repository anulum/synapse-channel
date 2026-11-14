# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — where a dashboard start keeps the bearer it generated
"""Keep a generated dashboard bearer in an owner-only file, never on the terminal.

A ``synapse dashboard`` start without ``--dashboard-token`` or
``--dashboard-access-file`` generates a bearer, because every read is gated.
The person at the front door needs it for the cockpit's unlock screen, so the
start writes it to ``$XDG_STATE_HOME/synapse/dashboard/bearer.token`` (or
``~/.local/state/…``) and prints only that path. Each start writes a new one.

The directory is created owner-only and must be owner-only (and not a link)
when it already exists, or the start refuses. The file is written beside its
final name with ``O_EXCL`` and ``O_NOFOLLOW``, made owner-only, and moved into
place in one step, so no partial or foreign file is ever read as the bearer.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from synapse_channel.core.secure_path import (
    apply_owner_only_dir,
    apply_owner_only_file,
    assert_owner_only_dir_path,
)

BEARER_FILE = "bearer.token"
"""The file name of the generated bearer inside :func:`bearer_directory`."""


def bearer_directory(environ: Mapping[str, str] | None = None) -> Path:
    """Return the directory a dashboard start keeps its generated bearer in."""
    values = os.environ if environ is None else environ
    state = values.get("XDG_STATE_HOME", "").strip()
    base = Path(state) if state else Path.home() / ".local" / "state"
    return base / "synapse" / "dashboard"


def write_generated_bearer(token: str, directory: Path | None = None) -> Path:
    """Write ``token`` owner-only into ``directory`` in one step; return the file's path.

    Raises
    ------
    synapse_channel.core.secure_path.SecurePathError
        When the directory is a link, belongs to another user, or is open to others.
    OSError
        When the directory or the file cannot be written.
    """
    target = bearer_directory() if directory is None else directory
    if not target.exists():
        target.mkdir(parents=True, mode=0o700)
        apply_owner_only_dir(target)
    assert_owner_only_dir_path(target, purpose="the dashboard bearer directory")
    final = target / BEARER_FILE
    staged = target / f".{BEARER_FILE}.{secrets.token_hex(8)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(staged, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        apply_owner_only_file(staged)
        os.replace(staged, final)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return final
