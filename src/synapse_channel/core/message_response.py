# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic response validation for durable chat messages
"""Validate structured chat responses without changing transport ACK semantics."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import is_recipient

RESPONSE_TO_SEQ_FIELD = "response_to_seq"
RESPONSE_STATUS_FIELD = "response_status"
RESPONSE_EVIDENCE_SCOPE_FIELD = "response_evidence_scope"
SEMANTIC_RESPONSE_STATUSES = frozenset(
    {"acknowledged", "in_progress", "needs_input", "declined", "completed"}
)
"""Closed response states accepted on a structured chat."""
SEMANTIC_RESPONSE_EVIDENCE_SCOPES = frozenset({"recipient", "operator_commentary"})
"""Closed evidence scopes: participant evidence or attributed commentary."""


def validate_semantic_response(
    store: EventStore | None,
    data: dict[str, Any],
    responder: str,
    responder_roles: Iterable[str] = (),
) -> str | None:
    """Return a refusal reason for an invalid structured response, else ``None``.

    An ordinary chat carries neither response field and passes unchanged. A
    structured response must carry all three response fields, reference one exact
    durable chat, and target that chat's hub-attested sender. ``recipient``
    evidence is accepted only from an addressee of the referenced chat;
    ``operator_commentary`` remains attributable but is explicitly non-authoritative
    for recipient or task-ownership evidence. The responder is always the
    server-derived connection identity, never a client-supplied claim.
    """
    has_seq = RESPONSE_TO_SEQ_FIELD in data
    has_status = RESPONSE_STATUS_FIELD in data
    has_scope = RESPONSE_EVIDENCE_SCOPE_FIELD in data
    if not has_seq and not has_status and not has_scope:
        return None
    if not (has_seq and has_status and has_scope):
        return (
            "semantic response needs response_to_seq, response_status, and response_evidence_scope"
        )

    raw_seq = data.get(RESPONSE_TO_SEQ_FIELD)
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int) or raw_seq < 1:
        return "response_to_seq must be a positive integer"
    raw_status = data.get(RESPONSE_STATUS_FIELD)
    if not isinstance(raw_status, str) or raw_status not in SEMANTIC_RESPONSE_STATUSES:
        return "response_status is not recognised"
    raw_scope = data.get(RESPONSE_EVIDENCE_SCOPE_FIELD)
    if not isinstance(raw_scope, str) or raw_scope not in SEMANTIC_RESPONSE_EVIDENCE_SCOPES:
        return "response_evidence_scope is not recognised"
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
    original_target = str(referenced[0].payload.get("target") or "all").strip()
    if raw_scope == "recipient" and not is_recipient(
        original_target,
        responder,
        responder_roles,
    ):
        return "recipient semantic response requires an addressee of the referenced message"
    return None
