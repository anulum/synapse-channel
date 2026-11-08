# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hand a mailbox's pending mail to a successor, or close it with a reason
"""Request, authorisation and journal record of a mailbox handover or close.

Mail waits at a name until that name acknowledges it. When a session ends, its
name rarely comes back, so the mail waits for nobody. A handover re-addresses
the name's pending directed chats to a successor and advances the old
watermark; a close advances the watermark with a reason and delivers nothing.
Either way a journalled ``mailbox_handover`` event records who did it, why, and
which chats it covered; no chat is deleted.

Who may act: the name itself, its custodian from the custodian map
(:mod:`synapse_channel.core.mailbox_custodians`), or an operator that the ACL
policy grants the ``mailbox_handover`` permission on the name. The mailbox may
be a role's own mailbox ``role:<project>/<role>``; its custodian is looked up
under the role's project. The successor is a local name or a role, never a
role's mailbox key: forwards to a role are role mail. A request covers the mail
up to ``through_seq``; one request forwards at most :data:`MAX_FORWARDS` chats,
and the result names the last sequence it covered so the caller can repeat.
``(from, through_seq)`` is the idempotency key: a repeated request is answered
"already applied" and does nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent
from synapse_channel.core.role_mailbox import is_role_mailbox_key

MAX_FORWARDS: Final = 200
"""Chats one request re-addresses; the caller repeats for the rest."""

MAX_REASON_LENGTH: Final = 500
"""Characters a handover or close reason may carry."""

ACTION_HANDOVER: Final = "handover"
ACTION_CLOSE: Final = "close"

WATERMARK_SOURCE_HANDOVER: Final = "handover"
WATERMARK_SOURCE_CLOSED: Final = "closed"

STATUS_APPLIED: Final = "applied"
STATUS_ALREADY_APPLIED: Final = "already_applied"
STATUS_REFUSED: Final = "refused"

REFUSED_INVALID: Final = "invalid_request"
REFUSED_NOT_AUTHORISED: Final = "not_authorised"
REFUSED_NO_JOURNAL: Final = "no_journal"
REFUSED_NOTHING_PENDING: Final = "nothing_pending"
REFUSED_FORWARD: Final = "forward_refused"

_GLOB_CHARACTERS = frozenset("*?[]")


class MailboxHandoverError(SynapseError, ValueError):
    """Raised when a handover request is malformed."""

    code = "mailbox_handover"


@dataclass(frozen=True)
class HandoverRequest:
    """One validated handover (with a successor) or close (without one)."""

    source: str
    successor: str | None
    through_seq: int | None
    reason: str

    @property
    def action(self) -> str:
        """Return ``handover`` when a successor is named, else ``close``."""
        return ACTION_CLOSE if self.successor is None else ACTION_HANDOVER

    @property
    def watermark_source(self) -> str:
        """Return the ``source`` the advanced watermark is journalled with."""
        return WATERMARK_SOURCE_CLOSED if self.successor is None else WATERMARK_SOURCE_HANDOVER


def _local_name(value: object, label: str) -> str:
    """Return a stripped local mailbox name, or raise :class:`MailboxHandoverError`."""
    if not isinstance(value, str) or not value.strip():
        raise MailboxHandoverError(f"{label} must be a non-empty name")
    name = value.strip()
    if "@" in name:
        raise MailboxHandoverError(f"{label} {name!r} names another hub; only local names")
    if "," in name or _GLOB_CHARACTERS.intersection(name) or name == "all":
        raise MailboxHandoverError(f"{label} {name!r} is not one mailbox")
    return name


def decode_handover_request(data: Mapping[str, Any]) -> HandoverRequest:
    """Validate the ``from``, ``to``, ``through_seq`` and ``reason`` fields of a request.

    Raises
    ------
    MailboxHandoverError
        When a field is missing, malformed, the successor is the source itself, or
        the successor is a role's mailbox key.
    """
    source = _local_name(data.get("from"), "from")
    raw_successor = data.get("to")
    successor = None if raw_successor is None else _local_name(raw_successor, "to")
    if successor is not None and is_role_mailbox_key(successor):
        raise MailboxHandoverError(
            f"to {successor!r} is a role's mailbox key; name the role itself as the successor"
        )
    if successor == source:
        raise MailboxHandoverError("to must differ from from")
    raw_through = data.get("through_seq")
    if raw_through is not None and (
        isinstance(raw_through, bool) or not isinstance(raw_through, int) or raw_through < 1
    ):
        raise MailboxHandoverError("through_seq must be a positive integer")
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise MailboxHandoverError("reason must be a non-empty string")
    if len(reason.strip()) > MAX_REASON_LENGTH:
        raise MailboxHandoverError(f"reason must be at most {MAX_REASON_LENGTH} characters")
    return HandoverRequest(source, successor, raw_through, reason.strip())


def authorise_handover(
    actor: str,
    source: str,
    *,
    custodian: str | None,
    operator_allowed: bool,
) -> str | None:
    """Return ``None`` when ``actor`` may act on ``source``'s mailbox, else the refusal.

    The name itself, its custodian, and an operator granted ``mailbox_handover`` may act.
    """
    if actor == source:
        return None
    if custodian is not None and actor == custodian:
        return None
    if operator_allowed:
        return None
    return REFUSED_NOT_AUTHORISED


class HandoverLedger:
    """The ``(from, through_seq)`` keys of journalled handovers, restored on start."""

    def __init__(self, store: EventStore | None) -> None:
        self._keys: set[tuple[str, int]] = set()
        if store is None:
            return
        for event in store.iter_events(kinds=(EventKind.MAILBOX_HANDOVER,)):
            key = _event_key(event)
            if key is not None:
                self._keys.add(key)

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` (a ``(from, through_seq)`` pair) was journalled."""
        return key in self._keys

    def add(self, source: str, through_seq: int) -> None:
        """Remember one applied handover or close."""
        self._keys.add((source, through_seq))


def _event_key(event: StoredEvent) -> tuple[str, int] | None:
    """Return the idempotency key of a stored handover event, or ``None`` if malformed."""
    source = event.payload.get("from")
    through = event.payload.get("through_seq")
    if not isinstance(source, str) or isinstance(through, bool) or not isinstance(through, int):
        return None
    return source, through


def record_mailbox_handover(
    store: EventStore,
    request: HandoverRequest,
    *,
    actor: str,
    through_seq: int,
    forwarded: Iterable[int],
) -> int:
    """Durably journal one applied handover or close and return its sequence."""
    payload: dict[str, Any] = {
        "action": request.action,
        "from": request.source,
        "to": request.successor,
        "through_seq": int(through_seq),
        "reason": request.reason,
        "actor": actor,
        "forwarded": [int(seq) for seq in forwarded],
    }
    return store.append(EventKind.MAILBOX_HANDOVER, payload, durable=True)


def forward_frame(event: StoredEvent, *, source: str, successor: str) -> dict[str, Any]:
    """Return the chat that re-addresses one pending chat to ``successor``.

    The successor sees the original sender, payload and time, and ``handover_of``
    cites the original sequence, which is the successor's dedupe key.
    """
    original = event.payload
    frame: dict[str, Any] = {
        "type": "chat",
        "target": successor,
        "payload": original.get("payload", ""),
        "handover_of": event.seq,
        "handover_from": source,
        "original_sender": str(original.get("sender") or ""),
        "original_target": str(original.get("target") or ""),
        "original_timestamp": original.get("timestamp"),
    }
    return frame
