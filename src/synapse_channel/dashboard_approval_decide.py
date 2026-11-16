# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — approval decisions from the cockpit, checked before any relay
"""Serve ``POST /approval/decide``: record a person's approval decision from the cockpit.

The route records what ``synapse approvals decide`` records: one progress note of
kind ``approval`` on ``sha256:<digest>``, under the relay identity of the
authenticated principal (its ``operator_name`` in the access file). It never takes
an identity from the request.

Before anything is relayed it refuses, in this order:

1. a body that is not exactly ``operation_id``, ``intent_sha256``, ``decision`` and
   ``reason``, or whose fields are malformed (400, naming the field);
2. a start without ``--feeds-db``, or an operation with no declaration (404);
3. a digest that is not equal in the body, in the declaration's row and in this
   reader's recomputation from the kept intent (409);
4. a relay identity that is the intent's actor, or not of the actor's project
   (409): the hub would refuse such an approval when an attempt is asked;
5. a relay identity online as an agent (409), or a roster that cannot be read
   (503): relaying under it would speak for that agent.

The answer says "recorded" and nothing more. The hub judges an approval only when
an attempt is asked, so a recorded decision never says that an attempt may run.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from synapse_channel.core.approvals import STATE_APPROVED, STATE_REJECTED
from synapse_channel.core.operation_attempts import project_of_identity
from synapse_channel.core.operation_report import OperationReport, run_operation_report
from synapse_channel.dashboard_feed_serving import FeedResponse, plain_response
from synapse_channel.dashboard_operations_feed import MAX_OPERATION_ID_LENGTH
from synapse_channel.dashboard_operator_writes import RelayPlan

_LOGGER = logging.getLogger(__name__)

APPROVAL_DECIDE_ACTION: Final = "approval_decide"
"""The ``action`` every answer of the route names."""

REFUSED: Final = "refused"
"""Status of an answer the dashboard gave before relaying anything."""

MAX_APPROVAL_REASON_BYTES: Final = 4096
"""Largest UTF-8 size of a reason; the hub sets no progress-note bound of its own."""

DECISIONS: Final = (STATE_APPROVED, STATE_REJECTED)
"""The decisions a person can record from the cockpit."""

_FIELDS: Final = frozenset({"operation_id", "intent_sha256", "decision", "reason"})
_DIGEST: Final = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ApprovalDecision:
    """One parsed request: the operation, the digest it names, the decision and its words."""

    operation_id: str
    intent_sha256: str
    decision: str
    reason: str


@dataclass(frozen=True)
class ApprovalDecideBindings:
    """What one dashboard start lends the route.

    Parameters
    ----------
    feeds_db, key_file : pathlib.Path or None
        The event store the declaration is read from, and its key when encrypted.
    online_agents : callable
        Returns the hub's live agent names; raises when the roster cannot be read.
    relay : callable
        Runs one relay plan under the given relay identity and answers it.
    """

    feeds_db: Path | None
    key_file: Path | None
    online_agents: Callable[[], Sequence[str]]
    relay: Callable[[RelayPlan, str], FeedResponse]


def _text_field(body: Mapping[str, Any], name: str) -> str | None:
    value = body.get(name)
    return value if isinstance(value, str) else None


def parse_approval_decision(body: Mapping[str, Any]) -> ApprovalDecision | str:
    """Return the request's decision, or the 400 reason naming the field."""
    unknown = sorted(set(body) - _FIELDS)
    if unknown:
        return f"unknown field '{unknown[0]}': the relay identity comes from the access file"
    operation_id = _text_field(body, "operation_id")
    if (
        operation_id is None
        or not operation_id.strip()
        or len(operation_id) > MAX_OPERATION_ID_LENGTH
        or any(ord(character) < 32 for character in operation_id)
    ):
        return (
            "'operation_id' must be one identifier of 1 to "
            f"{MAX_OPERATION_ID_LENGTH} characters without control characters"
        )
    digest = _text_field(body, "intent_sha256")
    if digest is None or _DIGEST.fullmatch(digest) is None:
        return "'intent_sha256' must be 64 lowercase hexadecimal characters"
    decision = _text_field(body, "decision")
    if decision not in DECISIONS:
        return "'decision' must be 'approved' or 'rejected'"
    reason = _text_field(body, "reason")
    if reason is None:
        return "'reason' must be a string; it may be empty"
    if len(reason.encode("utf-8")) > MAX_APPROVAL_REASON_BYTES:
        return f"'reason' exceeds {MAX_APPROVAL_REASON_BYTES} UTF-8 bytes"
    return ApprovalDecision(operation_id, digest, decision, reason)


