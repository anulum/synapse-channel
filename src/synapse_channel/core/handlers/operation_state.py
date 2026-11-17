# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — answer which attempts an operation has and what state each is in
"""Serve ``operation_state``: one operation's attempts and their states, read only.

The answer is given only to an identity that holds ``operation-evidence`` or
``operation-seal`` on the task the request names; the handler checks that
itself, also on a hub that requires no ACL, and a hub without a policy grants
it to nobody. Anyone else is answered ``not_authorised`` and learns nothing
about the attempts. An operation that is not declared on the named task is
answered ``unknown_operation``, so the answer never shows another task's
operations.

The answer lists each attempt's id, its executor and its state (``open``,
``sealed``, ``ended``, ``reconciled`` or ``closed``) and whether the operation
is closed, with the declared ``effect``, its ``effect_target`` and the
``intent_sha256`` the hub holds, which a stop's seal needs. ``stopped_by``
names the narrowest stopped scope that contains the operation, with the journal
sequence of its stop, its computed state, profile, blockers and counts, or is
``null``. ``level`` is the operation's autonomy level, the lowest of its
scopes, with the scope and sequence of its level line. Nothing
is journalled and nothing changes. A command that seals an
attempt asks first, and refuses to seal one that is not open; a command that
removes an applied marker requires the operation closed. A hub that does not
know this request answers with an error, which a command reads as a hub too old
for the seal.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from synapse_channel.core.acl import OPERATION_EVIDENCE, OPERATION_SEAL, task_grant
from synapse_channel.core.operation_records import (
    REFUSED_INVALID,
    REFUSED_NOT_AUTHORISED,
    REFUSED_UNKNOWN,
    STATUS_ACCEPTED,
    STATUS_REFUSED,
)
from synapse_channel.core.operation_stops import RESUMED_FROM_STOP_REQUESTED
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verb_registry import VerbSpec

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.core.acl import AclPolicy
    from synapse_channel.core.operation_records import OperationLedger

    class OperationStateContext(Protocol):
        """Capabilities the state handler consumes."""

        @property
        def acl_policy(self) -> AclPolicy | None:
            """Return the loaded ACL policy, or ``None``."""
            ...

        @property
        def operation_records(self) -> OperationLedger:
            """Return the declared operations and their attempts."""
            ...

        async def send_json(self, websocket: Any, frame: dict[str, Any]) -> None:
            """Send one frame to one socket."""
            ...

        def system(self, payload: str, **fields: Any) -> dict[str, Any]:
            """Build one hub frame."""
            ...


_OPERATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
"""The operation id rule of operation records."""

_MAX_TASK = 256


def _request(data: dict[str, Any]) -> tuple[str, str] | None:
    """Return the task and the operation a request names, or ``None`` when malformed."""
    task_id, operation_id = data.get("task_id"), data.get("operation_id")
    if not isinstance(task_id, str) or not 0 < len(task_id) <= _MAX_TASK:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in task_id):
        return None
    if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
        return None
    return task_id, operation_id


def _stopped_by(
    ledger: OperationLedger, stopped_by: tuple[str, int] | None
) -> dict[str, Any] | None:
    """Return the narrowest standing stop with its computed state, profile and blockers."""
    if stopped_by is None:
        return None
    scope, seq = stopped_by
    computed = ledger.attempts.stop_state(seq)
    report = {} if computed is None else computed.to_payload()
    return {
        **report,
        "scope": scope,
        "seq": seq,
        "state": RESUMED_FROM_STOP_REQUESTED if computed is None else computed.state,
    }


def _level(ledger: OperationLedger, operation_id: str) -> dict[str, Any]:
    """Return the operation's level, the lowest of its scopes, and its level line."""
    level, line = ledger.attempts.level_facts(operation_id)
    return {
        "level": level,
        "line_scope": None if line is None else line[0],
        "line_seq": None if line is None else line[1],
    }


def _answer(
    hub: OperationStateContext, sender: str, task_id: str, operation_id: str
) -> dict[str, Any]:
    """Return the answer's fields for a well-formed request."""
    granted = any(
        task_grant(hub.acl_policy, sender, task_id, permission)
        for permission in (OPERATION_EVIDENCE, OPERATION_SEAL)
    )
    if not granted:
        detail = f"{sender} holds neither operation-evidence nor operation-seal for {task_id}"
        return {"status": STATUS_REFUSED, "reason": REFUSED_NOT_AUTHORISED, "detail": detail}
    ledger = hub.operation_records
    summary = ledger.attempts.summary(operation_id)
    if ledger.task_of(operation_id) != task_id or summary is None:
        detail = f"no operation {operation_id} is declared on {task_id}"
        return {"status": STATUS_REFUSED, "reason": REFUSED_UNKNOWN, "detail": detail}
    closed, attempts = summary
    stopped_by, _line = ledger.attempts.stop_facts(operation_id)
    fact = ledger.attempts.operation(operation_id)
    return {
        "status": STATUS_ACCEPTED,
        "reason": "",
        "detail": f"{len(attempts)} attempt(s)",
        "closed": closed,
        "effect": ledger.effect_of(operation_id) or "",
        "effect_target": ledger.target_of(operation_id),
        "intent_sha256": (None if fact is None else fact.intent_sha256) or "",
        "stopped_by": _stopped_by(ledger, stopped_by),
        "level": _level(ledger, operation_id),
        "attempts": [
            {"attempt_id": item.attempt_id, "executor": item.executor, "state": item.state}
            for item in attempts
        ],
    }


async def handle_operation_state(
    hub: OperationStateContext, sender: str, data: dict[str, Any], websocket: Any
) -> None:
    """Answer one ``operation_state`` request privately; journal nothing."""
    named = _request(data)
    if named is None:
        fields: dict[str, Any] = {
            "status": STATUS_REFUSED,
            "reason": REFUSED_INVALID,
            "detail": "an operation_state request names a task and an operation id",
        }
    else:
        fields = _answer(hub, sender, *named)
    detail = str(fields.pop("detail"))
    await hub.send_json(
        websocket,
        hub.system(
            detail,
            msg_type=MessageType.OPERATION_STATE_RESULT,
            target=sender,
            task_id=str(data.get("task_id") or ""),
            operation_id=str(data.get("operation_id") or ""),
            request_id=str(data.get("request_id") or ""),
            **fields,
        ),
    )


VERB_SPECS = (
    VerbSpec(
        request_types=(MessageType.OPERATION_STATE,),
        handler=handle_operation_state,
        reply_types=(MessageType.OPERATION_STATE_RESULT,),
        mutates=False,
        replay_protected=False,
        mutation_guarded=False,
        accesses=None,
        event_kinds=(),
        minimum_wire_version=7,
        commands=("operation",),
    ),
)
"""A read of one operation's attempts, authorised by its own handler."""
