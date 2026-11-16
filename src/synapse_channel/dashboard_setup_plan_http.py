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

It answers, in this order, and writes no receipt before the caller is known:

1. 404 naming the reason, when this start does not serve setup;
2. 403 without exactly one ``Origin`` that is this dashboard's own;
3. 401 without a recognised bearer; 415 without ``application/json``;
4. 400 naming the refusal code of a body that is not exactly the plan request.

From here every answer is a receipt in the setup chain:

5. 429, ``rate_limited``, when the operator writes' rate limit is spent;
6. 403, ``denied`` with ``capability_denied``, without ``setup_plan``;
7. 503, ``refused`` with ``plan_issue_failed``, when the inspection or the
   command line's plan fails;
8. the plan store's refusals: 409 ``plan_request_reused``, 503
   ``plan_store_full``, 500 ``plan_issue_failed``;
9. 200 with the issued plan, recorded as ``planned``.

A receipt that cannot be written answers 503, and an issued plan whose receipt
could not be written is discarded, so it can never be applied. A refusal's body
names its reason and nothing else.
"""

from __future__ import annotations

import io
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from typing import Final, cast

from synapse_channel import __version__
from synapse_channel.dashboard_access import DashboardAccessPolicy, DashboardPrincipal
from synapse_channel.dashboard_access_http import AccessHttpDecision, is_own_origin
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_operator_writes import is_json_media_type
from synapse_channel.dashboard_setup_contract import (
    MAX_SETUP_REQUEST_BYTES,
    SetupContractRefusal,
    SetupPlanRequest,
    parse_setup_plan_request,
    setup_generation_digest,
)
from synapse_channel.dashboard_setup_http import Inspection, SetupServing
from synapse_channel.dashboard_setup_planner import (
    IssuedSetupPlan,
    SetupPlanBinding,
    SetupPlanStore,
)
from synapse_channel.dashboard_setup_receipts import (
    SetupEffectReceipt,
    SetupInspectionVerdict,
    SetupReceiptDraft,
    SetupReceiptOutcome,
    SetupReceiptReason,
    SetupReceiptStore,
)
from synapse_channel.setup_contract import SETUP_SCHEMA_VERSION
from synapse_channel.setup_planner import build_setup_plan as build_cli_setup_plan
from synapse_channel.setup_profiles import SetupProfile

SETUP_PLAN_PATH: Final = "/setup/plan"
"""Issue one setup plan; a POST, because it stores a one-use record."""

_EFFECT_TARGETS: Final = {
    "establish_local_loopback_hub": "synapse-hub.service",
    "establish_identity_waiter": "synapse-arm@.service",
}
"""The unit each supported effect installs, as the executor names it; others name themselves."""

_BEFORE: Final[Mapping[str, SetupInspectionVerdict]] = {
    "pass": "satisfied",  # nosec B105 # an inspection status, not a secret
    "warn": "unsatisfied",
    "fail": "unsatisfied",
    "unavailable": "unverifiable",
}
"""The inspection's status of an effect's check, as the receipt's ``before`` verdict."""

_STORE_REFUSALS: Final[Mapping[str, tuple[HTTPStatus, SetupReceiptReason]]] = {
    "duplicate_request": (HTTPStatus.CONFLICT, "plan_request_reused"),
    "capacity": (HTTPStatus.SERVICE_UNAVAILABLE, "plan_store_full"),
    "invalid_token": (HTTPStatus.INTERNAL_SERVER_ERROR, "plan_issue_failed"),
    "token_collision": (HTTPStatus.INTERNAL_SERVER_ERROR, "plan_issue_failed"),
}
"""Each plan-store refusal of an issue: its answer and its receipt's reason."""

_HEADERS: Final = (("Vary", "Authorization"),)


