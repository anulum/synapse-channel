# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — monotonic operational counters the hub increments in-path
"""Monotonic counters for the hub's operational decisions.

The live gauges (:func:`~synapse_channel.core.metrics.collect_hub_metrics`)
answer *how things are*; these counters answer *what the hub has decided
since start* — claims granted and denied, releases, directed versus broadcast
chat, authentication failures, rate-limit rejections, federation denials, and
waiter takeovers with their quarantines. Each increment is a single integer
addition at the decision site, so the live message path pays nothing
measurable and a scrape reads plain attributes with no I/O.

Honest scope: counters reset with the process (Prometheus expects that of a
``counter``; ``rate()`` and ``increase()`` handle restarts), they are *not*
persisted to the journal, and they count decisions, not intents — a denied
claim that the client retries and wins counts once as denied and once as
granted, which is exactly what happened.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HubCounters:
    """Monotonic decision counters, incremented at the hub's decision sites.

    Attributes
    ----------
    claims_granted : int
        Claim requests answered ``CLAIM_GRANTED``, including grants a hub
        applied for a forwarded (multi-hub) claim.
    claims_denied : int
        Claim requests answered ``CLAIM_DENIED``.
    releases_granted : int
        Releases answered ``RELEASE_GRANTED``.
    chat_directed : int
        Chat frames addressed to a specific name, list, or glob.
    chat_broadcast : int
        Chat frames addressed to everyone (``all`` or blank target).
    auth_failures : int
        Frames refused by required per-message authentication.
    rate_limited : int
        Frames refused by the per-sender rate limiter.
    federation_denied : int
        Frames refused by the federation gate (peered key without a
        resolvable single peer, scope mismatch, or unverified signature).
    takeovers : int
        Waiter takeover requests accepted (an existing binding evicted).
    takeover_quarantines : int
        Takeover oscillations that entered quarantine (counted at entry).
    """

    claims_granted: int = 0
    claims_denied: int = 0
    releases_granted: int = 0
    chat_directed: int = 0
    chat_broadcast: int = 0
    auth_failures: int = 0
    rate_limited: int = 0
    federation_denied: int = 0
    takeovers: int = 0
    takeover_quarantines: int = 0
