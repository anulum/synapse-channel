# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — account entitlement facts and correction rules
"""Validate private account, pool, surface, window and usage facts.

Events are immutable and carry source, confidence and timestamps. A later event
may explicitly supersede an earlier event of the same kind and logical target.
No plan, allowance, price or provider-specific product name is built in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Final

KINDS: Final = frozenset({"account", "pool", "surface", "window", "usage", "balance"})
"""Accepted ledger event kinds."""

CONFIDENCE: Final = frozenset({"official", "operator", "inferred"})
"""Evidence categories, ordered by explicit source rather than implicit trust."""

ACCOUNT_STATUS: Final = frozenset({"active", "suspended", "expired"})
"""Account states relevant to advisory availability."""

COMPUTE_UNITS: Final = {
    "gpu_time": frozenset({"gpu_seconds"}),
    "quantum_shots": frozenset({"shots"}),
    "quantum_credits": frozenset({"quantum_credits"}),
    "ci_minutes": frozenset({"ci_minutes"}),
    "cloud_grant": frozenset({"USD", "CHF", "EUR"}),
}
"""Exact units accepted for explicitly classified compute-credit pools."""

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REQUIRED: Final[dict[str, frozenset[str]]] = {
    "account": frozenset({"account_id", "label", "status"}),
    "pool": frozenset({"pool_id", "account_id", "unit"}),
    "surface": frozenset({"surface_id", "account_id", "pool_id", "product", "channel"}),
    "window": frozenset(
        {"window_id", "pool_id", "starts_at", "ends_at", "grant", "unit", "price_revision"}
    ),
    "usage": frozenset(
        {"window_id", "window_event_id", "source_event_id", "amount", "observed_at"}
    ),
    "balance": frozenset(
        {"window_id", "window_event_id", "source_event_id", "remaining", "observed_at"}
    ),
}
_OPTIONAL: Final[dict[str, frozenset[str]]] = {
    "account": frozenset({"credential_ref", "expires_at"}),
    "pool": frozenset(
        {"resource_kind", "capabilities", "data_classes", "eligible_projects", "idle_cost"}
    ),
    "surface": frozenset(),
    "window": frozenset({"renewal_at"}),
    "usage": frozenset(),
    "balance": frozenset(),
}
_COMMON: Final = frozenset(
    {"event_id", "kind", "recorded_at", "source", "confidence", "supersedes"}
)


class EntitlementError(ValueError):
    """Raised for invalid entitlement evidence or inconsistent revisions."""


def parse_time(value: object, field: str) -> datetime:
    """Parse an offset-aware ISO 8601 instant and normalise it to UTC.

    Parameters
    ----------
    value : object
        Input timestamp; naive local times are refused.
    field : str
        Field name used in validation errors.

    Returns
    -------
    datetime.datetime
        Corresponding UTC instant.
    """
    if not isinstance(value, str):
        raise EntitlementError(f"{field} must be an offset-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EntitlementError(f"{field} must be an offset-aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EntitlementError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def parse_quantity(value: object, field: str) -> Decimal:
    """Parse a finite, non-negative decimal string without binary-float rounding."""
    if not isinstance(value, str) or len(value) > 80:
        raise EntitlementError(f"{field} must be a decimal string")
    try:
        quantity = Decimal(value)
    except InvalidOperation as exc:
        raise EntitlementError(f"{field} must be a decimal string") from exc
    if (
        not quantity.is_finite()
        or quantity < 0
        or (quantity and not -18 <= quantity.adjusted() <= 30)
    ):
        raise EntitlementError(f"{field} must be finite and non-negative")
    return quantity


def _identifier(value: object, field: str) -> str:
    """Return a bounded opaque identifier, rejecting free-form labels."""
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise EntitlementError(f"{field} must be a bounded opaque identifier")
    return value


def _text(value: object, field: str) -> str:
    """Return a bounded non-empty printable text value."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 for character in value)
    ):
        raise EntitlementError(f"{field} must be non-empty printable text")
    return value


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    """Validate a non-empty bounded set of opaque eligibility identifiers."""
    if not isinstance(value, list) or not 1 <= len(value) <= 32:
        raise EntitlementError(f"{field} must be a non-empty list of at most 32 identifiers")
    names = tuple(_identifier(item, field) for item in value)
    if len(names) != len(set(names)):
        raise EntitlementError(f"{field} must not contain duplicates")
    return names


