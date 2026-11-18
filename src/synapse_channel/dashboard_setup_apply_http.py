# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — apply one issued setup plan once, through the command line's executor
"""Serve ``POST /setup/apply``: apply one issued plan once, and record what happened.

What a person confirmed is what is applied. The route rebuilds the command
line's plan from a fresh inspection, takes the issued plan's one use only when
that plan is still the one the dashboard plan was bound to, and hands the
command line's executor (:func:`synapse_channel.setup_executor.apply_setup`) an
authorisation built on the server for that plan. The browser never sends an
authorisation, a process id or a path.

It admits the caller as every setup write does, writing no receipt before the
caller is known, and answers 400 naming the refusal code of a body that is not
exactly the apply request. From there every answer is a receipt:

1. 429 ``rate_limited``; 403 ``denied`` without ``setup_apply``;
2. 503 ``refused`` with ``apply_unavailable`` when the inspection fails;
3. 409 ``drifted`` with ``plan_drift`` when the host's executables changed since
   the plan was issued (the plan is not consumed);
4. the plan store's refusals of the one use, 409: ``plan_not_found``,
   ``plan_expired`` (``expired``), ``plan_replayed``, ``plan_mismatch``;
5. 409 ``drifted`` with ``plan_drift`` when the rebuilt plan's digest is not
   the bound one, and 409 ``refused`` with ``plan_blocked`` for a blocked
   effect;
6. 200 ``already_satisfied`` for a plan with no effect; the executor is not run;
7. ``authorised``, written before the executor runs: a chain that cannot be
   written answers 503 before any effect;
8. the executor's refusals before any effect: ``drifted``, ``expired``,
   ``refused`` with ``unsafe_posture``, or 503 ``apply_unavailable``;
9. 200 with the outcome's receipt: ``applied`` when every effect's ``after``,
   read by a fresh inspection after the apply, is ``satisfied``; ``partial`` with
   ``postcondition_unverified`` when one is not; ``refused`` with
   ``effect_failed`` when an effect failed and was recovered; ``partial`` with
   ``partial_effects`` when recovery failed.

The dashboard protects itself: its own process id is a protected process, so
the executor refuses a restart of it before any effect.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from http import HTTPStatus
from typing import Any, Final, cast

from synapse_channel.dashboard_access import DashboardAccessPolicy
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator import WriteRateLimiter
from synapse_channel.dashboard_setup_contract import (
    SetupApplyRequest,
    SetupContractRefusal,
    SetupPlan,
    parse_setup_apply_request,
    setup_generation_digest,
)
from synapse_channel.dashboard_setup_http import Inspection, SetupServing
from synapse_channel.dashboard_setup_planner import SetupPlanBinding, SetupPlanStoreRefusal
from synapse_channel.dashboard_setup_receipts import (
    SetupInspectionVerdict,
    SetupReceiptOutcome,
    SetupReceiptReason,
)
from synapse_channel.dashboard_setup_write_http import (
    CHAIN_UNAVAILABLE,
    SETUP_WRITE_HEADERS,
    STATUS_VERDICTS,
    ReceiptFacts,
    SetupWriter,
    SetupWriteRequest,
    admit_setup_write,
    effect_receipts,
    plain,
    receipt_builder,
    record_receipt,
)
from synapse_channel.setup_authorization import (
    MAX_AUTHORIZATION_SECONDS,
    MIN_AUTHORIZATION_SECONDS,
    SetupAuthorizationError,
    build_setup_authorization,
)
from synapse_channel.setup_executor import (
    PidProbe,
    SetupCommandRunner,
    SetupInspectionRunner,
    apply_setup,
)
from synapse_channel.setup_planner import build_setup_plan as build_cli_setup_plan

SETUP_APPLY_PATH: Final = "/setup/apply"
"""Apply one issued setup plan once."""

_NONCE_DOMAIN: Final = b"synapse-dashboard-setup-apply\x00"
"""The tag the command line's nonce is derived under; the browser's nonce stays out of it."""

_Refusal = tuple[HTTPStatus, SetupReceiptOutcome, SetupReceiptReason]

_UNAVAILABLE: Final[_Refusal] = (HTTPStatus.SERVICE_UNAVAILABLE, "refused", "apply_unavailable")

_DRIFTED: Final[_Refusal] = (HTTPStatus.CONFLICT, "drifted", "plan_drift")

_AUTHORISE_REFUSALS: Final[Mapping[str, _Refusal]] = {
    "not_found": (HTTPStatus.CONFLICT, "refused", "plan_not_found"),
    "expired": (HTTPStatus.CONFLICT, "expired", "plan_expired"),
    "replayed": (HTTPStatus.CONFLICT, "refused", "plan_replayed"),
    "mismatch": (HTTPStatus.CONFLICT, "refused", "plan_mismatch"),
}
"""Each plan-store refusal of the one use: its answer, outcome and reason."""

_EXECUTOR_REFUSALS: Final[Mapping[str, _Refusal]] = {
    "application_target_changed": _DRIFTED,
    "authorization_expired": (HTTPStatus.CONFLICT, "expired", "plan_expired"),
    "application_protected_process_missing": (HTTPStatus.CONFLICT, "refused", "unsafe_posture"),
}
"""The executor's refusals before any effect that are named; any other is ``apply_unavailable``."""

