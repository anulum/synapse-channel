# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-principal bounds on accepted chat ingress volume
"""Bound accepted chat bytes and events per server-derived principal.

The in-memory chat history is already capped (:attr:`SynapseHub.max_history`) and
the optional token-bucket rate limiter charges by *message count* on the asserted
sender name. Neither stops one principal from filling the durable event log:
every accepted chat is journalled, and a 1 MiB frame is legal by default. This
module closes that gap.

Each principal keeps a sliding window of admissions. An admission costs one event
and the UTF-8 size of the complete normalized chat frame. When the window would exceed
``max_events`` or ``max_bytes``, the hub refuses the frame *before* history or
journal growth, with a machine-readable reason. Accepted chats still follow the
ordinary journal path unchanged — durability of admitted work is not weakened.

The key is the hub's server-derived quota principal (connect-token fingerprint or
open-host bucket), not the client-asserted sender name, so rotating a name cannot
multiply the budget. Buckets are not cleared on disconnect; only the sliding
window and a bounded principal map keep memory finite.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict, deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_EVENTS = 100
"""Default admitted chat events per principal per window."""

DEFAULT_MAX_BYTES = 1_048_576
"""Default admitted serialized chat-frame bytes per principal per window (1 MiB)."""

DEFAULT_WINDOW_SECONDS = 60.0
"""Default sliding window length in seconds."""

DEFAULT_MAX_PRINCIPALS = 4096
"""Bound on retained principal buckets (LRU eviction of idle ones)."""

REASON_EVENTS = "events"
REASON_BYTES = "bytes"
REASON_OVERSIZED = "oversized"
REASON_PRINCIPAL_CAPACITY = "principal-capacity"


@dataclass(frozen=True)
class _Admission:
    """One accepted chat inside a principal's sliding window."""

    at: float
    nbytes: int


@dataclass
class _Bucket:
    """Admissions and their running byte total for one principal."""

    admissions: deque[_Admission] = field(default_factory=deque)
    total_bytes: int = 0


def chat_frame_bytes(data: Mapping[str, Any]) -> int:
    """Return the UTF-8 size of a normalized chat frame for quota charging.

    The journal retains the complete frame, including optional and extension
    fields, so charging only ``payload`` would let an oversized sibling field
    bypass the durable-byte budget. Compact JSON mirrors the journal's durable
    representation closely enough while remaining independent of Python object
    overhead. The parser normally guarantees JSON-compatible values; the fallback
    keeps direct programmatic callers bounded as well.
    """
    try:
        return len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return len(str(dict(data)).encode("utf-8"))


class DurableIngressQuota:
    """Sliding-window event and byte quota keyed by server-derived principal.

    Parameters
    ----------
    max_events : int
        Maximum chat admissions per principal inside the window (clamped to ≥1).
    max_bytes : int
        Maximum admitted serialized chat-frame bytes per principal inside the
        window (clamped to ≥1).
    window_seconds : float
        Sliding window length in seconds (clamped to ≥0.001).
    max_principals : int
        Maximum retained principal buckets; the least-recently used idle bucket is
        dropped when the map would grow past this bound.
    """

    def __init__(
        self,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_principals: int = DEFAULT_MAX_PRINCIPALS,
    ) -> None:
        self.max_events = max(int(max_events), 1)
        self.max_bytes = max(int(max_bytes), 1)
        self.window_seconds = max(float(window_seconds), 0.001)
        self.max_principals = max(int(max_principals), 1)
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def allow(
        self,
        principal: str,
        *,
        nbytes: int,
        now: float | None = None,
    ) -> str:
        """Admit one chat for ``principal`` or return a refusal reason code.

        Parameters
        ----------
        principal : str
            Server-derived quota principal (not the free-form sender name).
        nbytes : int
            Payload byte cost of this chat (≥0).
        now : float or None, optional
            Override for the current time, in seconds.

        Returns
        -------
        str
            Empty string when admitted; otherwise one of :data:`REASON_EVENTS`,
            :data:`REASON_BYTES`, :data:`REASON_OVERSIZED`, or
            :data:`REASON_PRINCIPAL_CAPACITY`.
        """
        key = str(principal or "").strip() or "anonymous"
        cost = max(int(nbytes), 0)
        ts = time.monotonic() if now is None else float(now)
        if cost > self.max_bytes:
            return REASON_OVERSIZED
        bucket = self._bucket(key, ts)
        if bucket is None:
            return REASON_PRINCIPAL_CAPACITY
        self._prune(bucket, ts)
        if len(bucket.admissions) + 1 > self.max_events:
            return REASON_EVENTS
        if bucket.total_bytes + cost > self.max_bytes:
            return REASON_BYTES
        bucket.admissions.append(_Admission(at=ts, nbytes=cost))
        bucket.total_bytes += cost
        self._buckets.move_to_end(key)
        return ""

    def usage(self, principal: str, *, now: float | None = None) -> tuple[int, int]:
        """Return ``(events, bytes)`` currently counted for ``principal``."""
        key = str(principal or "").strip() or "anonymous"
        bucket = self._buckets.get(key)
        if bucket is None:
            return 0, 0
        ts = time.monotonic() if now is None else float(now)
        self._prune(bucket, ts)
        if not bucket.admissions:
            del self._buckets[key]
            return 0, 0
        return len(bucket.admissions), bucket.total_bytes

    def _bucket(self, key: str, now: float) -> _Bucket | None:
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._buckets.move_to_end(key)
            return bucket
        if len(self._buckets) >= self.max_principals:
            for candidate, candidate_bucket in tuple(self._buckets.items()):
                self._prune(candidate_bucket, now)
                if not candidate_bucket.admissions:
                    del self._buckets[candidate]
                    break
        if len(self._buckets) >= self.max_principals:
            return None
        bucket = _Bucket()
        self._buckets[key] = bucket
        return bucket

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket.admissions and bucket.admissions[0].at <= cutoff:
            bucket.total_bytes -= bucket.admissions.popleft().nbytes
