# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — source-isolated durable inbox cursors
"""Keep hub sequence cursors separate from local feed and wake cursors."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class HubInboxCursor:
    """Actual hub identity and last consumed journal sequence."""

    hub_id: str = ""
    seq: int = 0


def hub_inbox_cursor_path(home: Path, uri: str, identity: str) -> Path:
    """Return an independent cursor path for one endpoint and exact identity.

    Parameters
    ----------
    home : Path
        Owner-local coordination home; existing feed cursors remain separate.
    uri : str
        WebSocket endpoint without inline credentials, query or fragment.
    identity : str
        Exact identity whose journal pages are consumed.

    Returns
    -------
    Path
        Hashed flat filename under ``hub-inbox-cursor``.

    Raises
    ------
    ValueError
        When the source endpoint or identity is invalid.
    """
    parsed = urlsplit(uri)
    if (
        parsed.scheme not in {"ws", "wss"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not identity.strip()
    ):
        raise ValueError("inbox requires a WebSocket endpoint without inline credentials")
    digest = hashlib.sha256((uri + "\0" + identity).encode()).hexdigest()
    return home / "hub-inbox-cursor" / (digest + ".json")


def load_hub_inbox_cursor(path: Path) -> HubInboxCursor:
    """Read a hub-bound cursor, refusing corrupt state instead of replaying it.

    Parameters
    ----------
    path : Path
        Cursor location selected by endpoint and identity.

    Returns
    -------
    HubInboxCursor
        Empty initial cursor when absent; otherwise the validated stored cursor.

    Raises
    ------
    ValueError
        When persisted state has an invalid schema or value.
    OSError
        When existing cursor state cannot be read.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HubInboxCursor()
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
        raise ValueError("invalid hub inbox cursor")
    hub_id, seq = raw.get("hub_id"), raw.get("seq")
    if (
        not isinstance(hub_id, str)
        or not hub_id
        or type(seq) is not int
        or not 0 <= seq <= 9223372036854775807
    ):
        raise ValueError("invalid hub inbox cursor")
    return HubInboxCursor(hub_id, seq)


def save_hub_inbox_cursor(path: Path, cursor: HubInboxCursor) -> None:
    """Atomically save a validated owner-only cursor after returning a page.

    Parameters
    ----------
    path : Path
        Owner-local source-specific cursor file.
    cursor : HubInboxCursor
        Actual hub identity and nonnegative last scanned sequence.

    Raises
    ------
    ValueError
        When the cursor has no hub binding or invalid sequence.
    OSError
        When persistence fails; the preceding cursor remains the resume point.
    """
    if (
        not isinstance(cursor.hub_id, str)
        or not cursor.hub_id
        or type(cursor.seq) is not int
        or not 0 <= cursor.seq <= 9223372036854775807
    ):
        raise ValueError("invalid hub inbox cursor")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "hub_id": cursor.hub_id, "seq": cursor.seq}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
