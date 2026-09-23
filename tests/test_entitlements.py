# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — entitlement event contract tests
"""Exercise the public entitlement event and correction contract."""

from __future__ import annotations

from copy import deepcopy

import pytest

from synapse_channel.core.entitlements import (
    EntitlementError,
    active_events,
    parse_quantity,
    parse_time,
    validate_event,
)


def _event(kind: str, event_id: str, **fields: object) -> dict[str, object]:
    return {
        "event_id": event_id,
        "kind": kind,
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:account-owner",
        "confidence": "operator",
        **fields,
    }


def _ledger() -> list[dict[str, object]]:
    return [
        _event("account", "a1", account_id="account-1", label="Private Alpha", status="active"),
        _event("pool", "p1", pool_id="pool-1", account_id="account-1", unit="tokens"),
        _event(
            "surface",
            "s1",
            surface_id="chat-1",
            account_id="account-1",
            pool_id="pool-1",
            product="Chat",
            channel="chat",
        ),
        _event(
            "surface",
            "s2",
            surface_id="cli-1",
            account_id="account-1",
            pool_id="pool-1",
            product="Coding CLI",
            channel="coding_cli",
        ),
        _event(
            "window",
            "w1",
            window_id="window-1",
            pool_id="pool-1",
            starts_at="2026-09-19T00:00:00+02:00",
            ends_at="2026-09-20T00:00:00+02:00",
            grant="1000",
            unit="tokens",
            price_revision="price-1",
        ),
    ]


def test_shared_pool_and_timezone_boundaries() -> None:
    events = _ledger()
    events.append(
        _event(
            "usage",
            "u1",
            window_id="window-1",
            window_event_id="w1",
            source_event_id="host-1",
            amount="11.5",
            observed_at="2026-09-18T22:00:00Z",
        )
    )
    assert len(active_events(events)) == 6
    assert parse_time("2026-09-19T00:00:00+02:00", "start") == parse_time(
        "2026-09-18T22:00:00Z", "start"
    )
    events[-1]["observed_at"] = "2026-09-19T22:00:00Z"
    with pytest.raises(EntitlementError, match="inside its quota window"):
        active_events(events)


def test_manual_correction_preserves_old_fact_and_source() -> None:
    events = _ledger()
    events.append(
        _event(
            "balance",
            "b1",
            window_id="window-1",
            window_event_id="w1",
            source_event_id="host-1",
            remaining="800",
            observed_at="2026-09-19T01:00:00Z",
        )
    )
    replacement = {
        **events[-1],
        "event_id": "b2",
        "recorded_at": "2026-09-19T12:00:00Z",
        "source": "operator:correction",
        "remaining": "790",
        "supersedes": "b1",
    }
    events.append(replacement)
    projected = active_events(events)
    assert len(events) == 7
    assert next(item for item in projected if item["kind"] == "balance")["remaining"] == "790"
    assert events[-2]["remaining"] == "800"
    events[-1] = {**replacement, "window_id": "another-window"}
    with pytest.raises(EntitlementError, match="correction must retain"):
        active_events(events)


def test_duplicate_source_id_and_incompatible_unit_refused() -> None:
    events = _ledger()
    events.append(
        _event(
            "usage",
            "u1",
            window_id="window-1",
            window_event_id="w1",
            source_event_id="same-transaction",
            amount="10",
            observed_at="2026-09-19T01:00:00Z",
        )
    )
    events.append(
        _event(
            "balance",
            "b1",
            window_id="window-1",
            window_event_id="w1",
            source_event_id="same-transaction",
            remaining="900",
            observed_at="2026-09-19T02:00:00Z",
        )
    )
    with pytest.raises(EntitlementError, match="source observation id reused"):
        active_events(events)
    events.pop()
    changed = deepcopy(events)
    changed[4]["unit"] = "USD"
    with pytest.raises(EntitlementError, match="pool's unit"):
        active_events(changed)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("confidence", [], "confidence"),
        ("kind", "unknown", "kind"),
        ("status", [], "account status"),
        ("credential_ref", "plaintext-key", "external reference"),
        ("recorded_at", "2026-09-19T10:00:00", "UTC offset"),
        ("recorded_at", 123, "timestamp"),
        ("supersedes", "a1", "supersede itself"),
        ("unexpected", "secret", "unexpected fields"),
    ],
)
def test_invalid_account_facts_refused(field: str, value: object, message: str) -> None:
    entry = _ledger()[0]
    entry[field] = value
    with pytest.raises(EntitlementError, match=message):
        validate_event(entry)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", 1.25, "no-price"])
