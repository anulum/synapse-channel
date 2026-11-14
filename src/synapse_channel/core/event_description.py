# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the hub's own words for every stored event
"""Who acted, and one terse line, for every kind of event the journal stores.

The dashboard's events feed adds these words to each event, so the cockpit's
signal log shows the hub's words for every kind instead of keeping its own
table. :data:`EVENT_WORDS` has one entry per :class:`EventKind`; a test holds the
two to the same set, so a new kind cannot be journalled without its words.

Every actor is the field the event's writer records (``core/journal.py`` and
the writers it is handed to), never a guess. A kind whose writer records no
actor has an empty one. The kinds the cockpit already worded keep its words.
Receipt-bearing kinds take the actor and the line the universal receipt
projection (:mod:`synapse_channel.core.universal_receipts`) already gives them.
The archive report keeps its own report wording.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, NamedTuple

from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.universal_receipts import universal_receipt_from_event

LINE_LIMIT = 160
"""How much of a free-text field (a chat, a note, a finding) a line keeps."""


class EventWords(NamedTuple):
    """The actor an event names, and its one-line summary."""

    actor: str
    summary: str


Describer = Callable[[Mapping[str, Any]], EventWords]


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _nested(payload: Mapping[str, Any], outer: str, key: str) -> str:
    inner = payload.get(outer)
    return _text(inner, key) if isinstance(inner, Mapping) else ""


def _trimmed(text: str) -> str:
    return f"{text[:LINE_LIMIT]}…" if len(text) > LINE_LIMIT else text


def _status(payload: Mapping[str, Any]) -> str:
    status = _text(payload, "status")
    return f" ({status})" if status else ""


def _count(payload: Mapping[str, Any], key: str, words: str) -> str:
    value = payload.get(key)
    return f" · {value} {words}" if isinstance(value, int) and not isinstance(value, bool) else ""


def _relay_status(payload: Mapping[str, Any]) -> str:
    """Return the relay status as the operator-actions feed reads it: recorded, else from flags."""
    recorded = _text(payload, "status")
    if recorded:
        return recorded
    if payload.get("applied") is True:
        return "applied"
    return "pending" if payload.get("pending") is True else "refused"


# The cockpit's words, moved here as they were (``eventProjection.ts``).


def _claim(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "owner"), f"claimed {_text(p, 'task_id')}")


def _task_update(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "owner"), f"updated {_text(p, 'task_id')}{_status(p)}")


def _finding(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_nested(p, "provenance", "actor"), _trimmed(_text(p, "statement")))


def _operator_relay(p: Mapping[str, Any]) -> EventWords:
    line = f"operator {_text(p, 'action')} {_text(p, 'task_id')} ({_relay_status(p)})"
    return EventWords(_text(p, "operator"), line)


def _release(p: Mapping[str, Any]) -> EventWords:
    # The release record holds only the task id: it names no actor.
    return EventWords("", f"released {_text(p, 'task_id')}")


def _ledger_progress(p: Mapping[str, Any]) -> EventWords:
    task, text = _text(p, "task_id"), _trimmed(_text(p, "text"))
    return EventWords(_text(p, "author"), f"{task}: {text}" if task else text)


def _ledger_task(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "created_by"), f"task {_text(p, 'task_id')}{_status(p)}")


def _chat(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "sender"), _trimmed(_text(p, "payload")))


def _dead_letter_escalation(p: Mapping[str, Any]) -> EventWords:
    target = _text(p, "target")
    return EventWords(
        target, f"dead-letter escalation: {target}{_count(p, 'count', 'undelivered')}"
    )


def _dead_letter_forwarding(p: Mapping[str, Any]) -> EventWords:
    target, direction = _text(p, "target"), _text(p, "direction")
    origin, owner = _text(p, "origin_hub_id"), _text(p, "owner_hub_id")
    hubs = f" · {origin} → {owner}" if origin and owner else ""
    heading = f"dead-letter forward ({direction})" if direction else "dead-letter forward"
    return EventWords(target, f"{heading}: {target}{_count(p, 'count', 'undelivered')}{hubs}")


# New words, in the same terse style, from each writer's own fields.


def _checkpoint(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "owner"), f"checkpoint {_text(p, 'task_id')}")


def _handoff(p: Mapping[str, Any]) -> EventWords:
    # The record is the recipient's claim; the prior owner is not a field of it.
    return EventWords("", f"handed {_text(p, 'task_id')} to {_text(p, 'owner')}")


def _resource(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "agent"), f"offered {_text(p, 'kind')} {_text(p, 'name')}")


def _recall(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "by"), f"recall: {_trimmed(_text(p, 'query_text'))}")


def _mailbox_watermark(p: Mapping[str, Any]) -> EventWords:
    through = p.get("through_seq")
    line = f"mailbox read through {through}" if isinstance(through, int) else "mailbox read"
    return EventWords(_text(p, "identity"), line)


def _mailbox_handover(p: Mapping[str, Any]) -> EventWords:
    line = f"mailbox {_text(p, 'action')}: {_text(p, 'from')} → {_text(p, 'to')}"
    return EventWords(_text(p, "actor"), line)


def _role_binding(p: Mapping[str, Any]) -> EventWords:
    roles = p.get("roles")
    names = ", ".join(str(role) for role in roles) if isinstance(roles, list) else ""
    return EventWords(_text(p, "identity"), f"roles: {names}" if names else "roles cleared")


def _operation(step: str) -> Describer:
    def describe(p: Mapping[str, Any]) -> EventWords:
        return EventWords(_text(p, "sender"), f"operation {_text(p, 'operation_id')} {step}")

    return describe


def _attempt_answered(p: Mapping[str, Any]) -> EventWords:
    line = f"attempt {_text(p, 'attempt_id')} answered: {_text(p, 'answer')}"
    return EventWords("", line)


def _entitlement_advert(p: Mapping[str, Any]) -> EventWords:
    return EventWords(_text(p, "advertiser"), "entitlement advert")


def _agent_line(text: Callable[[Mapping[str, Any]], str]) -> Describer:
    def describe(p: Mapping[str, Any]) -> EventWords:
        return EventWords(_text(p, "agent"), text(p))

    return describe


def _fixed(line: Callable[[Mapping[str, Any]], str]) -> Describer:
    def describe(p: Mapping[str, Any]) -> EventWords:
        return EventWords("", line(p))

    return describe


def _receipt(kind: str) -> Describer:
    """Words from the universal receipt projection, which already reads this kind's actor."""

    def describe(p: Mapping[str, Any]) -> EventWords:
        receipt = universal_receipt_from_event(StoredEvent(0, 0.0, kind, dict(p)))
        if receipt is None:
            return EventWords("", kind.replace("_", " "))
        return EventWords(receipt.actor, receipt.summary)

    return describe


