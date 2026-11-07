# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — serve a mailbox handover or close requested over the wire
"""Serve ``mailbox_handover``: re-address a name's pending mail, or close it.

The request names the mailbox (``from``), an optional successor (``to``), an
optional ``through_seq`` and a ``reason``. The hub:

1. refuses a malformed request, a hub without a journal, and an actor that is
   neither the name, its custodian nor an operator granted ``mailbox-handover``;
2. answers "already applied" for a ``(from, through_seq)`` it has journalled;
3. for a handover, routes each covered pending chat to the successor as a new chat
   from the actor that cites the original sequence, stopping at the first refusal;
4. journals one ``mailbox_handover`` event naming the chats it forwarded, then
   advances the old watermark with source ``handover`` or ``closed``;
5. answers the actor privately with a ``mailbox_handover_result``.

A chat is never deleted. A crash between a forward and the journal event leaves
the watermark where it was, so a repeated request forwards those chats again;
each forward carries ``handover_of``, the original sequence, so the successor
can drop a repeat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from synapse_channel.core.acl import MAILBOX_HANDOVER, MESSAGE, WOULD_ALLOW, Target, evaluate_access
from synapse_channel.core.handlers.messaging import route_chat
from synapse_channel.core.identity_namespace import project_of
from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_custodians import MailboxCustodians
from synapse_channel.core.mailbox_handover import (
    MAX_FORWARDS,
    REFUSED_FORWARD,
    REFUSED_INVALID,
    REFUSED_NO_JOURNAL,
    REFUSED_NOTHING_PENDING,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_REFUSED,
    HandoverRequest,
    MailboxHandoverError,
    authorise_handover,
    decode_handover_request,
    forward_frame,
    record_mailbox_handover,
)
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Protocol

    from synapse_channel.core.acl import Target as AclTarget
    from synapse_channel.core.handlers.messaging import MessagingContext
    from synapse_channel.core.mailbox_handover import HandoverLedger
    from synapse_channel.core.persistence import EventStore

    class MailboxHandoverContext(MessagingContext, Protocol):
        """Capabilities consumed by the mailbox handover handler."""

        @property
        def mailbox_custodians(self) -> Mapping[str, str] | None:
            """Return the validated custodian entries, or ``None`` when the hub has none."""
            ...

        @property
        def mailbox_handovers(self) -> HandoverLedger:
            """Return the idempotency keys of journalled handovers."""
            ...


@dataclass(frozen=True)
class _Verdict:
    """What the hub answers one request with."""

    status: str
    detail: str
    reason: str = ""
    through_seq: int | None = None
    forwarded: int = 0
    remaining: bool = False


def _refused(reason: str, detail: str, through_seq: int | None = None) -> _Verdict:
    return _Verdict(STATUS_REFUSED, detail, reason=reason, through_seq=through_seq)


async def handle_mailbox_handover(
    hub: MailboxHandoverContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Apply one authorised handover or close and answer the actor privately."""
    try:
        request = decode_handover_request(data)
    except MailboxHandoverError as exc:
        verdict = _refused(REFUSED_INVALID, str(exc))
    else:
        verdict = _admit(hub, sender, request) or await _apply(hub, sender, request, websocket)
    await _answer(hub, websocket, sender, data, verdict)


def _admit(hub: MailboxHandoverContext, sender: str, request: HandoverRequest) -> _Verdict | None:
    """Return the refusal or repeat answer for a request, or ``None`` to apply it."""
    store = hub.journal
    if store is None or not hub.mailbox_pending.available:
        return _refused(REFUSED_NO_JOURNAL, "this hub keeps no journal, so it has no mailboxes")
    entries = hub.mailbox_custodians
    custodian = MailboxCustodians(entries).custodian_for(request.source) if entries else None
    denial = authorise_handover(
        sender,
        request.source,
        custodian=custodian,
        operator_allowed=_operator_allowed(hub, sender, request.source),
    )
    if denial is not None:
        return _refused(denial, f"{sender} may not hand over or close {request.source}'s mailbox")
    through = _through(request, store.max_seq())
    if (request.source, through) in hub.mailbox_handovers:
        return _Verdict(
            STATUS_ALREADY_APPLIED, "this handover was already applied", through_seq=through
        )
    if through <= hub.mailbox_pending.watermark_for(request.source):
        detail = f"{request.source} has already accepted mail through {through}"
        return _refused(REFUSED_NOTHING_PENDING, detail, through)
    return None


def _through(request: HandoverRequest, max_seq: int) -> int:
    """Return the last sequence a request covers: its own bound, else the whole journal."""
    return request.through_seq if request.through_seq is not None else max_seq


