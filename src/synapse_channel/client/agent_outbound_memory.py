# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound memory envelope helpers
"""Outbound recall and finding helpers for the reusable client."""

from __future__ import annotations

from typing import Any

from synapse_channel.client.agent_outbound_types import _OutboundAgent
from synapse_channel.core.protocol import MessageType

__all__ = ["AgentMemoryMixin"]


class AgentMemoryMixin:
    """Send memory telemetry and finding envelopes."""

    async def log_recall(
        self: _OutboundAgent,
        query_text: str,
        *,
        returned_claim_ids: tuple[str, ...] | list[str] = (),
        was_used: bool = False,
        abstained: bool = False,
    ) -> None:
        """Log one recall query-stream event to the hub.

        Parameters
        ----------
        query_text : str
            The query that was asked.
        returned_claim_ids : tuple[str, ...] or list[str], optional
            Identifiers of the memories returned for the query.
        was_used : bool, optional
            Whether the returned answer was actually used.
        abstained : bool, optional
            Whether the memory layer abstained.
        """
        await self.send_message(
            MessageType.RECALL_LOG,
            query_text=query_text,
            returned_claim_ids=list(returned_claim_ids),
            was_used=was_used,
            abstained=abstained,
        )

    async def record_finding(
        self: _OutboundAgent,
        statement: str,
        *,
        subkind: str,
        evidence_kind: str | None = None,
        claim_status: str | None = None,
        freshness: str | None = None,
        evidence_ref: str | None = None,
        project: str = "",
        session: str = "",
        source_event_seq: int | None = None,
        valid_from: float | None = None,
        valid_to: float | None = None,
        lifecycle: str | None = None,
        supersedes: str | None = None,
        checked_this_session: bool = False,
        source_ref: str = "",
        producer_confidence: float | None = None,
        execution_substrate: str | None = None,
        entities: tuple[str, ...] | list[str] = (),
        tags: tuple[str, ...] | list[str] = (),
    ) -> None:
        """Record one finding to the durable memory spine.

        Parameters
        ----------
        statement : str
            The assertion being remembered.
        subkind : str
            Episodic category for the authored memory atom.
        evidence_kind, claim_status, freshness, evidence_ref : str or None, optional
            Evidence and epistemic metadata carried to the hub emit gate.
        project, session, source_ref : str, optional
            Provenance and checked-source context.
        source_event_seq : int or None, optional
            Hub-log sequence of the carrying message, when known.
        valid_from, valid_to : float or None, optional
            Validity interval for the finding.
        lifecycle, supersedes : str or None, optional
            Lifecycle status and superseded finding id.
        checked_this_session : bool, optional
            Whether the source was re-checked this session.
        producer_confidence : float or None, optional
            Advisory producer confidence.
        execution_substrate : str or None, optional
            Runtime or host context for the result.
        entities, tags : tuple[str, ...] or list[str], optional
            Entity and tag metadata.
        """
        extra: dict[str, Any] = {
            "statement": statement,
            "subkind": subkind,
            "provenance": {
                "project": project,
                "session": session,
                "source_event_seq": source_event_seq,
            },
            "validity": {"valid_from": valid_from, "valid_to": valid_to},
            "verified_at_source": {
                "checked_this_session": checked_this_session,
                "source_ref": source_ref,
            },
        }
        if evidence_kind is not None:
            extra["evidence_kind"] = evidence_kind
        if claim_status is not None:
            extra["claim_status"] = claim_status
        if freshness is not None:
            extra["freshness"] = freshness
        if evidence_ref is not None:
            extra["evidence_ref"] = evidence_ref
        if lifecycle is not None:
            extra["lifecycle"] = lifecycle
        if supersedes is not None:
            extra["supersedes"] = supersedes
        if producer_confidence is not None:
            extra["producer_confidence"] = producer_confidence
        if execution_substrate is not None:
            extra["execution_substrate"] = execution_substrate
        if entities:
            extra["entities"] = list(entities)
        if tags:
            extra["tags"] = list(tags)
        await self.send_message(MessageType.FINDING, target="System", **extra)
