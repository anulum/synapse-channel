# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the fleet advertisement carries buckets and an alias, never private facts
"""Tests for :mod:`synapse_channel.core.entitlement_advert` over a realistic private ledger."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from synapse_channel.core.entitlement_advert import (
    EntitlementAdvertError,
    build_advert,
    remaining_bucket,
    validate_advert,
    window_ref,
)

AT = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
COMMON = {
    "recorded_at": "2026-09-19T12:00:00Z",
    "source": "operator:owner",
    "confidence": "operator",
}
PRIVATE_STRINGS = ("account-1", "Private Label", "pool-1", "window-1", "ref:vault/secret", "host")


def ledger() -> list[dict[str, object]]:
    """A private ledger: one account, one pool with a current and a past window."""
    return [
        {
            **COMMON,
            "event_id": "a1",
            "kind": "account",
            "account_id": "account-1",
            "label": "Private Label",
            "status": "active",
            "credential_ref": "ref:vault/secret",
        },
        {
            **COMMON,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "pool-1",
            "account_id": "account-1",
            "unit": "gpu_seconds",
            "resource_kind": "gpu_time",
            "capabilities": ["code", "long-context"],
            "data_classes": ["internal"],
            "eligible_projects": ["SYNAPSE-CHANNEL"],
        },
        {
            **COMMON,
            "event_id": "w0",
            "kind": "window",
            "window_id": "window-0",
            "pool_id": "pool-1",
            "starts_at": "2026-09-18T00:00:00Z",
            "ends_at": "2026-09-19T00:00:00Z",
            "grant": "100",
            "unit": "gpu_seconds",
            "price_revision": "price-1",
        },
        {
            **COMMON,
            "event_id": "w1",
            "kind": "window",
            "window_id": "window-1",
            "pool_id": "pool-1",
            "starts_at": "2026-09-19T00:00:00Z",
            "ends_at": "2026-09-20T00:00:00Z",
            "grant": "100",
            "unit": "gpu_seconds",
            "price_revision": "price-1",
        },
        {
            **COMMON,
            "event_id": "b1",
            "kind": "balance",
            "window_id": "window-1",
            "window_event_id": "w1",
            "source_event_id": "host-balance-1",
            "remaining": "30",
            "observed_at": "2026-09-19T10:00:00Z",
            "source": "official:host",
            "confidence": "official",
        },
    ]


def test_the_advertisement_is_redacted_and_bucketed() -> None:
    advert = build_advert(ledger(), pool_id="pool-1", alias="gpu-a", as_of=AT)
    text = json.dumps(advert)
    for private in PRIVATE_STRINGS:
        assert private not in text, private
    assert all("remaining" not in window and "grant" not in window for window in advert["windows"])
    assert advert["pool_alias"] == "gpu-a"
    assert advert["capabilities"] == ["code", "long-context"]
    assert advert["account_usable"] is True
    past, current = advert["windows"]
    assert current["current"] is True
    assert current["remaining_bucket"] == "10-50%"
    assert current["balance_evidence"] == "observed"
    assert current["window_ref"] == window_ref("gpu-a", "window-1")
    assert past["current"] is False and past["remaining_bucket"] == "unknown"
    assert past["balance_evidence"] == "outside_window"
    assert validate_advert(copy.deepcopy(advert)) == advert


def test_the_builder_refuses_an_unknown_pool_or_a_bad_alias() -> None:
    with pytest.raises(EntitlementAdvertError, match="no such pool"):
        build_advert(ledger(), pool_id="pool-9", alias="a", as_of=AT)
    with pytest.raises(EntitlementAdvertError, match="alias must be"):
        build_advert(ledger(), pool_id="pool-1", alias="bad alias", as_of=AT)


@pytest.mark.parametrize(
    ("remaining", "grant", "bucket"),
    [
        ("0", "100", "depleted"),
        ("5", "100", "<10%"),
        ("10", "100", "10-50%"),
        ("50", "100", "10-50%"),
        ("51", "100", ">50%"),
        (None, "100", "unknown"),
        ("5", "0", "unknown"),
        ("x", "100", "unknown"),
        ("NaN", "100", "unknown"),
    ],
)
def test_remaining_buckets(remaining: object, grant: object, bucket: str) -> None:
    assert remaining_bucket(remaining, grant) == bucket


def _mutations() -> list[tuple[str, Any]]:
    return [
        ("extra top-level field", lambda a: a.update(pool_id="pool-1")),
        ("wrong schema", lambda a: a.update(schema=2)),
        ("bad alias", lambda a: a.update(pool_alias="a b")),
        ("bad unit", lambda a: a.update(unit="tokens and more")),
        ("bad resource kind", lambda a: a.update(resource_kind=7)),
        ("list not a list", lambda a: a.update(capabilities="code")),
        ("too many items", lambda a: a.update(capabilities=["c"] * 33)),
        ("bad list item", lambda a: a.update(data_classes=["with space"])),
        ("usable not bool", lambda a: a.update(account_usable="yes")),
        ("naive time", lambda a: a.update(as_of="2026-09-19T12:00:00")),
        ("not a time", lambda a: a.update(as_of="yesterday")),
        ("long time", lambda a: a.update(as_of="2" * 41)),
        ("windows not list", lambda a: a.update(windows={})),
        ("too many windows", lambda a: a.update(windows=a["windows"] * 9)),
        ("window extra field", lambda a: a["windows"][0].update(remaining="30")),
        ("bad window ref", lambda a: a["windows"][0].update(window_ref="window-1")),
        ("current not bool", lambda a: a["windows"][0].update(current=1)),
        ("bad bucket", lambda a: a["windows"][0].update(remaining_bucket="30%")),
        ("bad evidence", lambda a: a["windows"][0].update(balance_evidence="x")),
        ("negative age", lambda a: a["windows"][0].update(observation_age_seconds=-1)),
        ("bool age", lambda a: a["windows"][0].update(observation_age_seconds=True)),
        ("inf age", lambda a: a["windows"][0].update(observation_age_seconds=float("inf"))),
        ("window not mapping", lambda a: a.update(windows=["w"])),
    ]


@pytest.mark.parametrize(("label", "mutate"), _mutations(), ids=[m[0] for m in _mutations()])
def test_the_validator_accepts_only_the_exact_shape(label: str, mutate: Any) -> None:
    advert = build_advert(ledger(), pool_id="pool-1", alias="gpu-a", as_of=AT)
    mutate(advert)
    with pytest.raises(EntitlementAdvertError):
        validate_advert(advert)
    with pytest.raises(EntitlementAdvertError):
        validate_advert("not a mapping")


def test_a_plain_pool_advertises_without_compute_metadata() -> None:
    events = [event for event in ledger() if event["kind"] != "pool"]
    events.insert(
        1,
        {
            **COMMON,
            "event_id": "p1",
            "kind": "pool",
            "pool_id": "pool-1",
            "account_id": "account-1",
            "unit": "gpu_seconds",
        },
    )
    advert = build_advert(events, pool_id="pool-1", alias="plain", as_of=AT)
    assert advert["resource_kind"] is None
    assert advert["capabilities"] == [] and advert["eligible_projects"] == []