def _set_line(noun: str, ids: str) -> Callable[[Mapping[str, Any]], str]:
    def line(p: Mapping[str, Any]) -> str:
        found = p.get(ids)
        return f"{noun} applied ({len(found) if isinstance(found, list) else 0})"

    return line


EVENT_WORDS: Mapping[str, Describer] = {
    EventKind.CLAIM: _claim,
    EventKind.TASK_UPDATE: _task_update,
    EventKind.FINDING: _finding,
    EventKind.OPERATOR_RELAY: _operator_relay,
    EventKind.RELEASE: _release,
    EventKind.LEDGER_PROGRESS: _ledger_progress,
    EventKind.LEDGER_TASK: _ledger_task,
    EventKind.CHAT: _chat,
    EventKind.DEAD_LETTER_ESCALATION: _dead_letter_escalation,
    EventKind.DEAD_LETTER_FORWARDING: _dead_letter_forwarding,
    EventKind.CHECKPOINT: _checkpoint,
    EventKind.HANDOFF: _handoff,
    EventKind.RESOURCE: _resource,
    EventKind.RECALL: _recall,
    EventKind.IDEMPOTENCY: _fixed(lambda _p: "idempotency record"),
    EventKind.NATIVE_MESSAGE: _fixed(lambda _p: "native message"),
    EventKind.CORRUPT: _fixed(lambda _p: "unreadable event"),
    EventKind.MAILBOX_WATERMARK: _mailbox_watermark,
    EventKind.MAILBOX_HANDOVER: _mailbox_handover,
    EventKind.ROLE_BINDING: _role_binding,
    EventKind.OPERATION_DECLARED: _operation("declared"),
    EventKind.OPERATION_OUTCOME: _operation("outcome"),
    EventKind.OPERATION_VERIFIED: _operation("verified"),
    EventKind.OPERATION_SEALED: _operation("sealed"),
    EventKind.OPERATION_ATTEMPT_ANSWERED: _attempt_answered,
    EventKind.ENTITLEMENT_ADVERT: _entitlement_advert,
    EventKind.DELIVERY_INTENT_ACCEPTED: _fixed(lambda _p: "delivery accepted"),
    EventKind.DELIVERY_INTENT_QUEUED: _fixed(lambda _p: "delivery queued"),
    EventKind.DELIVERY_INTENT_TRANSITION: _fixed(lambda p: f"delivery {_text(p, 'stage')}"),
    EventKind.DELIVERY_CANCEL_REQUESTED: _fixed(lambda _p: "delivery cancel requested"),
    EventKind.DELIVERY_RECEIVING_HUB_BOUND: _fixed(lambda _p: "delivery bound to its hub"),
    EventKind.CONNECT_TOKEN_ADMITTED: _agent_line(
        lambda p: f"admitted by token {_text(p, 'token_id')}"
    ),
    EventKind.CONNECT_TOKEN_REFUSED: _agent_line(
        lambda p: f"token refused: {_text(p, 'reason')}{_count(p, 'count', 'times')}"
    ),
    EventKind.CONNECT_TOKEN_SESSION_ENDED: _agent_line(
        lambda p: f"session ended: token {_text(p, 'reason')}"
    ),
    EventKind.CONNECT_TOKEN_SET_APPLIED: _fixed(_set_line("token set", "token_ids")),
    EventKind.CONNECT_TOKEN_SET_REJECTED: _fixed(
        lambda p: f"token set rejected: {_text(p, 'reason')}"
    ),
    EventKind.METRICS_TOKEN_SET_APPLIED: _fixed(_set_line("metrics token set", "token_ids")),
    EventKind.METRICS_TOKEN_SET_REJECTED: _fixed(
        lambda p: f"metrics token set rejected: {_text(p, 'reason')}"
    ),
    EventKind.METRICS_TOKEN_EXPIRED: _fixed(
        lambda p: f"metrics token {_text(p, 'token_id')} expired"
    ),
    EventKind.METRICS_TOKEN_RETIRING_ADMISSION: _fixed(
        lambda p: f"metrics admitted by retiring token {_text(p, 'token_id')}"
    ),
    EventKind.MESSAGE_AUTH_KEY_SET_APPLIED: _fixed(_set_line("message key set", "key_ids")),
    EventKind.MESSAGE_AUTH_KEY_SET_REJECTED: _fixed(
        lambda p: f"message key set rejected: {_text(p, 'reason')}"
    ),
    EventKind.MESSAGE_AUTH_KEY_EXPIRED: _fixed(
        lambda p: f"message key {_text(p, 'key_id')} expired"
    ),
    EventKind.MESSAGE_AUTH_KEY_RETIRING_ADMISSION: lambda p: EventWords(
        _text(p, "sender"), f"message signed with retiring key {_text(p, 'key_id')}"
    ),
    **{
        kind: _receipt(kind)
        for kind in (
            EventKind.CLAIM_DENIAL,
            EventKind.GUARD_DENIAL,
            EventKind.SANDBOX_RUN,
            EventKind.DELIVERY_RECEIPT_REQUESTED,
            EventKind.DELIVERY_RECEIPT_IMMEDIATE,
            EventKind.DELIVERY_RECEIPT_DEFERRED,
            EventKind.DELIVERY_RECEIPT_EXPIRED,
            EventKind.IDENTITY_PIN_RECLAIM,
            EventKind.IDENTITY_ENROLLMENT,
            EventKind.VERDICT_STALE,
            EventKind.VERDICT_RECORD_REFUSED,
            EventKind.VERDICT_GATE_BYPASS,
            EventKind.MULTIHUB_PARTITION,
            EventKind.MULTIHUB_HEAL,
            EventKind.MULTIHUB_EQUIVOCATION,
            EventKind.MULTIHUB_EQUIVOCATION_RECOVERY,
        )
    },
}
"""One describer per :class:`EventKind`, keyed by the stored kind."""


def describe_event(kind: str, payload: Mapping[str, Any]) -> EventWords | None:
    """Return the hub's words for one stored event, or ``None`` for a kind it does not know.

    Parameters
    ----------
    kind : str
        The stored event's kind.
    payload : Mapping[str, Any]
        The stored event's payload, as its writer recorded it.

    Returns
    -------
    EventWords or None
        The actor (empty when the writer records none) and a one-line summary;
        ``None`` for a kind outside :class:`EventKind`, such as one a newer hub
        wrote, which a reader then shows under its raw kind.
    """
    describer = EVENT_WORDS.get(kind)
    return None if describer is None else describer(payload)
