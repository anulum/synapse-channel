# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — issue one setup plan bound to the command line's plan, and record it
"""Serve ``POST /setup/plan``: one setup plan, bound to the command line's plan, as a receipt.

The route inspects the served profile with the command line's own inspection,
plans it with the command line's own planner, and issues a dashboard plan bound
to that plan's digest
(:func:`~synapse_channel.dashboard_setup_contract.build_setup_plan`), with a
one-use confirmation nonce.

It admits the caller as every setup write does
(:func:`~synapse_channel.dashboard_setup_write_http.admit_setup_write`), writing
no receipt before the caller is known, and then answers 400 naming the refusal
code of a body that is not exactly the plan request, also unrecorded.

From here every answer is a receipt in the setup chain:

1. 429, ``rate_limited``, when the operator writes' rate limit is spent;
2. 403, ``denied`` with ``capability_denied``, without ``setup_plan``;
3. 503, ``refused`` with ``plan_issue_failed``, when the inspection or the
   command line's plan fails;
4. the plan store's refusals: 409 ``plan_request_reused``, 503
   ``plan_store_full``, 500 ``plan_issue_failed``;
5. 200 with the issued plan, recorded as ``planned``.

A receipt that cannot be written answers 503, and an issued plan whose receipt
could not be written is discarded, so it can never be applied. A refusal's body
names its reason and nothing else.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_setup_contract import (
    SetupContractRefusal,
    SetupPlanRequest,
    parse_setup_plan_request,
    setup_generation_digest,
)
from synapse_channel.dashboard_setup_http import Inspection, SetupServing
from synapse_channel.dashboard_setup_planner import IssuedSetupPlan, SetupPlanBinding
from synapse_channel.dashboard_setup_receipts import SetupReceiptOutcome, SetupReceiptReason
from synapse_channel.dashboard_setup_write_http import (
    CHAIN_UNAVAILABLE,
    SETUP_WRITE_HEADERS,
    ReceiptFacts,
    SetupWriter,
    SetupWriteRequest,
    admit_setup_write,
    effect_receipts,
    plain,
    receipt_builder,
    record_receipt,
)
from synapse_channel.setup_planner import build_setup_plan as build_cli_setup_plan

SETUP_PLAN_PATH: Final = "/setup/plan"
"""Issue one setup plan; a POST, because it stores a one-use record."""

_STORE_REFUSALS: Final[Mapping[str, tuple[HTTPStatus, SetupReceiptReason]]] = {
    "duplicate_request": (HTTPStatus.CONFLICT, "plan_request_reused"),
    "capacity": (HTTPStatus.SERVICE_UNAVAILABLE, "plan_store_full"),
    "invalid_token": (HTTPStatus.INTERNAL_SERVER_ERROR, "plan_issue_failed"),
    "token_collision": (HTTPStatus.INTERNAL_SERVER_ERROR, "plan_issue_failed"),
}
"""Each plan-store refusal of an issue: its answer and its receipt's reason."""


@dataclass(frozen=True)
class SetupPlanBindings:
    """What one dashboard start lends the route."""

    policy: DashboardAccessPolicy
    serving: SetupServing
    inspect: Inspection
    rate_limiter: WriteRateLimiter
    clock: Callable[[], float] = time.time


def setup_plan_response(
    request: SetupWriteRequest, bindings: SetupPlanBindings
) -> AccessHttpDecision:
    """Answer one ``POST /setup/plan`` in the module's order; record every known caller's answer."""
    writer = admit_setup_write(request, bindings.policy, bindings.serving, bindings.clock())
    if isinstance(writer, AccessHttpDecision):
        return writer
    parsed = parse_setup_plan_request(request.body)
    if isinstance(parsed, SetupContractRefusal):
        return plain(HTTPStatus.BAD_REQUEST, f"setup plan request refused: {parsed.code}")
    return _issue(writer, parsed, bindings)


def _issue(
    writer: SetupWriter, request: SetupPlanRequest, bindings: SetupPlanBindings
) -> AccessHttpDecision:
    """Answer an admitted request; each answer is recorded before it is given."""
    if not bindings.rate_limiter.allow():
        return _refuse(writer, request, HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "rate_limit")
    if not writer.principal.capabilities.setup_plan:
        return _refuse(writer, request, HTTPStatus.FORBIDDEN, "denied", "capability_denied")
    principal_id = writer.principal.principal_id
    try:
        cli_plan = build_cli_setup_plan(writer.profile, bindings.inspect(writer.profile))
        generation = setup_generation_digest(cli_plan)
        binding = SetupPlanBinding(principal_id, writer.host, writer.origin, generation)
        now = int(writer.now)
        issued = writer.plans.issue(request, binding=binding, cli_plan=cli_plan, now=now)
    except Exception:  # any inspection or planning failure is one bounded answer, never a trace
        status = HTTPStatus.SERVICE_UNAVAILABLE
        return _refuse(writer, request, status, "refused", "plan_issue_failed")
    if not isinstance(issued, IssuedSetupPlan):
        status, reason = _STORE_REFUSALS[issued.code]
        return _refuse(writer, request, status, "refused", reason, generation=generation)
    planned = ReceiptFacts(
        request.request_id,
        "setup_plan",
        "planned",
        "none",
        plan_id=issued.plan_id,
        plan_digest=issued.plan.digest,
        generation=issued.plan.configuration_generation,
        effects=effect_receipts(cli_plan),
    )
    if record_receipt(writer.receipts, receipt_builder(writer, planned)) is None:
        writer.plans.discard(issued.plan_id)
        return plain(HTTPStatus.SERVICE_UNAVAILABLE, CHAIN_UNAVAILABLE)
    body = json.dumps(issued.as_dict(), sort_keys=True).encode()
    return AccessHttpDecision(HTTPStatus.OK, body, writer.principal, headers=SETUP_WRITE_HEADERS)


def _refuse(
    writer: SetupWriter,
    request: SetupPlanRequest,
    status: HTTPStatus,
    outcome: SetupReceiptOutcome,
    reason: SetupReceiptReason,
    *,
    generation: str | None = None,
) -> AccessHttpDecision:
    """Record a refusal before any plan exists, then answer it naming its reason."""
    facts = ReceiptFacts(request.request_id, "setup_plan", outcome, reason, generation=generation)
    if record_receipt(writer.receipts, receipt_builder(writer, facts)) is None:
        return plain(HTTPStatus.SERVICE_UNAVAILABLE, CHAIN_UNAVAILABLE)
    return plain(status, f"setup plan refused: {reason}")