def event_target(event: Mapping[str, object]) -> str:
    """Return the logical id whose revisions must form one correction chain."""
    kind = event["kind"]
    if kind == "account":
        return str(event["account_id"])
    if kind == "pool":
        return str(event["pool_id"])
    if kind == "surface":
        return str(event["surface_id"])
    if kind == "window":
        return str(event["window_id"])
    return f"{event['window_id']}\0{event['source_event_id']}"


def validate_event(event: Mapping[str, object]) -> dict[str, object]:
    """Return a validated detached event with no unexpected fields.

    The exact field set makes plan changes explicit: callers cannot smuggle
    secrets, untyped balances or undocumented units into an accepted record.
    """
    kind = event.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise EntitlementError("kind must name a supported entitlement event")
    required = _REQUIRED[kind] | (_COMMON - {"supersedes"})
    keys = set(event)
    if not required <= keys or not keys <= required | _OPTIONAL[kind] | {"supersedes"}:
        raise EntitlementError(f"{kind} event has missing or unexpected fields")
    _identifier(event["event_id"], "event_id")
    parse_time(event["recorded_at"], "recorded_at")
    _text(event["source"], "source")
    if not isinstance(event["confidence"], str) or event["confidence"] not in CONFIDENCE:
        raise EntitlementError("confidence must be official, operator or inferred")
    if "supersedes" in event:
        _identifier(event["supersedes"], "supersedes")
        if event["supersedes"] == event["event_id"]:
            raise EntitlementError("event cannot supersede itself")
    for field in (
        "account_id",
        "pool_id",
        "surface_id",
        "window_id",
        "window_event_id",
        "source_event_id",
    ):
        if field in event:
            _identifier(event[field], field)
    if kind == "account":
        _text(event["label"], "label")
        if not isinstance(event["status"], str) or event["status"] not in ACCOUNT_STATUS:
            raise EntitlementError("account status must be active, suspended or expired")
        if "credential_ref" in event:
            reference = _text(event["credential_ref"], "credential_ref")
            if not reference.startswith("ref:"):
                raise EntitlementError("credential_ref must be an external reference")
        if "expires_at" in event:
            parse_time(event["expires_at"], "expires_at")
    if kind in {"pool", "window"}:
        _identifier(event["unit"], "unit")
    if kind == "pool" and "resource_kind" in event:
        resource_kind = _identifier(event["resource_kind"], "resource_kind")
        if resource_kind not in COMPUTE_UNITS or event["unit"] not in COMPUTE_UNITS[resource_kind]:
            raise EntitlementError("compute resource kind and unit are incompatible")
        for field in ("capabilities", "data_classes", "eligible_projects"):
            _identifiers(event.get(field), field)
        if "idle_cost" in event:
            idle = event["idle_cost"]
            if not isinstance(idle, dict) or set(idle) != {"amount_per_hour", "currency"}:
                raise EntitlementError("idle_cost requires amount_per_hour and currency")
            parse_quantity(idle["amount_per_hour"], "idle_cost amount_per_hour")
            if not isinstance(idle["currency"], str) or idle["currency"] not in {
                "USD",
                "CHF",
                "EUR",
            }:
                raise EntitlementError("idle_cost currency must be USD, CHF or EUR")
    elif kind == "pool" and any(
        field in event
        for field in ("capabilities", "data_classes", "eligible_projects", "idle_cost")
    ):
        raise EntitlementError("compute pool metadata requires resource_kind")
    if kind == "surface":
        _text(event["product"], "product")
        _identifier(event["channel"], "channel")
    if kind == "window":
        start = parse_time(event["starts_at"], "starts_at")
        end = parse_time(event["ends_at"], "ends_at")
        if end <= start:
            raise EntitlementError("window end must follow its start")
        parse_quantity(event["grant"], "grant")
        _identifier(event["price_revision"], "price_revision")
        if "renewal_at" in event:
            parse_time(event["renewal_at"], "renewal_at")
    if kind in {"usage", "balance"}:
        parse_time(event["observed_at"], "observed_at")
        quantity_field = "amount" if kind == "usage" else "remaining"
        if event[quantity_field] is not None or kind == "usage":
            parse_quantity(event[quantity_field], quantity_field)
    return dict(event)


