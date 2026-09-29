# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-authenticated event rows (K4-REPLAY)
"""Authenticate every event row the hub writes, so a forged row is quarantined on replay.

The anti-rollback checkpoint detects a log that was cut or rewritten; it cannot
detect a row appended after the last anchor by anyone who can write the SQLite
file. Replay would then rebuild state from that row — a forged claim, a forged
release — as if the hub had written it (K4-REPLAY).

A hub with a key MACs each row it appends: HMAC-SHA256 over a domain separator, the
row's ``seq``, ``ts``, ``kind`` and exact stored payload text, kept in the row's
``mac`` column. The key lives outside the database, in an owner-only file beside
it, together with the sequence it started at. At start the hub checks every row
after that sequence; a row whose MAC is missing or wrong is quarantined through the
existing journal-recovery path, so replay skips it and the hub refuses mutations
until an operator resolves it. Rows written before the key existed are the legacy
prefix and replay unchanged.

The trust boundary is the key file: someone who can read it can forge rows, which
is the same boundary as the checkpoint store. Independent custody is separate work.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import secrets
import struct
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secret_files import SecretFileError, read_secret_file

ROW_MAC_DOMAIN = b"SYNAPSE-CHANNEL:EVENT-ROW-MAC:v1\x00"
"""Domain separator prepended to every row MAC input."""

ROW_MAC_KEY_SUFFIX = ".rowmac.key"
"""Suffix of the key file placed beside the event store (``<db>.rowmac.key``)."""

_KEY_HEADER = "synapse-row-mac-v1"
_LOST_KEY_REMEDY = (
    "restore the key file from a trusted copy. Only if it is lost and the log was "
    "reviewed, clear the row MACs (UPDATE events SET mac = NULL) so a new key "
    "starts at the current tip"
)
_KEY_BYTES = 32


class RowMacError(SynapseError, ValueError):
    """The row-authentication key is missing, malformed, or cannot be created."""

    code = "row_mac"


@dataclass(frozen=True)
class RowMacKey:
    """The hub's row-authentication key and the first sequence it covers.

    Attributes
    ----------
    key : bytes
        32 secret bytes.
    since_seq : int
        Rows with ``seq`` at or below this value predate the key and are not checked.
    """

    key: bytes
    since_seq: int

    def mac(self, seq: int, ts: float, kind: str, payload: str) -> str:
        """Return the hex MAC of one row as stored."""
        kind_bytes = kind.encode("utf-8", errors="surrogatepass")
        payload_bytes = payload.encode("utf-8", errors="surrogatepass")
        material = b"".join(
            (
                ROW_MAC_DOMAIN,
                struct.pack(">q", seq),
                float(ts).hex().encode("ascii"),
                b"\x00",
                struct.pack(">I", len(kind_bytes)),
                kind_bytes,
                payload_bytes,
            )
        )
        return hmac.new(self.key, material, hashlib.sha256).hexdigest()

    def verify(self, seq: int, ts: object, kind: object, payload: object, mac: object) -> bool:
        """Return whether a stored row carries this key's MAC."""
        if (
            not isinstance(mac, str)
            or isinstance(ts, bool)
            or not isinstance(ts, int | float)
            or not isinstance(kind, str)
            or not isinstance(payload, str)
        ):
            return False
        return hmac.compare_digest(mac, self.mac(seq, float(ts), kind, payload))


def row_mac_key_path(db_path: str | Path) -> Path:
    """Return the default key file for an event store: ``<db>.rowmac.key``."""
    return Path(str(db_path) + ROW_MAC_KEY_SUFFIX)


def _parse(text: str, path: Path) -> RowMacKey:
    parts = text.split()
    if len(parts) != 3 or parts[0] != _KEY_HEADER:
        raise RowMacError(f"row-authentication key {path} is not a {_KEY_HEADER} key")
    try:
        since_seq = int(parts[1])
        key = base64.b64decode(parts[2], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RowMacError(f"row-authentication key {path} is malformed") from exc
    if since_seq < 0 or len(key) != _KEY_BYTES:
        raise RowMacError(f"row-authentication key {path} is malformed")
    return RowMacKey(key=key, since_seq=since_seq)


def load_or_create_row_mac_key(
    path: str | Path, *, current_max_seq: int, log_has_macs: bool
) -> RowMacKey:
    """Load the hub's row key, or create it at the log's current tip.

    Parameters
    ----------
    path : str or pathlib.Path
        The key file (owner-only).
    current_max_seq : int
        The log's highest sequence; a new key covers only rows after it.
    log_has_macs : bool
        Whether any row already carries a MAC. Then a missing key is an error: a new
        key would silently exempt every existing row.

    Returns
    -------
    RowMacKey
        The loaded or newly created key.

    Raises
    ------
    RowMacError
        When the file is unreadable, not owner-only, malformed, missing while the log
        is already authenticated, or cannot be created.
    """
    target = Path(path)
    if target.exists() or target.is_symlink():
        try:
            return _parse(read_secret_file(target, flag="row-authentication key"), target)
        except SecretFileError as exc:
            raise RowMacError(f"cannot read row-authentication key {target}: {exc}") from exc
    if log_has_macs:
        raise RowMacError(
            f"the event log carries authenticated rows but the key {target} is missing; "
            + _LOST_KEY_REMEDY
        )
    key = RowMacKey(key=secrets.token_bytes(_KEY_BYTES), since_seq=max(0, current_max_seq))
    line = f"{_KEY_HEADER} {key.since_seq} {base64.b64encode(key.key).decode('ascii')}\n"
    try:
        descriptor = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
    except OSError as exc:
        raise RowMacError(f"cannot create row-authentication key {target}: {exc}") from exc
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
    return key
