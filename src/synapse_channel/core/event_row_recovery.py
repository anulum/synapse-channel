# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded, non-secret recovery markers for corrupt event rows
"""Decode durable event rows without letting one malformed row brick startup.

The SQLite event log is an audit and coordination spine.  Silently discarding a
malformed row would make the reconstructed state look authoritative when it is
not, while propagating a JSON/type error prevents every operator diagnostic from
starting.  This module takes the narrow middle path: it replaces a malformed row
with a typed, non-secret marker that preserves its sequence and a payload digest.
The hub can then expose degraded health and refuse mutations until an operator
archives and explicitly removes the affected settled row.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast

CORRUPT_EVENT_KIND = "corrupt_event"
"""Synthetic in-memory event kind used for a quarantined SQLite row."""

CORRUPT_EVENT_SCHEMA_VERSION = 1
"""Schema version of the public, non-secret corrupt-row marker payload."""


class CorruptEventReason(str, Enum):
    """Stable machine-readable reasons a stored event row was quarantined."""

    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_KIND = "invalid_kind"
    PAYLOAD_NOT_TEXT = "payload_not_text"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    PAYLOAD_NOT_OBJECT = "payload_not_object"
    RESERVED_KIND = "reserved_kind"


@dataclass(frozen=True, slots=True)
class CorruptEventRow:
    """Safe forensic identity for one quarantined event-store row.

    Attributes
    ----------
    seq : int
        Original monotonic event sequence.
    original_kind : str or None
        Stored kind only when it is short, printable, and control-free.
    reasons : tuple[CorruptEventReason, ...]
        Deterministic validation failures in discovery order.
    payload_sha256 : str
        Domain-separated SHA-256 digest of the raw SQLite payload value.
    """

    seq: int
    original_kind: str | None
    reasons: tuple[CorruptEventReason, ...]
    payload_sha256: str

    def as_payload(self) -> dict[str, Any]:
        """Return the non-secret marker body exposed to readers and operators."""
        return {
            "schema_version": CORRUPT_EVENT_SCHEMA_VERSION,
            "seq": self.seq,
            "original_kind": self.original_kind,
            "reasons": [reason.value for reason in self.reasons],
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_payload(cls, seq: int, payload: dict[str, Any]) -> CorruptEventRow:
        """Validate and reconstruct a marker emitted by :func:`decode_event_row`.

        Parameters
        ----------
        seq : int
            Sequence carried by the surrounding :class:`StoredEvent`.
        payload : dict[str, Any]
            Candidate marker payload.

        Returns
        -------
        CorruptEventRow
            Strictly validated marker.

        Raises
        ------
        ValueError
            If the payload is not an authentic decoder-shaped marker.
        """
        marker_seq = payload.get("seq")
        reasons_raw = payload.get("reasons")
        digest = payload.get("payload_sha256")
        original_kind = payload.get("original_kind")
        schema_version = payload.get("schema_version")
        if type(schema_version) is not int or schema_version != CORRUPT_EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported corrupt-event marker schema")
        if isinstance(marker_seq, bool) or not isinstance(marker_seq, int) or marker_seq != seq:
            raise ValueError("corrupt-event marker sequence mismatch")
        if original_kind is not None and _safe_original_kind(original_kind) != original_kind:
            raise ValueError("unsafe corrupt-event original kind")
        if not isinstance(reasons_raw, list) or not reasons_raw:
            raise ValueError("corrupt-event marker has no reasons")
        try:
            reasons = tuple(CorruptEventReason(reason) for reason in reasons_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid corrupt-event reason") from exc
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("invalid corrupt-event payload digest")
        return cls(
            seq=seq,
            original_kind=original_kind,
            reasons=reasons,
            payload_sha256=digest,
        )


@dataclass(frozen=True, slots=True)
class DecodedEventRow:
    """Validated event values plus optional quarantine metadata.

    Attributes
    ----------
    seq, ts, kind, payload
        Values safe to expose through the existing event-store read API.
    corruption : CorruptEventRow or None
        Forensic marker when any stored field failed validation.
    """

    seq: int
    ts: float
    kind: str
    payload: dict[str, Any]
    corruption: CorruptEventRow | None


def _safe_original_kind(value: object) -> str | None:
    """Return a bounded printable kind or ``None`` for unsafe stored content."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if not value.isprintable() or any(character in "\r\n" for character in value):
        return None
    return value


