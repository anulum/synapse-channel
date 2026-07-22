# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — versioned multiplexed dashboard live transport
"""Frame and cursor contracts for the cockpit's multiplexed NDJSON stream."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final
from urllib.parse import parse_qs

from synapse_channel.dashboard_feed_serving import FeedResponse

LIVE_TRANSPORT_PATH: Final = "/live.ndjson"
"""Authenticated same-origin endpoint for the versioned cockpit stream."""

LIVE_TRANSPORT_VERSION: Final = 1
"""Current frame-envelope version."""

LIVE_TRANSPORT_CONTENT_TYPE: Final = "application/x-ndjson"
"""Media type used for one-JSON-object-per-line delivery."""

LIVE_TRANSPORT_CHANNELS: Final = ("snapshot", "events", "receipts", "operator_actions")
"""High-frequency feeds carried by the first transport version."""

MAX_DIAGNOSTIC_CYCLES: Final = 8
"""Upper bound for the optional finite-stream diagnostic query."""


@dataclass
class LiveTransportCursor:
    """Monotonic durable-feed cursors retained for one stream connection."""

    events: int | None = None
    receipts: int = 0
    operator_actions: int = 0

    def query(self, channel: str) -> str:
        """Return the bounded query for ``channel`` and the current cursor."""
        if channel == "events":
            if self.events is None:
                return "since=latest&limit=250&history=1"
            return f"since={self.events}&limit=1000"
        if channel == "receipts":
            return f"since={self.receipts}&limit=100"
        if channel == "operator_actions":
            return f"since={self.operator_actions}&limit=100"
        raise ValueError(f"unknown live transport channel: {channel}")

    def advance(self, channel: str, document: dict[str, Any]) -> None:
        """Advance one cursor from a successfully decoded feed document."""
        raw = document.get("next_cursor")
        if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
            return
        if channel == "events":
            current = -1 if self.events is None else self.events
            self.events = max(current, raw)
        elif channel == "receipts":
            self.receipts = max(self.receipts, raw)
        elif channel == "operator_actions":
            self.operator_actions = max(self.operator_actions, raw)


@dataclass
class LiveFrameSequence:
    """Encode strictly increasing frames for one connection id."""

    connection_id: str
    sequence: int = 0

    def encode(
        self,
        kind: str,
        *,
        sent_at: int,
        channel: str | None = None,
        status: str | None = None,
        data: object | None = None,
        detail: str | None = None,
    ) -> bytes:
        """Return one canonical UTF-8 NDJSON frame and advance the sequence."""
        self.sequence += 1
        frame: dict[str, object] = {
            "version": LIVE_TRANSPORT_VERSION,
            "connection_id": self.connection_id,
            "sequence": self.sequence,
            "kind": kind,
            "sent_at": sent_at,
        }
        if channel is not None:
            frame["channel"] = channel
        if status is not None:
            frame["status"] = status
        if data is not None:
            frame["data"] = data
        if detail is not None:
            frame["detail"] = detail
        return (
            json.dumps(frame, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")


def decode_feed_response(response: FeedResponse) -> tuple[str, object | None, str | None]:
    """Map one existing feed response into an honest stream-channel state."""
    if response.status is HTTPStatus.NOT_FOUND:
        return "absent", None, response.body.decode("utf-8", errors="replace").strip()
    if response.status is not HTTPStatus.OK:
        return "error", None, response.body.decode("utf-8", errors="replace").strip()
    try:
        document = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "error", None, "feed returned invalid JSON"
    if not isinstance(document, dict):
        return "error", None, "feed returned a non-object JSON document"
    return "live", document, None


def snapshot_fingerprint(document: dict[str, Any]) -> str:
    """Hash snapshot evidence while excluding its presentation-only generation time."""
    stable = dict(document)
    fleet = stable.get("fleet")
    if isinstance(fleet, dict):
        stable["fleet"] = {key: value for key, value in fleet.items() if key != "generated_at"}
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_diagnostic_cycles(query: str) -> int | None:
    """Parse an optional bounded ``cycles`` query used by diagnostics and tests.

    An absent query means an open-ended production stream. Unknown, duplicated,
    empty, non-decimal, zero, or over-large values fail closed with ``ValueError``.
    """
    if not query:
        return None
    parsed = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    if set(parsed) != {"cycles"} or len(parsed["cycles"]) != 1:
        raise ValueError("live stream accepts only one cycles parameter")
    raw = parsed["cycles"][0]
    if not raw.isdecimal():
        raise ValueError("live stream cycles must be a positive decimal integer")
    cycles = int(raw)
    if cycles < 1 or cycles > MAX_DIAGNOSTIC_CYCLES:
        raise ValueError(f"live stream cycles must be within 1..{MAX_DIAGNOSTIC_CYCLES}")
    return cycles