_EXECUTED: Final[Mapping[str, tuple[SetupReceiptOutcome, SetupReceiptReason]]] = {
    "recovered": ("refused", "effect_failed"),
    "recovery_failed": ("partial", "partial_effects"),
}
"""The executor's outcomes after a failed effect; ``applied`` is judged by the effects' after."""


@dataclass(frozen=True)
class SetupApplyBindings:
    """What one dashboard start lends the route.

    ``runner``, ``inspection_runner`` and ``probe`` are the executor's seams; left
    ``None``, the executor uses its own (the real service manager, inspection and
    process probe). Tests set them, on a temporary home.
    """

    policy: DashboardAccessPolicy
    serving: SetupServing
    inspect: Inspection
    rate_limiter: WriteRateLimiter
    environment: Mapping[str, str]
    runner: SetupCommandRunner | None = None
    inspection_runner: SetupInspectionRunner | None = None
    probe: PidProbe | None = None
    clock: Callable[[], float] = time.time


@dataclass(frozen=True)
class _Apply:
    """An admitted apply request, and what its receipts say of the plan so far."""

    writer: SetupWriter
    request: SetupApplyRequest
    facts: ReceiptFacts


def setup_apply_response(
    request: SetupWriteRequest, bindings: SetupApplyBindings
) -> AccessHttpDecision:
    """Answer one ``POST /setup/apply`` in the module's order; record each known caller's answer."""
    writer = admit_setup_write(request, bindings.policy, bindings.serving, bindings.clock())
    if isinstance(writer, AccessHttpDecision):
        return writer
    parsed = parse_setup_apply_request(request.body)
    if isinstance(parsed, SetupContractRefusal):
        return plain(HTTPStatus.BAD_REQUEST, f"setup apply request refused: {parsed.code}")
    named = ReceiptFacts(
        parsed.request_id,
        "setup_apply",
        "authorised",
        "none",
        plan_id=parsed.plan_id,
        plan_digest=parsed.plan_digest,
    )
    return _authorise(_Apply(writer, parsed, named), bindings)


def _refuse(apply: _Apply, refusal: _Refusal) -> AccessHttpDecision:
    """Record a refusal with what is known of the plan so far, then answer it naming its reason."""
    status, outcome, reason = refusal
    facts = replace(apply.facts, outcome=outcome, reason=reason)
    if record_receipt(apply.writer.receipts, receipt_builder(apply.writer, facts)) is None:
        return plain(HTTPStatus.SERVICE_UNAVAILABLE, CHAIN_UNAVAILABLE)
    return plain(status, f"setup apply refused: {reason}")


def _authorise(apply: _Apply, bindings: SetupApplyBindings) -> AccessHttpDecision:
    """Take the plan's one use once the host is still the one it was planned on."""
    writer, request = apply.writer, apply.request
    if not bindings.rate_limiter.allow():
        return _refuse(apply, (HTTPStatus.TOO_MANY_REQUESTS, "rate_limited", "rate_limit"))
    if not writer.principal.capabilities.setup_apply:
        return _refuse(apply, (HTTPStatus.FORBIDDEN, "denied", "capability_denied"))
    try:
        cli_plan = build_cli_setup_plan(writer.profile, bindings.inspect(writer.profile))
        generation = setup_generation_digest(cli_plan)
        principal_id = writer.principal.principal_id
        binding = SetupPlanBinding(principal_id, writer.host, writer.origin, generation)
    except Exception:  # any inspection or planning failure is one bounded answer, never a trace
        return _refuse(apply, _UNAVAILABLE)
    held = writer.plans.generation_of(request.plan_id)
    if held is not None and held != generation:
        return _refuse(apply, _DRIFTED)
    authorised = writer.plans.authorise_once(request, binding=binding, now=int(writer.now))
    if isinstance(authorised, SetupPlanStoreRefusal):
        return _refuse(apply, _AUTHORISE_REFUSALS[authorised.code])
    plan = authorised.plan
    bound = replace(apply.facts, generation=plan.configuration_generation)
    return _apply_plan(replace(apply, facts=bound), plan, cli_plan, bindings)


