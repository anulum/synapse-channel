# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the pool owner's spend ledger decides and records every reservation (F02)
"""Real SQLite ledgers: the invariant, the review corrections C1-C4, idempotency and races."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.spend_ledger import NOT_ADMITTED, SpendLedger, SpendLedgerError

OWNER = "hub-owner"
PEER = "hub-peer"
T0 = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def _config(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "pool_id": "pool-a",
        "owner_hub_id": OWNER,
        "epoch": 1,
        "account_ref": "acct-ref-1",
        "billing_surface": "api",
        "unit": "USD",
        "window_starts_at": "2026-10-01T00:00:00Z",
        "window_ends_at": "2026-11-01T00:00:00Z",
        "price_revision": "price-1",
        "hard_bound": "100",
        "cost_basis": {"tax": "pre_tax", "fixed_fee": "1", "minimum_charge": "2"},
        "limits": {"max_depth": 2, "max_agents": 2, "max_wall_seconds": 3600},
        "grantees": [{"hub": PEER, "project": "PROJ"}],
        "cause": "initial pool",
    }
    document.update(overrides)
    return document


def _request(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "pool_id": "pool-a",
        "seat": "PROJ/alice",
        "project": "PROJ",
        "task": "t1",
        "operation": "call-1",
        "key": "k1",
        "unit": "USD",
        "tax": "pre_tax",
        "price_revision": "price-1",
        "upper_bound": "10",
        "depth": 1,
        "wall_seconds": 600,
    }
    document.update(overrides)
    return document


def _settle(reservation: str, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "pool_id": "pool-a",
        "reservation_id": reservation,
        "usage_ref": "u1",
        "amount": "5",
        "provenance": "billed",
        "final": True,
    }
    document.update(overrides)
    return document


def _ledger(tmp_path: Path, **config: Any) -> SpendLedger:
    home = tmp_path / "spend"
    home.mkdir(mode=0o700, exist_ok=True)
    ledger = SpendLedger(home / "ledger.sqlite3", owner_hub_id=OWNER)
    ledger.configure(_config(**config), now=T0)
    return ledger


def _reasons(ledger: SpendLedger) -> list[str]:
    return [e["body"]["reason"] for e in ledger.audit("pool-a") if e["kind"] == "refusal"]


def test_a_grant_reserves_fees_and_minimum_and_replays_by_key(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="1"), now=T0)
    assert grant["admitted"] is True
    assert grant["exposure"] == "3"  # max(1, minimum 2) + fixed fee 1
    assert grant["epoch"] == 1 and grant["config_revision"] == 1 and grant["unit"] == "USD"
    assert grant["expires_at"] == (T0 + timedelta(seconds=600)).isoformat()
    assert ledger.reserve(PEER, _request(upper_bound="1"), now=T0 + timedelta(seconds=5)) == grant
    conflict = ledger.reserve(PEER, _request(upper_bound="2"), now=T0)
    assert conflict == {"admitted": False, "reason": "idempotency_conflict"}
    query = {"pool_id": "pool-a", "seat": "PROJ/alice", "task": "t1", "operation": "call-1"}
    assert ledger.query(PEER, {**query, "key": "k1"}) == {"found": True, "response": grant}
    assert ledger.query(PEER, {**query, "key": "k9"}) == {"found": False}
    assert ledger.query("hub-other", {**query, "key": "k1"}) == {"found": False}
    assert ledger.query(PEER, {**query, "key": "bad key"}) == {"found": False}
    assert ledger.query(PEER, query) == {"found": False}
    status = ledger.status("pool-a", now=T0)
    assert (status["settled"], status["outstanding"], status["headroom"]) == ("0", "3", "97")
    assert status["active_agents"] == 1


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"pool_id": "pool-z"}, "unknown_pool"),
        ({"unit": "EUR"}, "unit_mismatch"),
        ({"tax": "post_tax"}, "tax_basis_mismatch"),
        ({"price_revision": "price-2"}, "price_revision_mismatch"),
        ({"depth": 3}, "depth_exceeded"),
        ({"wall_seconds": 3601}, "wall_time_exceeded"),
        ({"upper_bound": "100"}, "bound_exceeded"),
        ({"project": "OTHER"}, "caller_not_granted"),
    ],
)
def test_every_refusal_is_uniform_and_audited_in_detail(
    tmp_path: Path, change: dict[str, Any], reason: str
) -> None:
    ledger = _ledger(tmp_path)
    response = ledger.reserve(PEER, _request(**change), now=T0)
    assert response == {"admitted": False, "reason": NOT_ADMITTED}
    pool = change.get("pool_id", "pool-a")
    assert [e["body"]["reason"] for e in ledger.audit(pool) if e["kind"] == "refusal"] == [reason]


def test_owner_window_caller_and_malformed_requests_are_refused(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    assert ledger.reserve("hub-stranger", _request(), now=T0)["reason"] == NOT_ADMITTED
    late = T0 + timedelta(days=40)
    assert ledger.reserve(PEER, _request(key="k2"), now=late)["reason"] == NOT_ADMITTED
    assert ledger.reserve(PEER, {"pool_id": "pool-a"}, now=T0)["reason"] == NOT_ADMITTED
    assert ledger.reserve(PEER, _request(upper_bound="0"), now=T0)["reason"] == NOT_ADMITTED
    ledger.configure(_config(owner_hub_id="hub-new", epoch=2, cause="moved"), now=T0)
    assert ledger.reserve(PEER, _request(key="k3"), now=T0)["reason"] == NOT_ADMITTED
    assert _reasons(ledger) == ["caller_not_granted", "outside_window", "not_owner"]


def test_expiry_and_a_new_epoch_never_release_exposure(tmp_path: Path) -> None:
    """C1 and C3: unsettled exposure stays counted across expiry and an epoch change."""
    ledger = _ledger(tmp_path)
    first = ledger.reserve(PEER, _request(upper_bound="80"), now=T0)  # exposure 81
    assert first["admitted"] is True
    later = T0 + timedelta(hours=2)  # the grant has expired
    ledger.configure(_config(epoch=2, cause="failover to epoch 2"), now=later)
    refused = ledger.reserve(PEER, _request(key="k2", upper_bound="20"), now=later)
    assert refused["reason"] == NOT_ADMITTED
    assert _reasons(ledger) == ["bound_exceeded"]
    ledger.settle(PEER, _settle(str(first["reservation_id"]), amount="10"), now=later)
    granted = ledger.reserve(PEER, _request(key="k3", upper_bound="20"), now=later)
    assert granted["admitted"] is True and granted["epoch"] == 2
    with pytest.raises(SpendLedgerError, match="backwards"):
        ledger.configure(_config(epoch=1, cause="rollback"), now=later)


def test_lowering_the_bound_keeps_in_flight_grants_and_refuses_new_ones(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="30"), now=T0)
    ledger.configure(_config(hard_bound="20", cause="lower the bound"), now=T0)
    assert ledger.reserve(PEER, _request(key="k2", upper_bound="1"), now=T0)["admitted"] is False
    settled = ledger.settle(PEER, _settle(str(grant["reservation_id"]), amount="4"), now=T0)
    assert settled == {
        "settled": True,
        "reservation_id": grant["reservation_id"],
        "usage_ref": "u1",
        "overrun": False,
    }
    assert ledger.reserve(PEER, _request(key="k3", upper_bound="1"), now=T0)["admitted"] is True
    assert ledger.status("pool-a", now=T0)["revision"] == 2


def test_the_agent_limit_counts_distinct_live_seats(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    for seat, key in (("PROJ/alice", "a"), ("PROJ/bob", "b"), ("PROJ/alice", "a2")):
        assert ledger.reserve(PEER, _request(seat=seat, key=key), now=T0)["admitted"] is True
    assert ledger.reserve(PEER, _request(seat="PROJ/carol", key="c"), now=T0)["admitted"] is False
    later = T0 + timedelta(hours=2)
    assert ledger.reserve(PEER, _request(seat="PROJ/carol", key="c2"), now=later)["admitted"]
    assert _reasons(ledger) == ["agents_exceeded"]


def test_settlements_are_idempotent_and_an_overrun_blocks_until_reconciled(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="10"), now=T0)  # exposure 11
    rid = str(grant["reservation_id"])
    partial = ledger.settle(PEER, _settle(rid, amount="6", final=False), now=T0)
    assert partial["overrun"] is False
    assert ledger.settle(PEER, _settle(rid, amount="6", final=False), now=T0) == partial
    conflict = ledger.settle(PEER, _settle(rid, amount="7", final=False), now=T0)
    assert conflict == {"settled": False, "reason": "idempotency_conflict"}
    over = ledger.settle(PEER, _settle(rid, usage_ref="u2", amount="9"), now=T0)
    assert over["overrun"] is True
    status = ledger.status("pool-a", now=T0)
    assert status["overruns"] == [rid] and status["settled"] == "15"
    assert ledger.reserve(PEER, _request(key="k2", upper_bound="1"), now=T0)["admitted"] is False
    assert ledger.settle(PEER, _settle(rid, usage_ref="u3"), now=T0)["settled"] is False
    assert ledger.settle("hub-other", _settle(rid, usage_ref="u4"), now=T0)["settled"] is False
    assert ledger.settle(PEER, _settle("rsv-unknown", usage_ref="u5"), now=T0)["settled"] is False
    assert ledger.settle(PEER, {"pool_id": "pool-a"}, now=T0)["reason"] == NOT_ADMITTED
    assert ledger.settle(PEER, _settle(rid, provenance="guess"), now=T0)["settled"] is False
    reconcile = {
        "pool_id": "pool-a",
        "reservation_id": rid,
        "amount": "15",
        "evidence_ref": "invoice-7",
        "cause": "provider invoice",
    }
    assert ledger.reconcile(reconcile, now=T0) == {"reconciled": True, "reservation_id": rid}
    assert ledger.status("pool-a", now=T0)["overruns"] == []
    assert ledger.reserve(PEER, _request(key="k3", upper_bound="1"), now=T0)["admitted"] is True
    with pytest.raises(SpendLedgerError, match="already closed"):
        ledger.reconcile(reconcile, now=T0)
    assert _reasons(ledger).count("settlement_not_accepted") == 3


def test_an_expired_unsettled_grant_is_reconciled_by_the_operator(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="90"), now=T0)
    later = T0 + timedelta(hours=3)
    assert ledger.status("pool-a", now=later)["outstanding"] == "91"
    base = {"pool_id": "pool-a", "reservation_id": grant["reservation_id"]}
    ledger.reconcile(
        {**base, "amount": "0", "evidence_ref": "call-cancelled", "cause": "cancelled"},
        now=later,
    )
    assert ledger.status("pool-a", now=later)["headroom"] == "100"
    for bad, match in (
        ({"pool_id": "pool-a"}, "documented fields"),
        ({**base, "amount": "x", "evidence_ref": "e", "cause": "c"}, "decimal string"),
        ({**base, "amount": "1", "evidence_ref": "e", "cause": " "}, "needs a cause"),
        (
            {
                **base,
                "reservation_id": "rsv-none",
                "amount": "1",
                "evidence_ref": "e",
                "cause": "c",
            },
            "no such reservation",
        ),
    ):
        with pytest.raises(SpendLedgerError, match=match):
            ledger.reconcile(bad, now=later)


def test_concurrent_owners_never_grant_past_the_bound(tmp_path: Path) -> None:
    """Many threads, each with its own ledger object on the same file, race for headroom."""
    _ledger(
        tmp_path,
        hard_bound="50",
        limits={"max_depth": 2, "max_agents": 99, "max_wall_seconds": 3600},
    )
    path = tmp_path / "spend" / "ledger.sqlite3"
    results: list[dict[str, object]] = []
    lock = threading.Lock()

    def race(index: int) -> None:
        ledger = SpendLedger(path, owner_hub_id=OWNER)
        response = ledger.reserve(
            PEER, _request(seat=f"PROJ/s{index}", key=f"k{index}", upper_bound="9"), now=T0
        )
        with lock:
            results.append(response)

    threads = [threading.Thread(target=race, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    granted = [r for r in results if r["admitted"]]
    assert len(results) == 12
    assert len(granted) == 5  # 5 * (9 + 1 fee) = 50
    assert len({r["sequence"] for r in granted}) == 5
    status = SpendLedger(path, owner_hub_id=OWNER).status("pool-a", now=T0)
    assert status["outstanding"] == "50" and status["headroom"] == "0"


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"epoch": 0}, "positive integer"),
        ({"cost_basis": {"tax": "none", "fixed_fee": "0", "minimum_charge": "0"}}, "tax must be"),
        (
            {"cost_basis": {"tax": "not_applicable", "fixed_fee": "0", "minimum_charge": "0"}},
            "monetary pool",
        ),
        ({"window_ends_at": "2026-10-01T00:00:00Z"}, "end after"),
        ({"window_ends_at": "2026-11-01T00:00:00"}, "UTC offset"),
        ({"window_ends_at": "soon"}, "ISO-8601"),
        ({"window_starts_at": 5}, "ISO-8601"),
        ({"grantees": []}, "1 to 256"),
        ({"grantees": [{"hub": PEER}]}, "exactly the fields"),
        ({"cause": "  "}, "non-empty reason"),
        ({"hard_bound": "-1"}, "non-negative"),
        ({"hard_bound": "NaN"}, "non-negative"),
        ({"hard_bound": 5}, "decimal string"),
        ({"hard_bound": "lots"}, "decimal string"),
        ({"limits": {"max_depth": 0, "max_agents": 1, "max_wall_seconds": 1}}, "integer from 1"),
        ({"pool_id": "bad id"}, "plain token"),
        ({"extra": True}, "exactly the fields"),
    ],
)
def test_an_invalid_configuration_is_refused(
    tmp_path: Path, change: dict[str, Any], match: str
) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(SpendLedgerError, match=match):
        ledger.configure(_config(**change), now=T0)


def test_a_non_monetary_pool_may_declare_no_tax_basis(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        pool_id="gpu",
        unit="gpu_seconds",
        cost_basis={"tax": "not_applicable", "fixed_fee": "0", "minimum_charge": "0"},
    )
    grant = ledger.reserve(
        PEER, _request(pool_id="gpu", unit="gpu_seconds", tax="not_applicable"), now=T0
    )
    assert grant["admitted"] is True and grant["exposure"] == "10"


def test_the_request_shape_is_exact(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    for change in ({"depth": -1}, {"depth": True}, {"tax": "gross"}, {"wall_seconds": 0}):
        assert ledger.reserve(PEER, _request(**change), now=T0)["reason"] == NOT_ADMITTED
    assert _reasons(ledger) == []  # malformed requests reveal and record nothing


def test_the_store_is_owner_only_versioned_and_needs_aware_times(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    with pytest.raises(SpendLedgerError):
        SpendLedger(shared / "ledger.sqlite3", owner_hub_id=OWNER)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    (home / "real").write_text("", encoding="utf-8")
    (home / "link.sqlite3").symlink_to(home / "real")
    with pytest.raises(SpendLedgerError, match="symlink"):
        SpendLedger(home / "link.sqlite3", owner_hub_id=OWNER)
    foreign = home / "foreign.sqlite3"
    with sqlite3.connect(foreign) as connection:
        connection.execute("CREATE TABLE other (x)")
    foreign.chmod(0o600)
    with pytest.raises(SpendLedgerError, match="unversioned"):
        SpendLedger(foreign, owner_hub_id=OWNER)
    future = home / "future.sqlite3"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version=9")
    future.chmod(0o600)
    with pytest.raises(SpendLedgerError, match="unsupported"):
        SpendLedger(future, owner_hub_id=OWNER)
    junk = home / "junk.sqlite3"
    junk.write_bytes(b"not a database at all" * 64)
    junk.chmod(0o600)
    with pytest.raises(SpendLedgerError, match="not a valid database"):
        SpendLedger(junk, owner_hub_id=OWNER)
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)  # owner-only, but not writable: SQLite cannot create the file
    with pytest.raises(SpendLedgerError, match="cannot open"):
        SpendLedger(locked / "ledger.sqlite3", owner_hub_id=OWNER)
    missing = tmp_path / "missing" / "ledger.sqlite3"
    with pytest.raises(SpendLedgerError):
        SpendLedger(missing, owner_hub_id=OWNER)
    ledger = _ledger(tmp_path)
    naive = datetime(2026, 10, 1, 12)
    calls: list[Callable[[], object]] = [
        lambda: ledger.reserve(PEER, _request(), now=naive),
        lambda: ledger.status("pool-a", now=naive),
        lambda: ledger.configure(_config(), now=naive),
    ]
    for call in calls:
        with pytest.raises(SpendLedgerError, match="UTC offset"):
            call()
    with pytest.raises(SpendLedgerError, match="no such pool"):
        ledger.status("pool-z", now=T0)
    with pytest.raises(SpendLedgerError, match="exactly the fields"):
        ledger.configure({"pool_id": "x"}, now=T0)
