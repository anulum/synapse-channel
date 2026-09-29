# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the redacted, opt-in pool advertisement a fleet may see (F03 option A)
"""Build and validate the only entitlement facts that may leave the owner's ledger.

Entitlement facts are private: the ledger is owner-only and nothing in it is
replicated (:mod:`synapse_channel.core.entitlement_store`). A fleet that plans
work across hubs still needs to know which pools exist and roughly how much is
left. The owner therefore opts in per pool: ``synapse entitlements advertise``
turns one pool of the private view into an advertisement under an alias. The
hub records it as an audit-only journal row, and fleet mirrors read it.

What an advertisement carries:
- the alias chosen by the owner, never the pool id, account id, account label,
  credential reference, products or source references;
- the unit, resource kind, capabilities, data classes and eligible projects the
  planner needs to exclude unsuitable work;
- per window, an opaque reference, the end time, whether it is current, a
  remaining-balance **bucket** (never the amount), the balance evidence class,
  the observation age, the forecast state and the confidence.

The validator accepts exactly these fields, with bounded sizes, so neither the
hub nor a fleet ever stores anything wider.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from synapse_channel.core.entitlement_view import entitlement_view
from synapse_channel.core.errors import SynapseError

ADVERT_SCHEMA = 1
"""Version of the advertisement document."""

BUCKETS = ("depleted", "<10%", "10-50%", ">50%", "unknown")
"""Remaining-balance buckets, shared instead of amounts."""

EVIDENCE_CLASSES = ("observed", "estimated", "unknown", "outside_window")
"""Coarse balance evidence, mapped from the private view's evidence."""

FORECAST_STATES = ("insufficient_data", "depleted", "quiet", "depleting")
CONFIDENCE = ("official", "operator", "inferred")
MAX_WINDOWS = 16
MAX_LIST_ITEMS = 32
_ALIAS = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TOKEN = re.compile(r"[A-Za-z0-9._:/-]{1,64}")
_WINDOW_REF = re.compile(r"[0-9a-f]{16}")
_EVIDENCE_MAP = {
    "observed_plus_recorded_usage": "observed",
    "incomplete_usage_estimate": "estimated",
    "unknown": "unknown",
    "outside_window": "outside_window",
}
_TOP_FIELDS = frozenset(
    {
        "schema",
        "pool_alias",
        "unit",
        "resource_kind",
        "capabilities",
        "data_classes",
        "eligible_projects",
        "account_usable",
        "as_of",
        "windows",
    }
)
_WINDOW_FIELDS = frozenset(
    {
        "window_ref",
        "ends_at",
        "current",
        "remaining_bucket",
        "balance_evidence",
        "observation_age_seconds",
        "forecast_state",
        "confidence",
    }
)


class EntitlementAdvertError(SynapseError, ValueError):
    """Raised when an advertisement cannot be built or is not well formed."""

    code = "entitlement_advert"


def remaining_bucket(remaining: object, grant: object) -> str:
    """Return the bucket for ``remaining`` of ``grant``; ``unknown`` when either is unusable."""
    try:
        left = Decimal(str(remaining)) if remaining is not None else None
        total = Decimal(str(grant))
    except InvalidOperation:
        return "unknown"
    if left is None or not left.is_finite() or not total.is_finite() or total <= 0:
        return "unknown"
    if left <= 0:
        return "depleted"
    share = left / total
    if share < Decimal("0.1"):
        return "<10%"
    if share <= Decimal("0.5"):
        return "10-50%"
    return ">50%"


def window_ref(pool_alias: str, window_id: str) -> str:
    """Return an opaque, stable 16-hex reference for a window under an alias."""
    return hashlib.sha256(f"{pool_alias}\0{window_id}".encode()).hexdigest()[:16]