def _apply_plan(
    apply: _Apply, plan: SetupPlan, cli_plan: dict[str, object], bindings: SetupApplyBindings
) -> AccessHttpDecision:
    """Apply an authorised plan whose command-line plan is still the bound one."""
    if plan.cli_plan_digest != cli_plan["plan_digest"]:
        return _refuse(apply, _DRIFTED)
    if any(effect.disposition == "blocked" for effect in plan.effects):
        return _refuse(apply, (HTTPStatus.CONFLICT, "refused", "plan_blocked"))
    if not plan.effects:
        return _answer(apply, "already_satisfied", "none")
    try:
        authorization = build_setup_authorization(
            cli_plan,
            confirm_digest=plan.cli_plan_digest,
            nonce=_cli_nonce(apply.request.confirmation_nonce),
            expires_in=_lifetime(plan, apply.writer.now),
            restart_pid=next((e.process_id for e in plan.effects if e.process_id), None),
            clock=bindings.clock,
        )
    except SetupAuthorizationError:
        return _refuse(apply, _UNAVAILABLE)
    authorised = replace(apply.facts, effects=effect_receipts(cli_plan))
    writer = apply.writer
    if record_receipt(writer.receipts, receipt_builder(writer, authorised)) is None:
        return plain(HTTPStatus.SERVICE_UNAVAILABLE, CHAIN_UNAVAILABLE)
    return _execute(replace(apply, facts=authorised), plan, cli_plan, authorization, bindings)


def _execute(
    apply: _Apply,
    plan: SetupPlan,
    cli_plan: dict[str, object],
    authorization: dict[str, object],
    bindings: SetupApplyBindings,
) -> AccessHttpDecision:
    """Run the command line's executor once, and record its outcome with each effect's ``after``."""
    seams: dict[str, Any] = {
        name: value
        for name, value in (
            ("runner", bindings.runner),
            ("inspection_runner", bindings.inspection_runner),
            ("probe", bindings.probe),
        )
        if value is not None
    }
    try:
        result = asyncio.run(
            apply_setup(
                cli_plan,
                authorization,
                confirm_digest=plan.cli_plan_digest,
                protected_pids=(os.getpid(),),
                env=bindings.environment,
                clock=bindings.clock,
                **seams,
            )
        )
    except SetupAuthorizationError as refusal:
        return _refuse(apply, _EXECUTOR_REFUSALS.get(refusal.code, _UNAVAILABLE))
    effects = effect_receipts(cli_plan, _after(apply, bindings))
    outcome: SetupReceiptOutcome
    reason: SetupReceiptReason
    executed = _EXECUTED.get(str(result.get("outcome")))
    if executed is not None:
        outcome, reason = executed
    elif all(effect.after == "satisfied" for effect in effects):
        outcome, reason = "applied", "none"
    else:
        outcome, reason = "partial", "postcondition_unverified"
    return _answer(replace(apply, facts=replace(apply.facts, effects=effects)), outcome, reason)


def _after(apply: _Apply, bindings: SetupApplyBindings) -> Mapping[str, SetupInspectionVerdict]:
    """Return each check's verdict from a fresh inspection after the apply; none when it fails."""
    try:
        inspection = bindings.inspect(apply.writer.profile)
        checks = cast(list[dict[str, object]], inspection["checks"])
        return {str(check["id"]): STATUS_VERDICTS[str(check["status"])] for check in checks}
    except Exception:  # an unreadable host afterwards leaves every after unverifiable
        return {}


def _answer(
    apply: _Apply, outcome: SetupReceiptOutcome, reason: SetupReceiptReason
) -> AccessHttpDecision:
    """Record the apply's outcome and answer with the receipt as the browser may read it."""
    facts = replace(apply.facts, outcome=outcome, reason=reason)
    writer = apply.writer
    receipt = record_receipt(writer.receipts, receipt_builder(writer, facts))
    if receipt is None:
        return plain(HTTPStatus.SERVICE_UNAVAILABLE, CHAIN_UNAVAILABLE)
    body = json.dumps(receipt.browser_projection(), sort_keys=True).encode()
    return AccessHttpDecision(HTTPStatus.OK, body, writer.principal, headers=SETUP_WRITE_HEADERS)


def _cli_nonce(confirmation_nonce: str) -> str:
    """Derive the command line's one-use nonce from the confirmation nonce, under its own domain."""
    return hashlib.sha256(_NONCE_DOMAIN + confirmation_nonce.encode("ascii")).hexdigest()


def _lifetime(plan: SetupPlan, now: float) -> int:
    """Return the plan's remaining time, held within the command line's authorisation bounds."""
    remaining = plan.expires_at - int(now)
    return max(MIN_AUTHORIZATION_SECONDS, min(MAX_AUTHORIZATION_SECONDS, remaining))
