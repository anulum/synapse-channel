# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic response validation for durable chat messages
"""Validate structured chat responses without changing transport ACK semantics."""

from __future__ import annotations

from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore

RESPONSE_TO_SEQ_FIELD = "response_to_seq"
RESPONSE_STATUS_FIELD = "response_status"
SEMANTIC_RESPONSE_STATUSES = frozenset(
    {"acknowledged", "in_progress", "needs_input", "declined", "completed"}
)
"""Closed response states accepted on a structured chat."""


def validate_semantic_response(
    store: EventStore | None,
    data: dict[str, Any],
) -> str | None:
    """Return a refusal reason for an invalid structured response, else ``None``.

    An ordinary chat carries neither response field and passes unchanged. A
    structured response must carry both, reference one exact durable chat, and
    target that chat's hub-attested sender. The response actor remains the
    current connection identity; this validator never impersonates the original
    recipient or treats a transport acknowledgement as semantic evidence.
    """
    has_seq = RESPONSE_TO_SEQ_FIELD in data
    has_status = RESPONSE_STATUS_FIELD in data
    if not has_seq and not has_status:
        return None
    if has_seq != has_status:
        return "semantic response needs both response_to_seq and response_status"

    raw_seq = data.get(RESPONSE_TO_SEQ_FIELD)
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 1:
        return "response_to_seq must be a positive integer"
    raw_status = data.get(RESPONSE_STATUS_FIELD)
    if not isinstance(raw_status, str) or raw_status not in SEMANTIC_RESPONSE_STATUSES:
        return "response_status is not recognised"
    if store is None:
        return "semantic response requires the durable event store"

    referenced = store.read_window(
        min_seq=raw_seq,
        max_seq=raw_seq,
        kinds=(EventKind.CHAT,),
        limit=1,
    )
    if len(referenced) != 1 or referenced[0].seq != raw_seq:
        return "response_to_seq does not name a durable chat"
    original_sender = str(referenced[0].payload.get("sender") or "").strip()
    target = str(data.get("target") or "all").strip()
    if not original_sender or target != original_sender:
        return "semantic response target must match the referenced message sender"
    return None
