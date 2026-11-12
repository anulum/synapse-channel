# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — content-free stream activity metadata
"""Identify directed stream progress without disclosing stream IDs or text."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from synapse_channel.core.streaming import ABORT, DONE, parse_stream_frame

MAX_ACTIVITY_INTEGER = 9223372036854775807


@dataclass(frozen=True)
class StreamActivity:
    """Bind a bounded stream fingerprint and native timestamp to its journal row."""

    seq: int
    key: str
    terminal: bool
    at: int

    def __post_init__(self) -> None:
        """Reject coercion, unbounded fingerprints and invalid native positions."""
        if (
            type(self.seq) is not int
            or not 1 <= self.seq <= MAX_ACTIVITY_INTEGER
            or type(self.key) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.key) is None
            or type(self.terminal) is not bool
            or type(self.at) is not int
            or not 0 <= self.at <= MAX_ACTIVITY_INTEGER
        ):
            raise ValueError("invalid stream activity")

    def encode(self) -> dict[str, object]:
        """Return metadata without a raw stream identifier, sender or body."""
        return {"key": self.key, "terminal": self.terminal, "at": self.at}

    @classmethod
    def from_value(cls, seq: int, value: object) -> StreamActivity:
        """Decode the exact metadata shape before accepting native progress."""
        if not isinstance(value, dict) or set(value) != {"key", "terminal", "at"}:
            raise ValueError("invalid stream activity")
        return cls(seq, value["key"], value["terminal"], value["at"])


def stream_activity(
    payload: Mapping[str, object], identity: str, *, seq: int, ts: float
) -> StreamActivity | None:
    """Project only an external exact-directed well-formed stream frame.

    Parameters
    ----------
    payload : Mapping[str, object]
        Durable native CHAT envelope, never an inferred stream continuation.
    identity : str
        Admitted recipient; broadcasts, channels and self messages are ignored.
    seq : int
        Native journal sequence, independent of the stream frame counter.
    ts : float
        Native journal timestamp, independent of publisher-supplied timestamps.

    Returns
    -------
    StreamActivity or None
        Fingerprint and terminal/progress metadata for an eligible stream.
    """
    sender = payload.get("sender")
    if (
        payload.get("target") != identity
        or not isinstance(sender, str)
        or not sender
        or sender == identity
        or payload.get("channel")
        or not isinstance(payload.get("stream_id"), str)
    ):
        return None
    frame = parse_stream_frame(dict(payload))
    if frame is None:
        return None
    if not math.isfinite(ts) or not 0 <= ts < MAX_ACTIVITY_INTEGER:
        raise ValueError("invalid stream journal timestamp")
    key = hashlib.sha256(json.dumps([sender, frame.stream_id]).encode()).hexdigest()
    return StreamActivity(seq, key, frame.frame_type in (DONE, ABORT), int(ts))