def test_non_decimal_or_unbounded_quantity_refused(value: object) -> None:
    with pytest.raises(EntitlementError, match="decimal|finite"):
        parse_quantity(value, "grant")


@pytest.mark.parametrize(
    ("index", "changes", "message"),
    [
        (0, {"event_id": "bad id"}, "event_id"),
        (0, {"source": "\nsecret"}, "source"),
        (0, {"expires_at": "yesterday"}, "expires_at"),
        (1, {"unit": "bad unit"}, "unit"),
        (2, {"channel": "bad channel"}, "channel"),
        (4, {"ends_at": "2026-09-18T00:00:00Z"}, "window end"),
        (4, {"grant": "1e99"}, "grant"),
        (4, {"renewal_at": "local time"}, "renewal_at"),
    ],
)
def test_invalid_identity_time_and_units_are_refused(
    index: int, changes: dict[str, object], message: str
) -> None:
    record = {**_ledger()[index], **changes}
    with pytest.raises(EntitlementError, match=message):
        validate_event(record)


def test_cross_references_and_correction_chronology_are_enforced() -> None:
    events = _ledger()
    events[1]["account_id"] = "missing"
    with pytest.raises(EntitlementError, match="unknown account"):
        active_events(events)
    events = _ledger()
    events[2]["pool_id"] = "missing"
    with pytest.raises(EntitlementError, match="surface must belong"):
        active_events(events)
    events = _ledger()
    events.append({**events[4], "event_id": "w2", "window_id": "window-2"})
    with pytest.raises(EntitlementError, match="must not overlap"):
        active_events(events)
    events = _ledger()
    events.append({**events[0], "event_id": "a2", "supersedes": "a1"})
    with pytest.raises(EntitlementError, match="recorded after"):
        active_events(events)
    events = _ledger()
    events.append({**events[0], "event_id": "a2"})
    with pytest.raises(EntitlementError, match="requires explicit correction"):
        active_events(events)
    events[-1]["supersedes"] = "missing"
    with pytest.raises(EntitlementError, match="active earlier event"):
        active_events(events)


def test_observation_revision_and_balance_conflicts_are_enforced() -> None:
    events = _ledger()
    balance = _event(
        "balance",
        "b1",
        window_id="window-1",
        window_event_id="w1",
        source_event_id="host-1",
        remaining="800",
        observed_at="2026-09-19T01:00:00Z",
    )
    for changes, message in (
        ({"window_event_id": "missing"}, "unknown window revision"),
        ({"remaining": "1001"}, "exceeds window grant"),
    ):
        with pytest.raises(EntitlementError, match=message):
            active_events([*events, {**balance, **changes}])
    second = {**balance, "event_id": "b2", "source_event_id": "host-2"}
    with pytest.raises(EntitlementError, match="duplicate balance timestamp"):
        active_events([*events, balance, second])
    with pytest.raises(EntitlementError, match="duplicate event id"):
        active_events([*events, {**balance, "event_id": "a1"}])


@pytest.mark.parametrize(
    ("resource_kind", "unit"),
    [
        ("gpu_time", "gpu_seconds"),
        ("quantum_shots", "shots"),
        ("quantum_credits", "quantum_credits"),
        ("ci_minutes", "ci_minutes"),
        ("cloud_grant", "USD"),
    ],
)
def test_compute_pool_units_and_restrictions(resource_kind: str, unit: str) -> None:
    pool = _event(
        "pool",
        "p-compute",
        pool_id="compute",
        account_id="account-1",
        unit=unit,
        resource_kind=resource_kind,
        capabilities=["run"],
        data_classes=["internal"],
        eligible_projects=["SYNAPSE-CHANNEL"],
        idle_cost={"amount_per_hour": "0.25", "currency": "CHF"},
    )
    assert validate_event(pool)["resource_kind"] == resource_kind
    assert len(active_events([_ledger()[0], pool])) == 2
    with pytest.raises(EntitlementError, match="incompatible"):
        validate_event({**pool, "unit": "tokens"})
    with pytest.raises(EntitlementError, match="duplicates"):
        validate_event({**pool, "eligible_projects": ["A", "A"]})
    with pytest.raises(EntitlementError, match="idle_cost currency"):
        validate_event({**pool, "idle_cost": {"amount_per_hour": "1", "currency": "BTC"}})
    with pytest.raises(EntitlementError, match="requires resource_kind"):
        validate_event({key: value for key, value in pool.items() if key != "resource_kind"})
