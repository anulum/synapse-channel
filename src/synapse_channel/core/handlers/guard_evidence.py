# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — authenticated ingestion for digest-only guard denials
"""Admit bounded claim-guard denial evidence only from authenticated sockets."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from synapse_channel.core.durable_ingress import chat_frame_bytes
from synapse_channel.core.guard_evidence import GuardEvidenceError, parse_guard_denial
from synapse_channel.core.journal import record_guard_denial
from synapse_channel.core.protocol import MessageType

if TYPE_CHECKING:
    from synapse_channel.core.hub import SynapseHub


async def handle_guard_denial(
    hub: SynapseHub, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Journal one authenticated guard refusal and return its durable sequence."""
    if hub.authenticator is None or hub.journal is None:
        await hub._send_json(
            websocket,
            hub._system(
                "guard denial evidence requires an authenticated durable hub",
                msg_type=MessageType.ERROR,
                target=sender,
                error_code="guard_evidence_unavailable",
            ),
        )
        return
    try:
        evidence = parse_guard_denial(data)
    except GuardEvidenceError as exc:
        await hub._send_json(
            websocket,
            hub._system(
                str(exc),
                msg_type=MessageType.ERROR,
                target=sender,
                error_code=exc.code,
            ),
        )
        return

    principal = hub.clients.quota_principal(websocket, fallback_agent=sender)
    if not principal.startswith("auth-token:"):
        await hub._send_json(
            websocket,
            hub._system(
                "guard denial evidence requires authenticated credential provenance",
                msg_type=MessageType.ERROR,
                target=sender,
                error_code="guard_evidence_unauthenticated",
            ),
        )
        return
    quota_reason = hub.guard_evidence_quota.allow(
        principal,
        nbytes=chat_frame_bytes(data),
    )
    if quota_reason:
        await hub._send_json(
            websocket,
            hub._system(
                "guard denial evidence ingress limit exceeded",
                msg_type=MessageType.ERROR,
                target=sender,
                error_code="guard_evidence_rate_limited",
                limit_reason=quota_reason,
            ),
        )
        return

    evidence["credential_principal_sha256"] = hashlib.sha256(principal.encode("utf-8")).hexdigest()
    evidence["recorder_sha256"] = hashlib.sha256(sender.encode("utf-8")).hexdigest()
    seq = record_guard_denial(hub.journal, evidence)
    recorded = hub._system(
        "Guard denial evidence recorded.",
        msg_type=MessageType.GUARD_DENIAL_RECORDED,
        target=sender,
        audit_seq=seq,
        call_sha256=evidence["call_sha256"],
        reason_code=evidence["reason_code"],
    )
    hub._remember(data, recorded)
    await hub._send_json(websocket, recorded)