async def _apply(
    hub: MailboxHandoverContext, sender: str, request: HandoverRequest, websocket: Any
) -> _Verdict:
    """Forward or close the covered mail, journal it, and advance the old watermark."""
    store = cast("EventStore", hub.journal)
    through = _through(request, store.max_seq())
    chats, covered = hub.mailbox_pending.pending_chats(
        request.source, through_seq=through, limit=MAX_FORWARDS
    )
    if not chats:
        detail = f"{request.source} has no pending mail through {through}"
        return _refused(REFUSED_NOTHING_PENDING, detail, through)
    forwarded = await _forward(hub, sender, request, chats, websocket)
    if request.successor is not None and len(forwarded) < len(chats):
        if not forwarded:
            detail = "the successor refused the first forwarded message"
            return _refused(REFUSED_FORWARD, detail, chats[0].seq)
        # A refused forward stops the run; the watermark covers only what was forwarded.
        covered = forwarded[-1]
    record_mailbox_handover(store, request, actor=sender, through_seq=covered, forwarded=forwarded)
    hub.mailbox_handovers.add(request.source, covered)
    hub.mailbox_pending.advance(request.source, covered, source=request.watermark_source)
    remaining = covered < through
    return _Verdict(
        STATUS_APPLIED,
        _applied_detail(request, len(forwarded), covered, remaining),
        through_seq=covered,
        forwarded=len(forwarded),
        remaining=remaining,
    )


async def _forward(
    hub: MailboxHandoverContext,
    actor: str,
    request: HandoverRequest,
    chats: tuple[Any, ...],
    websocket: Any,
) -> list[int]:
    """Route each chat to the successor; return the original sequences forwarded."""
    if request.successor is None:
        return []
    forwarded: list[int] = []
    for event in chats:
        frame = forward_frame(event, source=request.source, successor=request.successor)
        frame["sender"] = actor
        routing = await route_chat(
            hub, actor, frame, websocket, report_refusal=False, dedupe_retries=False
        )
        if routing.refusal:
            break
        forwarded.append(event.seq)
    return forwarded


def _operator_allowed(hub: MailboxHandoverContext, actor: str, source: str) -> bool:
    """Return whether the ACL policy grants ``actor`` ``mailbox-handover`` on ``source``."""
    if hub.acl_policy is None:
        return False
    decision = evaluate_access(
        subject=actor,
        project=project_of(actor),
        permission=MAILBOX_HANDOVER,
        target=Target("agent", source),
        policy=hub.acl_policy,
    )
    return decision.decision == WOULD_ALLOW


def _applied_detail(request: HandoverRequest, forwarded: int, covered: int, remaining: bool) -> str:
    """Return the one-line summary of an applied handover or close."""
    if request.successor is None:
        text = f"closed {request.source}'s mail through {covered}"
    else:
        text = f"handed {forwarded} message(s) from {request.source} to {request.successor}"
        text += f" through {covered}"
    return text + ("; more mail remains, repeat the request" if remaining else "")


async def _answer(
    hub: MailboxHandoverContext,
    websocket: Any,
    sender: str,
    data: dict[str, Any],
    verdict: _Verdict,
) -> None:
    """Send one private ``mailbox_handover_result`` to the actor."""
    successor = data.get("to")
    await hub.send_json(
        websocket,
        hub.system(
            verdict.detail,
            msg_type=MessageType.MAILBOX_HANDOVER_RESULT,
            target=sender,
            status=verdict.status,
            reason=verdict.reason,
            mailbox=str(data.get("from") or ""),
            successor=successor if isinstance(successor, str) else None,
            through_seq=verdict.through_seq,
            forwarded=verdict.forwarded,
            remaining=verdict.remaining,
        ),
    )


def handover_access(data: dict[str, Any]) -> list[tuple[str, AclTarget]]:
    """Require the actor to be allowed to message the mailbox and the successor.

    Who may act at all (the name, its custodian, a granted operator) is decided by
    the handler; this mapping keeps a loaded ACL policy's message rules in force.
    """
    accesses = [(MESSAGE, Target("agent", str(data.get("from") or "")))]
    successor = data.get("to")
    if isinstance(successor, str) and successor.strip():
        accesses.append((MESSAGE, Target("agent", successor.strip())))
    return accesses


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.MAILBOX_HANDOVER,),
        handler=handle_mailbox_handover,
        reply_types=(MessageType.MAILBOX_HANDOVER_RESULT, MessageType.CHAT),
        mutates=True,
        replay_protected=False,
        mutation_guarded=True,
        accesses=handover_access,
        event_kinds=(EventKind.MAILBOX_HANDOVER, EventKind.MAILBOX_WATERMARK, EventKind.CHAT),
        minimum_wire_version=1,
        commands=("mailbox",),
    ),
)