def active_events(events: Sequence[Mapping[str, object]]) -> tuple[dict[str, object], ...]:
    """Project correction chains while retaining source records in storage.

    Raises
    ------
    EntitlementError
        On a missing predecessor, incompatible correction, duplicate target or
        corrupted chronology. Input order is ledger insertion order.
    """
    by_id: dict[str, dict[str, object]] = {}
    active: dict[tuple[str, str], dict[str, object]] = {}
    superseded: set[str] = set()
    for raw in events:
        event = validate_event(raw)
        event_id = str(event["event_id"])
        kind = str(event["kind"])
        target = event_target(event)
        key = (kind, target)
        if event_id in by_id:
            raise EntitlementError("duplicate event id in store")
        predecessor_id = event.get("supersedes")
        if predecessor_id is not None:
            predecessor = by_id.get(str(predecessor_id))
            if predecessor is None or str(predecessor_id) in superseded:
                raise EntitlementError("correction must name an active earlier event")
            if predecessor["kind"] != kind or event_target(predecessor) != target:
                raise EntitlementError("correction must retain kind and logical target")
            if parse_time(event["recorded_at"], "recorded_at") <= parse_time(
                predecessor["recorded_at"], "recorded_at"
            ):
                raise EntitlementError("correction must be recorded after its predecessor")
            superseded.add(str(predecessor_id))
        elif key in active:
            raise EntitlementError("duplicate logical target requires explicit correction")
        by_id[event_id] = event
        active[key] = event
    accounts = {target: item for (kind, target), item in active.items() if kind == "account"}
    pools = {target: item for (kind, target), item in active.items() if kind == "pool"}
    windows = {target: item for (kind, target), item in active.items() if kind == "window"}
    for pool in pools.values():
        if pool["account_id"] not in accounts:
            raise EntitlementError("pool refers to an unknown account")
    for (kind, _), surface in active.items():
        if kind != "surface":
            continue
        target_pool = pools.get(str(surface["pool_id"]))
        if target_pool is None or target_pool["account_id"] != surface["account_id"]:
            raise EntitlementError("surface must belong to the pool's account")
    intervals: dict[str, list[tuple[datetime, datetime]]] = {}
    for window in windows.values():
        target_pool = pools.get(str(window["pool_id"]))
        if target_pool is None or target_pool["unit"] != window["unit"]:
            raise EntitlementError("window must use its pool's unit")
        start = parse_time(window["starts_at"], "starts_at")
        end = parse_time(window["ends_at"], "ends_at")
        previous = intervals.setdefault(str(window["pool_id"]), [])
        if any(
            start < earlier_end and earlier_start < end for earlier_start, earlier_end in previous
        ):
            raise EntitlementError("windows for one pool must not overlap")
        previous.append((start, end))
    source_keys: set[tuple[str, str]] = set()
    balance_times: set[tuple[str, datetime]] = set()
    for (kind, _), observation in active.items():
        if kind not in {"usage", "balance"}:
            continue
        window_id = str(observation["window_id"])
        revision = by_id.get(str(observation["window_event_id"]))
        if revision is None or revision["kind"] != "window" or revision["window_id"] != window_id:
            raise EntitlementError("observation refers to an unknown window revision")
        observed = parse_time(observation["observed_at"], "observed_at")
        if not (
            parse_time(revision["starts_at"], "starts_at")
            <= observed
            < parse_time(revision["ends_at"], "ends_at")
        ):
            raise EntitlementError("observation must fall inside its quota window")
        source_key = (window_id, str(observation["source_event_id"]))
        if source_key in source_keys:
            raise EntitlementError("source observation id reused in one pool window")
        source_keys.add(source_key)
        if kind == "balance":
            if observation["remaining"] is not None and parse_quantity(
                observation["remaining"], "remaining"
            ) > parse_quantity(revision["grant"], "grant"):
                raise EntitlementError("remaining balance exceeds window grant")
            balance_key = (window_id, observed)
            if balance_key in balance_times:
                raise EntitlementError("duplicate balance timestamp in one window")
            balance_times.add(balance_key)
    return tuple(active.values())
