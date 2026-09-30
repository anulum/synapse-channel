# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — shared-pool configuration, requests and exposure projection (F02)
"""Validate shared-pool configuration and reservations, and project a pool's exposure.

A shared pool has one authoritative owner hub. The owner configures the pool (an
operator act on the owner host) and decides every reservation against one
invariant, in the pool's single unit and for the current window:

``settled + outstanding_exposure + new_exposure <= hard_bound``

A reservation's exposure covers the pool's per-call minimum charge and fixed fee
(``max(upper_bound, minimum_charge) + fixed_fee``). Expiry never releases exposure;
only a final settlement or an operator reconciliation does. Exposure carried from
an earlier owner epoch still counts. A settlement above a reservation's exposure is
an overrun, and it blocks every new grant until the operator reconciles it.

This module is pure: it validates documents and folds recorded ledger events into
a :class:`PoolState`. Storage and serialization live in
:mod:`synapse_channel.core.spend_ledger`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from synapse_channel.core.errors import SynapseError

TAX_BASES = ("pre_tax", "post_tax", "not_applicable")
"""Declared tax treatment of every quantity in a pool (review correction C2)."""

PROVENANCES = ("measured", "billed")
"""Evidence class of a settlement amount."""

MAX_LIMIT = 1_000_000
"""Upper bound on any configured integer limit."""

_TOKEN = re.compile(r"[A-Za-z0-9._:/@-]{1,128}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_MAX_CAUSE = 500


class SpendPoolError(SynapseError, ValueError):
    """Raised when a pool configuration or reservation request is malformed."""

    code = "spend_pool"


def token(value: object, name: str) -> str:
    """Return ``value`` when it is a short plain token."""
    if not isinstance(value, str) or _TOKEN.fullmatch(value) is None:
        raise SpendPoolError(f"{name} must be a short plain token")
    return value


def quantity(value: object, name: str) -> Decimal:
    """Return a finite, non-negative exact quantity from a decimal string."""
    if not isinstance(value, str) or len(value) > 64:
        raise SpendPoolError(f"{name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SpendPoolError(f"{name} must be a decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise SpendPoolError(f"{name} must be a finite, non-negative amount")
    return parsed


def limit(value: object, name: str) -> int:
    """Return a positive integer limit."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIMIT:
        raise SpendPoolError(f"{name} must be an integer from 1 to {MAX_LIMIT}")
    return value


def timestamp(value: object, name: str) -> datetime:
    """Return an offset-aware time from an ISO-8601 string."""
    if not isinstance(value, str) or len(value) > 40:
        raise SpendPoolError(f"{name} must be an ISO-8601 time")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise SpendPoolError(f"{name} must be an ISO-8601 time") from exc
    if parsed.tzinfo is None:
        raise SpendPoolError(f"{name} must include a UTC offset")
    return parsed


def _fields(
    document: object,
    required: frozenset[str],
    name: str,
    optional: frozenset[str] = frozenset(),
) -> Mapping[str, Any]:
    if (
        not isinstance(document, Mapping)
        or not required <= set(document)
        or not set(document) <= required | optional
    ):
        raise SpendPoolError(f"{name} must have exactly the fields: {', '.join(sorted(required))}")
    return document


_CONFIG_FIELDS = frozenset(
    {
        "pool_id",
        "owner_hub_id",
        "epoch",
        "account_ref",
        "billing_surface",
        "unit",
        "window_starts_at",
        "window_ends_at",
        "price_revision",
        "hard_bound",
        "cost_basis",
        "limits",
        "grantees",
        "cause",
    }
)
_CONFIG_OPTIONAL = frozenset({"revocation_keys"})
_REVOCATION_KEY_FIELDS = frozenset({"key_id", "public_key"})
_PUBLIC_KEY = re.compile(r"[A-Za-z0-9+/]{43}=")
_BASIS_FIELDS = frozenset({"tax", "fixed_fee", "minimum_charge"})
_LIMIT_FIELDS = frozenset({"max_depth", "max_agents", "max_wall_seconds"})
_GRANTEE_FIELDS = frozenset({"hub", "project"})


@dataclass(frozen=True)
class PoolConfig:
    """One validated configuration of a shared pool."""

    pool_id: str
    owner_hub_id: str
    epoch: int
    account_ref: str
    billing_surface: str
    unit: str
    window_starts_at: datetime
    window_ends_at: datetime
    price_revision: str
    hard_bound: Decimal
    tax: str
    fixed_fee: Decimal
    minimum_charge: Decimal
    max_depth: int
    max_agents: int
    max_wall_seconds: int
    grantees: frozenset[tuple[str, str]]
    cause: str
    revocation_keys: Mapping[str, str] = field(default_factory=dict)

    def exposure(self, upper_bound: Decimal) -> Decimal:
        """Return the exposure a call with ``upper_bound`` reserves (C2)."""
        return max(upper_bound, self.minimum_charge) + self.fixed_fee