@dataclass(frozen=True)
class SetupPlanHttpRequest:
    """What the HTTP handler read from one ``POST /setup/plan``.

    Attributes
    ----------
    authorization : str or None
        The ``Authorization`` header.
    host : str
        The request's single ``Host``, which the dashboard's host guard admitted.
    origins : tuple of str
        Every ``Origin`` header the request carried.
    content_type : str
        The ``Content-Type`` header.
    body : bytes
        The body, or empty when it was missing or over the contract's bound.
    """

    authorization: str | None
    host: str
    origins: tuple[str, ...]
    content_type: str
    body: bytes


def read_setup_plan_request(headers: Message, rfile: io.BufferedIOBase) -> SetupPlanHttpRequest:
    """Read one request's headers, and its body when it is within the contract's bound.

    An over-long or unreadable length leaves the body empty, and the strict parse
    then refuses it by size; nothing over the bound is read.
    """
    try:
        length = int(headers.get("Content-Length") or "0")
    except ValueError:
        length = 0
    body = rfile.read(length) if 0 < length <= MAX_SETUP_REQUEST_BYTES else b""
    return SetupPlanHttpRequest(
        authorization=headers.get("Authorization"),
        host=headers.get("Host", ""),
        origins=tuple(headers.get_all("Origin") or ()),
        content_type=headers.get("Content-Type", ""),
        body=body,
    )


@dataclass(frozen=True)
class SetupPlanBindings:
    """What one dashboard start lends the route."""

    policy: DashboardAccessPolicy
    serving: SetupServing
    inspect: Inspection
    rate_limiter: WriteRateLimiter
    clock: Callable[[], float] = time.time


@dataclass(frozen=True)
class _Admitted:
    """A known caller's plan request, on a start that serves setup."""

    request: SetupPlanRequest
    principal: DashboardPrincipal
    host: str
    origin: str
    profile: SetupProfile
    plans: SetupPlanStore
    receipts: Path
    now: float


def _plain(status: HTTPStatus, text: str, *, authenticate: bool = False) -> AccessHttpDecision:
    return AccessHttpDecision(status, text.encode(), authenticate=authenticate, headers=_HEADERS)


def setup_plan_response(
    request: SetupPlanHttpRequest, bindings: SetupPlanBindings
) -> AccessHttpDecision:
    """Answer one ``POST /setup/plan`` in the module's order; record every known caller's answer."""
    serving = bindings.serving
    profile, plans, receipts = serving.profile, serving.plans, serving.receipts
    if profile is None or plans is None or receipts is None:
        return _plain(HTTPStatus.NOT_FOUND, f"setup not served: {serving.decision.reason}")
    if not is_own_origin(request.host, request.origins):
        return _plain(HTTPStatus.FORBIDDEN, "setup writes require this dashboard's own Origin")
    principal = bindings.policy.resolve_credential(request.authorization)
    if principal is None:
        return _plain(HTTPStatus.UNAUTHORIZED, "bearer required", authenticate=True)
    if not is_json_media_type(request.content_type):
        return _plain(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "setup writes require application/json")
    parsed = parse_setup_plan_request(request.body)
    if isinstance(parsed, SetupContractRefusal):
        return _plain(HTTPStatus.BAD_REQUEST, f"setup plan request refused: {parsed.code}")
    expected = f"http://{request.host}".lower()
    admitted = _Admitted(
        parsed, principal, request.host, expected, profile, plans, receipts, bindings.clock()
    )
    return _issue(admitted, bindings)