def build_advert(
    events: Sequence[Mapping[str, object]], *, pool_id: str, alias: str, as_of: datetime
) -> dict[str, Any]:
    """Return the redacted advertisement for one pool of the private ledger.

    Raises
    ------
    EntitlementAdvertError
        When the alias is malformed or the ledger has no such pool.
    """
    if _ALIAS.fullmatch(alias) is None:
        raise EntitlementAdvertError(
            "alias must be 1-64 characters of letters, digits, '.', '_' or '-'"
        )
    view = entitlement_view(events, as_of=as_of, private=True)
    pool = next((row for row in view["pools"] if row["pool_id"] == pool_id), None)
    if pool is None:
        raise EntitlementAdvertError("the private ledger has no such pool")
    windows = [
        {
            "window_ref": window_ref(alias, str(window["window_id"])),
            "ends_at": str(window["ends_at"]),
            "current": bool(window["current"]),
            "remaining_bucket": (
                remaining_bucket(window["remaining"], window["grant"])
                if window["current"]
                else "unknown"
            ),
            "balance_evidence": _EVIDENCE_MAP[str(window["balance_evidence"])],
            "observation_age_seconds": window["observation_age_seconds"],
            "forecast_state": window["forecast"]["state"],
            "confidence": window["confidence"],
        }
        for window in pool["windows"]
    ]
    return validate_advert(
        {
            "schema": ADVERT_SCHEMA,
            "pool_alias": alias,
            "unit": pool["unit"],
            "resource_kind": pool["resource_kind"],
            "capabilities": list(pool["capabilities"]),
            "data_classes": list(pool["data_classes"]),
            "eligible_projects": list(pool["eligible_projects"]),
            "account_usable": pool["account_usable"],
            "as_of": view["as_of"],
            "windows": windows[-MAX_WINDOWS:],
        }
    )


def validate_advert(advert: object) -> dict[str, Any]:
    """Return ``advert`` when it has exactly the advertisement shape.

    Raises
    ------
    EntitlementAdvertError
        When a field is missing, extra, of the wrong type, or out of bounds.
    """
    if not isinstance(advert, Mapping) or set(advert) != _TOP_FIELDS:
        raise EntitlementAdvertError("an advertisement has exactly the documented fields")
    if advert["schema"] != ADVERT_SCHEMA:
        raise EntitlementAdvertError(f"advertisement schema must be {ADVERT_SCHEMA}")
    if not isinstance(advert["pool_alias"], str) or not _ALIAS.fullmatch(advert["pool_alias"]):
        raise EntitlementAdvertError("pool_alias is malformed")
    _token(advert["unit"], "unit")
    if advert["resource_kind"] is not None:
        _token(advert["resource_kind"], "resource_kind")
    for name in ("capabilities", "data_classes", "eligible_projects"):
        items = advert[name]
        if not isinstance(items, list) or len(items) > MAX_LIST_ITEMS:
            raise EntitlementAdvertError(f"{name} must be a list of at most {MAX_LIST_ITEMS}")
        for item in items:
            _token(item, name)
    if not isinstance(advert["account_usable"], bool):
        raise EntitlementAdvertError("account_usable must be a boolean")
    _timestamp(advert["as_of"], "as_of")
    windows = advert["windows"]
    if not isinstance(windows, list) or len(windows) > MAX_WINDOWS:
        raise EntitlementAdvertError(f"windows must be a list of at most {MAX_WINDOWS}")
    for window in windows:
        _window(window)
    return dict(advert)


def _window(window: object) -> None:
    if not isinstance(window, Mapping) or set(window) != _WINDOW_FIELDS:
        raise EntitlementAdvertError("a window has exactly the documented fields")
    if not isinstance(window["window_ref"], str) or not _WINDOW_REF.fullmatch(window["window_ref"]):
        raise EntitlementAdvertError("window_ref must be 16 lowercase hex characters")
    _timestamp(window["ends_at"], "ends_at")
    if not isinstance(window["current"], bool):
        raise EntitlementAdvertError("current must be a boolean")
    for name, allowed in (
        ("remaining_bucket", BUCKETS),
        ("balance_evidence", EVIDENCE_CLASSES),
        ("forecast_state", FORECAST_STATES),
        ("confidence", CONFIDENCE),
    ):
        if window[name] not in allowed:
            raise EntitlementAdvertError(f"{name} must be one of {', '.join(allowed)}")
    age = window["observation_age_seconds"]
    if age is not None and (
        isinstance(age, bool)
        or not isinstance(age, (int, float))
        or not math.isfinite(age)
        or age < 0
    ):
        raise EntitlementAdvertError("observation_age_seconds must be a non-negative number")


def _token(value: object, name: str) -> None:
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise EntitlementAdvertError(f"{name} entries must be short plain tokens")


def _timestamp(value: object, name: str) -> None:
    if not isinstance(value, str) or len(value) > 40:
        raise EntitlementAdvertError(f"{name} must be an ISO-8601 time")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EntitlementAdvertError(f"{name} must be an ISO-8601 time") from exc
    if parsed.tzinfo is None:
        raise EntitlementAdvertError(f"{name} must include a UTC offset")