def validate_config(document: object) -> PoolConfig:
    """Return the configuration when ``document`` has exactly the configuration shape.

    Raises
    ------
    SpendPoolError
        When a field is missing, extra or invalid, the window is empty, the cause is
        empty, or a monetary pool declares no tax basis.
    """
    data = _fields(document, _CONFIG_FIELDS, "pool configuration", _CONFIG_OPTIONAL)
    basis = _fields(data["cost_basis"], _BASIS_FIELDS, "cost_basis")
    limits = _fields(data["limits"], _LIMIT_FIELDS, "limits")
    epoch = data["epoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
        raise SpendPoolError("epoch must be a positive integer")
    unit = token(data["unit"], "unit")
    tax = basis["tax"]
    if tax not in TAX_BASES:
        raise SpendPoolError(f"cost_basis.tax must be one of {', '.join(TAX_BASES)}")
    if tax == "not_applicable" and _CURRENCY.fullmatch(unit):
        raise SpendPoolError("a monetary pool must declare a pre_tax or post_tax basis")
    starts = timestamp(data["window_starts_at"], "window_starts_at")
    ends = timestamp(data["window_ends_at"], "window_ends_at")
    if ends <= starts:
        raise SpendPoolError("the window must end after it starts")
    raw_grantees = data["grantees"]
    if not isinstance(raw_grantees, list) or not 1 <= len(raw_grantees) <= 256:
        raise SpendPoolError("grantees must be a list of 1 to 256 entries")
    grantees = frozenset(
        (
            token(_fields(item, _GRANTEE_FIELDS, "grantee")["hub"], "grantee hub"),
            token(item["project"], "grantee project"),
        )
        for item in raw_grantees
    )
    cause = data["cause"]
    if not isinstance(cause, str) or not cause.strip() or len(cause) > _MAX_CAUSE:
        raise SpendPoolError(f"cause must be a non-empty reason of at most {_MAX_CAUSE} characters")
    return PoolConfig(
        pool_id=token(data["pool_id"], "pool_id"),
        owner_hub_id=token(data["owner_hub_id"], "owner_hub_id"),
        epoch=epoch,
        account_ref=token(data["account_ref"], "account_ref"),
        billing_surface=token(data["billing_surface"], "billing_surface"),
        unit=unit,
        window_starts_at=starts,
        window_ends_at=ends,
        price_revision=token(data["price_revision"], "price_revision"),
        hard_bound=quantity(data["hard_bound"], "hard_bound"),
        tax=str(tax),
        fixed_fee=quantity(basis["fixed_fee"], "cost_basis.fixed_fee"),
        minimum_charge=quantity(basis["minimum_charge"], "cost_basis.minimum_charge"),
        max_depth=limit(limits["max_depth"], "limits.max_depth"),
        max_agents=limit(limits["max_agents"], "limits.max_agents"),
        max_wall_seconds=limit(limits["max_wall_seconds"], "limits.max_wall_seconds"),
        grantees=grantees,
        cause=cause.strip(),
        revocation_keys=_revocation_keys(data.get("revocation_keys", [])),
    )


def _revocation_keys(value: object) -> dict[str, str]:
    """Return the operator keys that may sign an owner revocation, by key id."""
    if not isinstance(value, list) or len(value) > 8:
        raise SpendPoolError("revocation_keys must be a list of at most 8 keys")
    keys: dict[str, str] = {}
    for item in value:
        entry = _fields(item, _REVOCATION_KEY_FIELDS, "revocation key")
        public = entry["public_key"]
        if not isinstance(public, str) or _PUBLIC_KEY.fullmatch(public) is None:
            raise SpendPoolError("a revocation public key must be a base64 Ed25519 key")
        keys[token(entry["key_id"], "revocation key_id")] = public
    return keys


_REQUEST_FIELDS = frozenset(
    {
        "pool_id",
        "seat",
        "project",
        "task",
        "operation",
        "key",
        "unit",
        "tax",
        "price_revision",
        "upper_bound",
        "depth",
        "wall_seconds",
    }
)


@dataclass(frozen=True)
class ReservationRequest:
    """One validated request to reserve exposure in a pool."""

    pool_id: str
    seat: str
    project: str
    task: str
    operation: str
    key: str
    unit: str
    tax: str
    price_revision: str
    upper_bound: Decimal
    depth: int
    wall_seconds: int

    def document(self) -> dict[str, object]:
        """Return the canonical request document (for digests)."""
        return {
            "pool_id": self.pool_id,
            "seat": self.seat,
            "project": self.project,
            "task": self.task,
            "operation": self.operation,
            "key": self.key,
            "unit": self.unit,
            "tax": self.tax,
            "price_revision": self.price_revision,
            "upper_bound": str(self.upper_bound),
            "depth": self.depth,
            "wall_seconds": self.wall_seconds,
        }


def validate_request(document: object) -> ReservationRequest:
    """Return the request when ``document`` has exactly the request shape.

    Raises
    ------
    SpendPoolError
        When a field is missing, extra or invalid, or the upper bound is zero.
    """
    data = _fields(document, _REQUEST_FIELDS, "reservation request")
    upper = quantity(data["upper_bound"], "upper_bound")
    if upper == 0:
        raise SpendPoolError("upper_bound must be a finite positive amount")
    depth = data["depth"]
    if isinstance(depth, bool) or not isinstance(depth, int) or not 0 <= depth <= MAX_LIMIT:
        raise SpendPoolError(f"depth must be an integer from 0 to {MAX_LIMIT}")
    if data["tax"] not in TAX_BASES:
        raise SpendPoolError(f"tax must be one of {', '.join(TAX_BASES)}")
    return ReservationRequest(
        pool_id=token(data["pool_id"], "pool_id"),
        seat=token(data["seat"], "seat"),
        project=token(data["project"], "project"),
        task=token(data["task"], "task"),
        operation=token(data["operation"], "operation"),
        key=token(data["key"], "key"),
        unit=token(data["unit"], "unit"),
        tax=str(data["tax"]),
        price_revision=token(data["price_revision"], "price_revision"),
        upper_bound=upper,
        depth=depth,
        wall_seconds=limit(data["wall_seconds"], "wall_seconds"),
    )


@dataclass
class Reservation:
    """A granted reservation and what has been settled against it."""

    reservation_id: str
    caller: str
    seat: str
    exposure: Decimal
    expires_at: datetime
    epoch: int
    settled: Decimal = Decimal(0)
    closed: bool = False
    overrun_open: bool = False
    usage: dict[str, tuple[Decimal, bool]] = field(default_factory=dict)

    def open_exposure(self) -> Decimal:
        """Return what this reservation still holds against the bound."""
        return Decimal(0) if self.closed else max(self.exposure - self.settled, Decimal(0))


@dataclass
class PoolState:
    """A pool's configuration history and reservations, folded from its ledger events."""

    config: PoolConfig | None = None
    revision: int = 0
    reservations: dict[str, Reservation] = field(default_factory=dict)
    revoked_epochs: set[int] = field(default_factory=set)

    def settled(self) -> Decimal:
        """Return every amount charged to the pool."""
        return sum((item.settled for item in self.reservations.values()), Decimal(0))

    def outstanding(self) -> Decimal:
        """Return exposure still held by open reservations, expired or not (C1, C3)."""
        return sum((item.open_exposure() for item in self.reservations.values()), Decimal(0))

    def overruns(self) -> tuple[str, ...]:
        """Return the reservations whose overrun still blocks new grants."""
        return tuple(sorted(r for r, item in self.reservations.items() if item.overrun_open))

    def active_seats(self, now: datetime) -> frozenset[str]:
        """Return seats holding an unexpired, open reservation."""
        return frozenset(
            item.seat
            for item in self.reservations.values()
            if not item.closed and item.expires_at > now
        )


def fold(events: Iterable[Mapping[str, Any]]) -> PoolState:
    """Fold one pool's recorded ledger events, oldest first, into its state."""
    state = PoolState()
    for event in events:
        kind = event["kind"]
        body = event["body"]
        if kind == "pool_config":
            state.config = validate_config(body)
            state.revision += 1
        elif kind == "grant":
            state.reservations[body["reservation_id"]] = Reservation(
                reservation_id=body["reservation_id"],
                caller=body["caller"],
                seat=body["seat"],
                exposure=Decimal(body["exposure"]),
                expires_at=timestamp(body["expires_at"], "expires_at"),
                epoch=int(body["epoch"]),
            )
        elif kind == "settlement":
            reservation = state.reservations[body["reservation_id"]]
            amount = Decimal(body["amount"])
            reservation.usage[body["usage_ref"]] = (amount, bool(body["final"]))
            reservation.settled += amount
            if reservation.settled > reservation.exposure:
                reservation.overrun_open = True
            if body["final"]:
                reservation.closed = True
        elif kind == "owner_revocation":
            state.revoked_epochs.add(int(body["revoked_epoch"]))
        elif kind == "reconciliation":
            reservation = state.reservations[body["reservation_id"]]
            reservation.settled = Decimal(body["amount"])
            reservation.closed = True
            reservation.overrun_open = False
    return state
