# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — CLI hub identity-pin namespace and migration
"""Resolve durable pin ownership before a CLI hub opens its event journal."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

from synapse_channel.cli_hub_startup import HubStartup, HubStartupRefused
from synapse_channel.core.identity_pins import IdentityPinStore


def _journal_exists(path: Path) -> bool:
    """Distinguish a missing journal from inaccessible paths and invalid file kinds.

    ``Path.exists()`` may return false for a symlink loop. Only a genuinely
    missing file can initialise a new trust namespace; other stat failures
    must reach the startup refusal before SQLite opens the path.
    """
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("journal path must be a regular file")
    return True


def _sync_parent(directory: Path) -> None:
    """Flush the published directory entry on platforms with directory descriptors."""
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _initialise_companion(path: Path) -> None:
    """Publish an owner-only empty table without replacing any existing trust.

    A same-directory hard link publishes the fully flushed temporary file in
    one step. A concurrent creator wins unchanged; a filesystem without that
    primitive refuses startup rather than exposing a partial or shared table.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write('{"pins": {}}\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        if path.is_symlink() or not path.is_file():
            raise ValueError("identity pin path must be a regular file without a symlink")
        IdentityPinStore(path=path)
        _sync_parent(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_hub_identity_pins(ctx: HubStartup) -> None:
    """Choose a pin file without silently resetting an existing hub's trust.

    Explicit file and empty-string overrides retain their existing meaning.
    The conventional journal and a hub without a journal retain the legacy
    home file. Each other journal owns a companion named after its full path.
    An existing journal with legacy trust but no companion needs an operator's
    explicit selection; pin rows do not identify their originating hub.

    Parameters
    ----------
    ctx : HubStartup
        Invocation whose parsed pin path is resolved before journal creation.

    Raises
    ------
    HubStartupRefused
        When migration is ambiguous or isolated pin persistence is unavailable.
    """
    if ctx.args.identity_pins is not None:
        return
    legacy = Path.home() / "synapse" / "identity-pins.json"
    if not ctx.args.db or str(ctx.args.db) == ":memory:":
        ctx.args.identity_pins = str(legacy)
        return
    try:
        database = Path(ctx.args.db).resolve()
        database_exists = _journal_exists(database)
        if database == (legacy.parent / "hub.db").resolve():
            ctx.args.identity_pins = str(legacy)
            return
        companion = Path(f"{database}.identity-pins.json")
        if database_exists and legacy.exists() and not companion.exists():
            print(
                "synapse hub: refused to choose identity pins for an existing journal. "
                f"Legacy file: {legacy}; isolated companion: {companion}. "
                "Pass --identity-pins with the legacy file to retain its entire trust table "
                "(including names from other hubs), or with an operator-prepared companion "
                "containing this hub's verified pins. No trust was copied or reset.",
                file=sys.stderr,
            )
            raise HubStartupRefused()
        _initialise_companion(companion)
        ctx.args.identity_pins = str(companion)
    except (OSError, RuntimeError, ValueError):
        print(
            "synapse hub: could not initialise the isolated identity-pin file; "
            "check its parent directory, file format and filesystem permissions. "
            "No in-memory fallback was selected.",
            file=sys.stderr,
        )
        raise HubStartupRefused() from None