def _payload_digest(value: object) -> str:
    """Return a domain-separated digest for any raw SQLite payload value."""
    if value is None:
        material = b"null\0"
    elif isinstance(value, bytes):
        material = b"blob\0" + value
    elif isinstance(value, str):
        material = b"text\0" + value.encode("utf-8", errors="surrogatepass")
    elif isinstance(value, bool):
        material = b"integer\0" + (b"1" if value else b"0")
    elif isinstance(value, int):
        material = b"integer\0" + str(value).encode("ascii")
    elif isinstance(value, float):
        material = b"real\0" + value.hex().encode("ascii")
    else:
        type_name = f"{type(value).__module__}.{type(value).__qualname__}"
        material = b"unknown\0" + type_name.encode("utf-8", errors="replace")
    return hashlib.sha256(material).hexdigest()


def decode_event_row(row: Sequence[object]) -> DecodedEventRow:
    """Decode one ``(seq, ts, kind, payload)`` SQLite row into safe values.

    JSON/type/Unicode failures are converted to a deterministic marker.  Resource
    failures such as :class:`MemoryError` deliberately propagate rather than being
    mislabeled as durable corruption.

    Parameters
    ----------
    row : Sequence[object]
        Four values selected directly from the event table.

    Returns
    -------
    DecodedEventRow
        Original validated event or a synthetic ``corrupt_event`` marker.
    """
    if len(row) != 4:
        raise ValueError("event row must contain seq, ts, kind, and payload")
    raw_seq, raw_ts, raw_kind, raw_payload = row
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
        raise ValueError("event sequence must be an integer")
    seq = raw_seq
    reasons: list[CorruptEventReason] = []

    if isinstance(raw_ts, bool) or not isinstance(raw_ts, int | float):
        ts = 0.0
        reasons.append(CorruptEventReason.INVALID_TIMESTAMP)
    else:
        ts = float(raw_ts)
        if not math.isfinite(ts):
            ts = 0.0
            reasons.append(CorruptEventReason.INVALID_TIMESTAMP)

    original_kind = _safe_original_kind(raw_kind)
    if original_kind is None:
        reasons.append(CorruptEventReason.INVALID_KIND)
    elif original_kind == CORRUPT_EVENT_KIND:
        reasons.append(CorruptEventReason.RESERVED_KIND)

    text: str | None
    if isinstance(raw_payload, str):
        text = raw_payload
    elif isinstance(raw_payload, bytes):
        try:
            text = raw_payload.decode("utf-8")
        except UnicodeDecodeError:
            text = None
            reasons.append(CorruptEventReason.INVALID_UTF8)
    else:
        text = None
        reasons.append(CorruptEventReason.PAYLOAD_NOT_TEXT)

    payload: dict[str, Any] | None = None
    if text is not None:
        try:
            parsed = json.loads(text)
        except (ValueError, RecursionError):
            reasons.append(CorruptEventReason.INVALID_JSON)
        else:
            if isinstance(parsed, dict):
                payload = parsed
            else:
                reasons.append(CorruptEventReason.PAYLOAD_NOT_OBJECT)

    if reasons:
        corruption = CorruptEventRow(
            seq=seq,
            original_kind=original_kind,
            reasons=tuple(reasons),
            payload_sha256=_payload_digest(raw_payload),
        )
        return DecodedEventRow(
            seq=seq,
            ts=ts,
            kind=CORRUPT_EVENT_KIND,
            payload=corruption.as_payload(),
            corruption=corruption,
        )
    return DecodedEventRow(
        seq=seq,
        ts=ts,
        kind=cast(str, original_kind),
        payload=cast(dict[str, Any], payload),
        corruption=None,
    )
