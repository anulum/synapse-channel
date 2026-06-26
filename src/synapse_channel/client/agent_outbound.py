# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound protocol helpers for the reusable client
"""Outbound protocol helpers for :class:`synapse_channel.client.agent.SynapseAgent`."""

from __future__ import annotations

import json
from typing import Any, Protocol

from websockets.asyncio.client import ClientConnection

from synapse_channel.core.protocol import MessageType, build_envelope


class _OutboundAgent(Protocol):
    """Attributes required to serialise and send outbound envelopes."""

    connection: ClientConnection | None
    name: str

    async def send_message(
        self,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Send one message envelope to the hub."""


class AgentOutboundMixin:
    """Send chat, task mutation, memory, ledger, wait, and capability envelopes."""

    async def send_message(
        self: _OutboundAgent,
        msg_type: str,
        *,
        target: str = "all",
        payload: str = "",
        **extra: Any,
    ) -> None:
        """Serialise and send one message envelope to the hub.

        Parameters
        ----------
        msg_type : str
            One of the :class:`~synapse_channel.core.protocol.MessageType` constants.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        payload : str, optional
            Free-form text body.
        **extra : Any
            Additional protocol fields merged into the envelope.
        """
        if self.connection is None:
            return
        msg = build_envelope(self.name, msg_type, target=target, payload=payload, **extra)
        await self.connection.send(json.dumps(msg))

    async def chat(
        self: _OutboundAgent,
        payload: str,
        *,
        target: str = "all",
        priority: bool = False,
        memory_tag: str = "",
    ) -> None:
        """Send a chat message to the room or a single agent.

        Parameters
        ----------
        payload : str
            Message text.
        target : str, optional
            Recipient agent name, or ``"all"``. Defaults to ``"all"``.
        priority : bool, optional
            Mark the message as priority so it wakes even directed-only waiters
            (use sparingly — for announcements that genuinely must reach everyone).
        memory_tag : str, optional
            An opaque tag marking the message memory-worthy (e.g. ``"remember"``).
            The hub carries it through the durable log and the broadcast without
            interpreting it, so a persistent-memory adapter can pick out actively
            authored context from the comms stream. Omitted from the envelope when
            blank.
        """
        extra: dict[str, Any] = {}
        if priority:
            extra["priority"] = True
        if memory_tag:
            extra["memory_tag"] = memory_tag
        await self.send_message(MessageType.CHAT, target=target, payload=payload, **extra)

    async def log_recall(
        self: _OutboundAgent,
        query_text: str,
        *,
        returned_claim_ids: tuple[str, ...] | list[str] = (),
        was_used: bool = False,
        abstained: bool = False,
    ) -> None:
        """Log one recall query-stream event to the hub.

        Records a lookup the agent (or a memory layer on its behalf) just made, so
        a downstream persistent-memory adapter can calibrate recall against the
        real query distribution. The hub stamps the producing identity and the
        time; only the query and its outcome travel from the client.

        Parameters
        ----------
        query_text : str
            The query that was asked.
        returned_claim_ids : tuple[str, ...] or list[str], optional
            Identifiers of the memories returned for the query.
        was_used : bool, optional
            Whether the returned answer was actually used.
        abstained : bool, optional
            Whether the memory layer abstained (returned no confident answer).
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

        Authors an assertion the agent wants remembered. The hub runs it through
        the emit gate, stamps its origin (the producing identity and time, which
        the client cannot forge), journals it, and broadcasts the verdict — so a
        claim stronger than its evidence comes back floored rather than refused.
        The provenance and validity envelopes are always sent so the gate sees
        them present; the hub fills the identity, the receive-time, and an unset
        ``valid_from``.

        Parameters
        ----------
        statement : str
            The assertion being remembered.
        subkind : str
            The episodic category — ``codebase-fact``, ``lesson``, ``decision``,
            ``dead-end``, or ``outcome``.
        evidence_kind : str or None, optional
            What backs the claim (e.g. ``measured``, ``producer-asserted``);
            required for a factual subkind.
        claim_status : str or None, optional
            The epistemic standing (e.g. ``reference-validated``,
            ``bounded-support``); required for a scientific subkind.
        freshness : str or None, optional
            How recently the reference was re-checked at source; derived from the
            re-check recency and reference signals when left unset.
        evidence_ref : str or None, optional
            A reference to the evidence (file:line, commit, command output).
        project : str, optional
            The project the finding belongs to; the hub falls back to the agent's
            project when blank.
        session : str, optional
            The producer's session identifier.
        source_event_seq : int or None, optional
            The hub-log sequence of the carrying message, when known.
        valid_from : float or None, optional
            When the fact starts holding; the hub anchors it to receive-time when
            left unset.
        valid_to : float or None, optional
            When the fact stops holding, or ``None`` for an open window.
        lifecycle : str or None, optional
            ``active`` (default), ``superseded``, or ``retracted``.
        supersedes : str or None, optional
            Identifier of the atom this one replaces.
        checked_this_session : bool, optional
            Whether the reference was re-verified at source this session.
        source_ref : str, optional
            The reference that was re-checked.
        producer_confidence : float or None, optional
            Advisory producer confidence; never gates recall.
        execution_substrate : str or None, optional
            Where the result was produced, when relevant.
        entities : tuple[str, ...] or list[str], optional
            Named entities the finding concerns.
        tags : tuple[str, ...] or list[str], optional
            Free-form tags for read-side hierarchy.
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

    async def claim(
        self: _OutboundAgent,
        task_id: str,
        note: str = "",
        ttl_seconds: float | None = None,
        *,
        worktree: str = "",
        paths: tuple[str, ...] | list[str] = (),
        idem_key: str | None = None,
        git: dict[str, Any] | None = None,
    ) -> None:
        """Request a scoped lease on a task.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        note : str, optional
            Human-readable context stored with the claim.
        ttl_seconds : float or None, optional
            Requested lease duration; ``None`` lets the hub apply its default.
        worktree : str, optional
            Worktree label; claims in different worktrees never contend for files.
        paths : tuple[str, ...] or list[str], optional
            Declared file/directory paths the claim intends to touch; empty claims
            the whole worktree.
        idem_key : str or None, optional
            Idempotency key; reuse the same key when retrying after a reconnect so
            the hub replays the original result instead of claiming twice.
        git : dict[str, Any] or None, optional
            Branch context (``branch``/``base``/``auto_release_on``) for a
            git-scoped claim, as built client-side by
            :mod:`synapse_channel.git.gitclaim`. The hub stores and displays it but
            never acts on it.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "note": note}
        if ttl_seconds is not None:
            extra["ttl_seconds"] = float(ttl_seconds)
        if worktree:
            extra["worktree"] = worktree
        if paths:
            extra["paths"] = list(paths)
        if idem_key:
            extra["idem_key"] = idem_key
        if git:
            extra["git"] = git
        await self.send_message(
            MessageType.CLAIM, target="System", payload=task_id.strip(), **extra
        )

    async def release(
        self: _OutboundAgent,
        task_id: str,
        *,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Release a task lease.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        epoch : int or None, optional
            Expected lease generation; when given, the hub refuses the release if
            the lease has since been superseded.
        idem_key : str or None, optional
            Idempotency key; reuse the same key when retrying after a reconnect so
            the hub replays the original result instead of releasing twice.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.RELEASE, target="System", payload=task_id.strip(), **extra
        )

    async def update_task(
        self: _OutboundAgent,
        task_id: str,
        *,
        status: str | None = None,
        note: str | None = None,
        data_ref: str | None = None,
        epoch: int | None = None,
        expected_version: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Update an owned task's status, note, or artefact reference.

        Parameters
        ----------
        task_id : str
            Task identifier; surrounding whitespace is stripped.
        status : str or None, optional
            New lifecycle status (see :mod:`synapse_channel.core.lifecycle`); the hub
            rejects an illegal transition.
        note : str or None, optional
            Replacement note.
        data_ref : str or None, optional
            Replacement artefact reference.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        expected_version : int or None, optional
            Expected field version for compare-and-swap; a mismatch is refused.
        idem_key : str or None, optional
            Idempotency key for safe retries after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if note is not None:
            extra["note"] = note
        if data_ref is not None:
            extra["data_ref"] = data_ref
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if expected_version is not None:
            extra["expected_version"] = int(expected_version)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.TASK_UPDATE, target="System", payload=task_id.strip(), **extra
        )

    async def handoff(
        self: _OutboundAgent,
        task_id: str,
        to_agent: str,
        *,
        note: str | None = None,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Hand an owned task to another agent in one atomic step.

        Transfers ownership directly, with no release/re-claim window, carrying
        the task's scope, status, and artefact reference. The recipient must be
        online; the hub records the move on the shared blackboard.

        Parameters
        ----------
        task_id : str
            Identifier of the owned task; whitespace is stripped.
        to_agent : str
            The agent to receive the task; whitespace is stripped.
        note : str or None, optional
            Replacement note for the moved claim; the existing note is kept when
            ``None``.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        idem_key : str or None, optional
            Idempotency key for a safe retry after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "to_agent": to_agent.strip()}
        if note is not None:
            extra["note"] = note
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(
            MessageType.HANDOFF, target="System", payload=task_id.strip(), **extra
        )

    async def save_checkpoint(
        self: _OutboundAgent,
        task_id: str,
        checkpoint: str,
        *,
        epoch: int | None = None,
        idem_key: str | None = None,
    ) -> None:
        """Save a resume checkpoint on an owned task.

        The checkpoint is durable and survives lease expiry: if this agent's
        lease lapses, the next agent to claim the task inherits it (and receives
        it in the claim grant) instead of restarting.

        Parameters
        ----------
        task_id : str
            Identifier of the owned task; whitespace is stripped.
        checkpoint : str
            Opaque resume token to store.
        epoch : int or None, optional
            Expected lease generation; a stale epoch is refused.
        idem_key : str or None, optional
            Idempotency key for a safe retry after a reconnect.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "checkpoint": checkpoint}
        if epoch is not None:
            extra["epoch"] = int(epoch)
        if idem_key:
            extra["idem_key"] = idem_key
        await self.send_message(MessageType.CHECKPOINT, target="System", **extra)

    async def request_wait(self: _OutboundAgent, task_id: str) -> None:
        """Register an advisory wait for a task another agent holds.

        The hub refuses the wait if it would close a hold-and-wait deadlock cycle.
        The wait is advisory: retry the claim once the holder releases.

        Parameters
        ----------
        task_id : str
            Identifier of the held task to wait for; whitespace is stripped.
        """
        await self.send_message(
            MessageType.WAIT_REQUEST,
            target="System",
            payload=task_id.strip(),
            task_id=task_id.strip(),
        )

    async def post_task(
        self: _OutboundAgent,
        task_id: str,
        title: str,
        *,
        description: str = "",
        depends_on: tuple[str, ...] | list[str] = (),
        suggested_owner: str = "",
    ) -> None:
        """Declare or re-declare a task on the shared plan (an upsert).

        This is the planning surface, distinct from :meth:`claim` (the lease on
        doing the work). Re-posting the same id refines the declaration.

        Parameters
        ----------
        task_id : str
            Stable identifier, shared with any claim taken on the task.
        title : str
            Short human-readable name of the work.
        description : str, optional
            Longer description or acceptance notes.
        depends_on : tuple[str, ...] or list[str], optional
            Prerequisite task ids; the hub refuses dependencies that form a cycle.
        suggested_owner : str, optional
            Advisory proposed owner.
        """
        extra: dict[str, Any] = {"task_id": task_id.strip(), "title": title}
        if description:
            extra["description"] = description
        if depends_on:
            extra["depends_on"] = list(depends_on)
        if suggested_owner:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK, target="System", **extra)

    async def update_ledger_task(
        self: _OutboundAgent,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
    ) -> None:
        """Change a plan task's planning status or suggested owner.

        Parameters
        ----------
        task_id : str
            Identifier of the task to update.
        status : str or None, optional
            New planning status (``open``/``in_progress``/``blocked``/``done``/
            ``cancelled``); an unknown status is refused.
        suggested_owner : str or None, optional
            Replacement advisory owner (``""`` clears it).
        """
        extra: dict[str, Any] = {"task_id": task_id.strip()}
        if status is not None:
            extra["status"] = status
        if suggested_owner is not None:
            extra["suggested_owner"] = suggested_owner
        await self.send_message(MessageType.LEDGER_TASK_UPDATE, target="System", **extra)

    async def post_progress(
        self: _OutboundAgent, task_id: str, text: str, *, kind: str = "note"
    ) -> None:
        """Append a structured progress note to the progress ledger.

        Parameters
        ----------
        task_id : str
            Task the note concerns; ``""`` for a board-wide note.
        text : str
            Body of the note.
        kind : str, optional
            One of ``note``/``blocked``/``assessment``. Defaults to ``"note"``.
        """
        await self.send_message(
            MessageType.LEDGER_PROGRESS,
            target="System",
            payload=text,
            task_id=task_id.strip(),
            kind=kind,
        )

    async def advertise(
        self: _OutboundAgent,
        *,
        description: str = "",
        skills: tuple[str, ...] | list[str] = (),
        task_classes: tuple[str, ...] | list[str] = (),
        model: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Advertise this agent's capability card to the hub.

        The card describes what the agent can do — its skills and the task
        classes it can take — so other agents can discover it and a router can
        pick it by task class. Re-advertising refreshes the card.

        Parameters
        ----------
        description : str, optional
            Free-form summary of what the agent does.
        skills : tuple[str, ...] or list[str], optional
            Capability tags the agent claims.
        task_classes : tuple[str, ...] or list[str], optional
            Routing classes the agent can take.
        model : str, optional
            Backing model identifier.
        meta : dict[str, Any] or None, optional
            Descriptive metadata.
        """
        extra: dict[str, Any] = {}
        if description:
            extra["description"] = description
        if skills:
            extra["skills"] = list(skills)
        if task_classes:
            extra["task_classes"] = list(task_classes)
        if model:
            extra["model"] = model
        if meta:
            extra["meta"] = meta
        await self.send_message(MessageType.ADVERTISE, target="System", **extra)