def approval_refusal(
    decision: ApprovalDecision, report: OperationReport, relay_identity: str
) -> tuple[HTTPStatus, str] | None:
    """Return why the store forbids relaying ``decision`` (status and words), or ``None``."""
    declaration = report.declaration
    if declaration is None:
        return HTTPStatus.NOT_FOUND, f"operation '{decision.operation_id}' has no declaration"
    if not (
        report.digest_check == "matches"
        and decision.intent_sha256 == report.intent_sha256 == report.recomputed_sha256
    ):
        return (
            HTTPStatus.CONFLICT,
            "the named digest, the declaration's digest and the digest recomputed "
            "from the kept intent are not all equal",
        )
    declarer = declaration.actor
    if relay_identity == declarer:
        return (
            HTTPStatus.CONFLICT,
            f"the relay identity '{relay_identity}' is the intent's actor; "
            "the hub never admits an attempt on its own approval",
        )
    project = project_of_identity(declarer)
    if project_of_identity(relay_identity) != project:
        return (
            HTTPStatus.CONFLICT,
            f"the relay identity '{relay_identity}' is not of the actor's project "
            f"'{project}'; the hub never admits an attempt on its approval",
        )
    return None


def _refused(status: HTTPStatus, decision: ApprovalDecision, detail: str) -> FeedResponse:
    """Answer a refusal given before relaying, in the route's own document."""
    document = {
        "action": APPROVAL_DECIDE_ACTION,
        "operation_id": decision.operation_id,
        "status": REFUSED,
        "detail": detail,
        "ok": False,
    }
    return FeedResponse(status, json.dumps(document).encode("utf-8"), "application/json")


def decide_approval(
    body: Mapping[str, Any], relay_identity: str, bindings: ApprovalDecideBindings
) -> FeedResponse:
    """Check one decision against the store and the roster, then relay it; or refuse.

    Parameters
    ----------
    body : Mapping
        The request's JSON object.
    relay_identity : str
        The authenticated principal's ``operator_name``; the note's author.
    bindings : ApprovalDecideBindings
        The start's store, roster and relay.

    Returns
    -------
    FeedResponse
        400 (plain text) for a malformed body; a ``refused`` document with 404, 409
        or 503 when a check fails; otherwise the relay's own answer.
    """
    decision = parse_approval_decision(body)
    if isinstance(decision, str):
        return plain_response(HTTPStatus.BAD_REQUEST, decision)
    store = bindings.feeds_db
    if store is None:
        detail = "approval decisions need --feeds-db: the declaration is read from the event store"
        return _refused(HTTPStatus.NOT_FOUND, decision, detail)
    try:
        report = run_operation_report(store, decision.operation_id, key_file=bindings.key_file)
    except Exception:
        _LOGGER.exception("Approval decision could not read the event store")
        detail = "the event store could not be read; nothing was relayed"
        return _refused(HTTPStatus.SERVICE_UNAVAILABLE, decision, detail)
    refusal = approval_refusal(decision, report, relay_identity)
    if refusal is not None:
        return _refused(refusal[0], decision, refusal[1])
    try:
        online = bindings.online_agents()
    except (RuntimeError, TimeoutError, OSError):
        detail = "the hub's roster could not be read; nothing was relayed"
        return _refused(HTTPStatus.SERVICE_UNAVAILABLE, decision, detail)
    if relay_identity in online:
        detail = (
            f"the relay identity '{relay_identity}' is online as an agent; "
            "relaying under it would speak for that agent"
        )
        return _refused(HTTPStatus.CONFLICT, decision, detail)
    plan = RelayPlan(
        APPROVAL_DECIDE_ACTION,
        {
            "operation_id": decision.operation_id,
            "intent_sha256": decision.intent_sha256,
            "decision": decision.decision,
        },
        lambda relay: relay.relay_approval_decision(
            decision.intent_sha256, decision.decision, decision.reason
        ),
    )
    return bindings.relay(plan, relay_identity)
