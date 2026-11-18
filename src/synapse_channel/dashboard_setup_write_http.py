# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what every setup write shares: the request, the caller, the receipt
"""Read a setup write, know its caller, and record its answer in the setup receipt chain.

``POST /setup/plan`` and ``POST /setup/apply`` admit a request the same way and
write nothing before the caller is known:

1. 404 naming the reason, when this start does not serve setup;
2. 403 without exactly one ``Origin`` that is this dashboard's own
   (``http://<Host>``, compared without case);
3. 401 without a recognised bearer; 415 without ``application/json``.

Each route then parses its own body. From there every answer is a receipt,
built here and appended to the chain, which is opened for that write alone.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from typing import Final, cast

from synapse_channel import __version__
from synapse_channel.dashboard_access import DashboardAccessPolicy, DashboardPrincipal
from synapse_channel.dashboard_access_http import AccessHttpDecision
from synapse_channel.dashboard_operator_writes import is_json_media_type
from synapse_channel.dashboard_setup_contract import MAX_SETUP_REQUEST_BYTES
from synapse_channel.dashboard_setup_http import SetupServing
from synapse_channel.dashboard_setup_planner import SetupPlanStore
from synapse_channel.dashboard_setup_receipts import (
    SetupCapability,
    SetupEffectReceipt,
    SetupInspectionVerdict,
    SetupReceipt,
    SetupReceiptDraft,
    SetupReceiptOutcome,
    SetupReceiptReason,
    SetupReceiptStore,
)
from synapse_channel.setup_contract import SETUP_SCHEMA_VERSION
from synapse_channel.setup_profiles import SetupProfile

SETUP_WRITE_HEADERS: Final = (("Vary", "Authorization"),)
"""Headers of every setup write answer: it varies with the bearer."""

CHAIN_UNAVAILABLE: Final = "setup receipt chain unavailable"
"""The answer's words when a receipt cannot be written."""

_EFFECT_TARGETS: Final = {
    "establish_local_loopback_hub": "synapse-hub.service",
    "establish_identity_waiter": "synapse-arm@.service",
}
"""The unit each supported effect installs, as the executor names it; others name themselves."""

STATUS_VERDICTS: Final[Mapping[str, SetupInspectionVerdict]] = {
    "pass": "satisfied",
    "warn": "unsatisfied",
    "fail": "unsatisfied",
    "unavailable": "unverifiable",
}
"""An inspection status of an effect's check, as a receipt's verdict."""


@dataclass(frozen=True)
class SetupWriteRequest:
    """What the HTTP handler read from one setup write.

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


@dataclass(frozen=True)
class SetupWriter:
    """A known caller of a setup write, on a start that serves setup."""

    principal: DashboardPrincipal
    host: str
    origin: str
    profile: SetupProfile
    plans: SetupPlanStore
    receipts: Path
    now: float


def read_setup_write_request(headers: Message, rfile: io.BufferedIOBase) -> SetupWriteRequest:
    """Read one request's headers, and its body when it is within the contract's bound.

    An over-long or unreadable length leaves the body empty, and the strict parse
    then refuses it by size; nothing over the bound is read.
    """
    try:
        length = int(headers.get("Content-Length") or "0")
    except ValueError:
        length = 0
    body = rfile.read(length) if 0 < length <= MAX_SETUP_REQUEST_BYTES else b""
    return SetupWriteRequest(
        authorization=headers.get("Authorization"),
        host=headers.get("Host", ""),
        origins=tuple(headers.get_all("Origin") or ()),
        content_type=headers.get("Content-Type", ""),
        body=body,
    )


def plain(status: HTTPStatus, text: str, *, authenticate: bool = False) -> AccessHttpDecision:
    """Return one plain-text setup write answer."""
    return AccessHttpDecision(
        status, text.encode(), authenticate=authenticate, headers=SETUP_WRITE_HEADERS
    )


def admit_setup_write(
    request: SetupWriteRequest,
    policy: DashboardAccessPolicy,
    serving: SetupServing,
    now: float,
) -> SetupWriter | AccessHttpDecision:
    """Return the known caller of ``request``, or the answer that refuses it unrecorded."""
    profile, plans, receipts = serving.profile, serving.plans, serving.receipts
    if profile is None or plans is None or receipts is None:
        return plain(HTTPStatus.NOT_FOUND, f"setup not served: {serving.decision.reason}")
    expected = f"http://{request.host}".lower()
    if len(request.origins) != 1 or request.origins[0].lower() != expected:
        return plain(HTTPStatus.FORBIDDEN, "setup writes require this dashboard's own Origin")
    principal = policy.resolve_credential(request.authorization)
    if principal is None:
        return plain(HTTPStatus.UNAUTHORIZED, "bearer required", authenticate=True)
    if not is_json_media_type(request.content_type):
        return plain(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "setup writes require application/json")
    return SetupWriter(principal, request.host, expected, profile, plans, receipts, now)


@dataclass(frozen=True)
class ReceiptFacts:
    """What one receipt says besides who wrote it and when."""

    request_id: str
    capability: SetupCapability
    outcome: SetupReceiptOutcome
    reason: SetupReceiptReason
    plan_id: str | None = None
    plan_digest: str | None = None
    generation: str | None = None
    effects: tuple[SetupEffectReceipt, ...] = ()


def receipt_builder(writer: SetupWriter, facts: ReceiptFacts) -> Callable[[], SetupReceiptDraft]:
    """Return the receipt's builder; building validates it, so it runs where a failure is caught."""

    def build() -> SetupReceiptDraft:
        return SetupReceiptDraft(
            request_id=facts.request_id,
            plan_id=facts.plan_id,
            plan_digest=facts.plan_digest,
            principal_id=writer.principal.principal_id,
            capability=facts.capability,
            profile=writer.profile.profile_id,
            profile_version=writer.profile.version,
            configuration_generation=facts.generation,
            timestamp_ms=max(1, int(writer.now * 1000)),
            outcome=facts.outcome,
            effects=facts.effects,
            package_version=__version__,
            template_version=SETUP_SCHEMA_VERSION,
            reason=facts.reason,
        )

    return build


def effect_receipts(
    cli_plan: Mapping[str, object],
    after: Mapping[str, SetupInspectionVerdict] | None = None,
) -> tuple[SetupEffectReceipt, ...]:
    """Return each effect's receipt: its unit, its state before, and after when it was read.

    ``after`` maps a check id to the verdict a fresh inspection gave it; without
    it every effect's ``after`` is ``not_checked``.
    """
    receipts = []
    for effect in cast(list[dict[str, object]], cli_plan["effects"]):
        effect_id = str(effect["id"])
        blocked = effect["disposition"] == "blocked"
        check = str(effect["verification_check"])
        receipts.append(
            SetupEffectReceipt(
                kind=effect_id,
                target=_EFFECT_TARGETS.get(effect_id, effect_id),
                before="blocked" if blocked else STATUS_VERDICTS[str(effect["observed_status"])],
                after="not_checked" if after is None else after.get(check, "unverifiable"),
            )
        )
    return tuple(receipts)


def record_receipt(receipts: Path, build: Callable[[], SetupReceiptDraft]) -> SetupReceipt | None:
    """Append one receipt to the chain in ``receipts``, opened for this write alone.

    Returns
    -------
    SetupReceipt or None
        The committed receipt, or ``None`` when the draft or the chain refused it.
    """
    try:
        draft = build()
        with SetupReceiptStore(receipts) as chain:
            return chain.append(draft)
    except (OSError, ValueError):  # the chain's errors are ValueErrors; a bad draft is one too
        return None