def _issue(admitted: _Admitted, bindings: SetupPlanBindings) -> AccessHttpDecision:
    """Answer an admitted request; each answer is recorded before it is given."""
    if not bindings.rate_limiter.allow():
        return _refuse(admitted, HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "rate_limit")
    if not admitted.principal.capabilities.setup_plan:
        return _refuse(admitted, HTTPStatus.FORBIDDEN, "denied", "capability_denied")
    principal_id = admitted.principal.principal_id
    try:
        cli_plan = build_cli_setup_plan(admitted.profile, bindings.inspect(admitted.profile))
        generation = setup_generation_digest(cli_plan)
        binding = SetupPlanBinding(principal_id, admitted.host, admitted.origin, generation)
        now = int(admitted.now)
        issued = admitted.plans.issue(admitted.request, binding=binding, cli_plan=cli_plan, now=now)
    except Exception:  # any inspection or planning failure is one bounded answer, never a trace
        status = HTTPStatus.SERVICE_UNAVAILABLE
        return _refuse(admitted, status, "refused", "plan_issue_failed")
    if not isinstance(issued, IssuedSetupPlan):
        status, reason = _STORE_REFUSALS[issued.code]
        return _refuse(admitted, status, "refused", reason, generation=generation)
    planned = _draft(
        admitted,
        "planned",
        "none",
        plan=issued,
        effects=_effect_receipts(cli_plan),
    )
    if not _record(admitted.receipts, planned):
        admitted.plans.discard(issued.plan_id)
        return _plain(HTTPStatus.SERVICE_UNAVAILABLE, "setup receipt chain unavailable")
    body = json.dumps(issued.as_dict(), sort_keys=True).encode()
    return AccessHttpDecision(HTTPStatus.OK, body, admitted.principal, headers=_HEADERS)


def _refuse(
    admitted: _Admitted,
    status: HTTPStatus,
    outcome: SetupReceiptOutcome,
    reason: SetupReceiptReason,
    *,
    generation: str | None = None,
) -> AccessHttpDecision:
    """Record a refusal before any plan exists, then answer it naming its reason."""
    draft = _draft(admitted, outcome, reason, generation=generation)
    if not _record(admitted.receipts, draft):
        return _plain(HTTPStatus.SERVICE_UNAVAILABLE, "setup receipt chain unavailable")
    return _plain(status, f"setup plan refused: {reason}")


def _draft(
    admitted: _Admitted,
    outcome: SetupReceiptOutcome,
    reason: SetupReceiptReason,
    *,
    plan: IssuedSetupPlan | None = None,
    generation: str | None = None,
    effects: tuple[SetupEffectReceipt, ...] = (),
) -> Callable[[], SetupReceiptDraft]:
    """Return the receipt's builder; building validates it, so it runs where a failure is caught."""

    def build() -> SetupReceiptDraft:
        return SetupReceiptDraft(
            request_id=admitted.request.request_id,
            plan_id=None if plan is None else plan.plan_id,
            plan_digest=None if plan is None else plan.plan.digest,
            principal_id=admitted.principal.principal_id,
            capability="setup_plan",
            profile=admitted.profile.profile_id,
            profile_version=admitted.profile.version,
            configuration_generation=(
                generation if plan is None else plan.plan.configuration_generation
            ),
            timestamp_ms=max(1, int(admitted.now * 1000)),
            outcome=outcome,
            effects=effects,
            package_version=__version__,
            template_version=SETUP_SCHEMA_VERSION,
            reason=reason,
        )

    return build


def _effect_receipts(cli_plan: Mapping[str, object]) -> tuple[SetupEffectReceipt, ...]:
    """Return each planned effect's receipt: its unit, its state before, nothing after yet."""
    receipts = []
    for effect in cast(list[dict[str, object]], cli_plan["effects"]):
        effect_id = str(effect["id"])
        blocked = effect["disposition"] == "blocked"
        receipts.append(
            SetupEffectReceipt(
                kind=effect_id,
                target=_EFFECT_TARGETS.get(effect_id, effect_id),
                before="blocked" if blocked else _BEFORE[str(effect["observed_status"])],
                after="not_checked",
            )
        )
    return tuple(receipts)


def _record(receipts: Path, build: Callable[[], SetupReceiptDraft]) -> bool:
    """Append one receipt to the chain in ``receipts``, opened for this write alone."""
    try:
        draft = build()
        with SetupReceiptStore(receipts) as chain:
            chain.append(draft)
    except (OSError, ValueError):  # the chain's errors are ValueErrors; a bad draft is one too
        return False
    return True
