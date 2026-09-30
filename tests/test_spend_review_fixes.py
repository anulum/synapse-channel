# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — F02 review findings R1, R2 and R4 on real ledgers
"""Exact arithmetic (R1), expiry at the use boundary (R2), and window scope (R4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.spend_epoch import SpendEpochError, usable_grant
from synapse_channel.core.spend_ledger import NOT_ADMITTED, SpendLedger, SpendLedgerError
from test_spend_ledger import OWNER, PEER, T0, _config, _ledger, _request, _settle

NEXT_WINDOW = {
    "window_starts_at": "2026-11-01T00:00:00Z",
    "window_ends_at": "2026-12-01T00:00:00Z",
}
IN_NEXT = T0 + timedelta(days=31)


def _reasons(ledger: SpendLedger) -> list[str]:
    return [e["body"]["reason"] for e in ledger.audit("pool-a") if e["kind"] == "refusal"]


def test_a_fee_that_default_precision_would_round_away_is_counted(tmp_path: Path) -> None:
    """R1: at precision 28, 100000000000 + 1E-18 rounds to the bound; exactly, it exceeds it."""
    ledger = _ledger(
        tmp_path,
        hard_bound="100000000000",
        cost_basis={"tax": "pre_tax", "fixed_fee": "0.000000000000000001", "minimum_charge": "0"},
    )
    refused = ledger.reserve(PEER, _request(upper_bound="100000000000"), now=T0)
    assert refused == {"admitted": False, "reason": NOT_ADMITTED}
    assert _reasons(ledger) == ["bound_exceeded"]
    fits = ledger.reserve(
        PEER, _request(key="k2", upper_bound="99999999999.999999999999999999"), now=T0
    )
    assert fits["admitted"] is True and fits["exposure"] == "100000000000.000000000000000000"
    status = SpendLedger(ledger.path, owner_hub_id=OWNER).status("pool-a", now=T0)  # reopened
    assert Decimal(str(status["headroom"])) == 0  # exactly zero, not a rounded remainder


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("1E-28", "18 decimals"),
        ("1" * 31, "at most 30 digits"),
        ("1E+19", "magnitude below"),
        ("0." + "0" * 17 + "12", "18 decimals"),
    ],
)
def test_quantities_outside_the_exact_domain_are_refused(
    tmp_path: Path, value: str, match: str
) -> None:
    ledger = _ledger(tmp_path)
    with pytest.raises(SpendLedgerError, match=match):
        ledger.configure(_config(hard_bound=value, cause="out of domain"), now=T0)
    assert ledger.reserve(PEER, _request(upper_bound=value), now=T0)["reason"] == NOT_ADMITTED


def test_fine_settlements_aggregate_exactly_across_a_reopen(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="10"), now=T0)
    for index in range(7):
        ledger.settle(
            PEER,
            _settle(
                str(grant["reservation_id"]),
                usage_ref=f"u{index}",
                amount="0.000000000000000001",
                final=False,
            ),
            now=T0,
        )
    status = SpendLedger(ledger.path, owner_hub_id=OWNER).status("pool-a", now=T0)
    assert status["settled"] == "7E-18"
    assert status["outstanding"] == "10.999999999999999993"


def test_a_legacy_row_outside_the_domain_fails_closed(tmp_path: Path) -> None:
    """A 0.99.35 ledger could hold an unbounded amount; evaluating it must refuse, not round."""
    ledger = _ledger(tmp_path)
    with sqlite3.connect(ledger.path) as connection:
        body = {
            "reservation_id": "rsv-legacy",
            "caller": PEER,
            "seat": "PROJ/old",
            "exposure": "1E+200",
            "epoch": 1,
            "expires_at": "2026-10-01T13:00:00+00:00",
        }
        connection.execute(
            "INSERT INTO events (pool_id, kind, recorded_at, body) VALUES (?, ?, ?, ?)",
            ("pool-a", "grant", T0.isoformat(), json.dumps(body)),
        )
    with pytest.raises(SpendLedgerError, match="cannot evaluate exactly|round"):
        ledger.status("pool-a", now=T0)
    with pytest.raises(SpendLedgerError, match="cannot evaluate exactly|round"):
        ledger.reserve(PEER, _request(key="k9"), now=T0)
    legacy_config = _config(hard_bound="1E-28")
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "INSERT INTO events (pool_id, kind, recorded_at, body) VALUES (?, ?, ?, ?)",
            (
                "pool-b",
                "pool_config",
                T0.isoformat(),
                json.dumps({**legacy_config, "pool_id": "pool-b"}),
            ),
        )
    with pytest.raises(SpendLedgerError, match="cannot evaluate exactly"):
        ledger.checkpoint("pool-b")


def test_an_expired_grant_is_never_usable_to_start_work(tmp_path: Path) -> None:
    """R2: expiry blocks new consumption even when the answer is replayed or queried later."""
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(wall_seconds=60), now=T0)
    late = T0 + timedelta(seconds=120)
    replay = ledger.reserve(PEER, _request(wall_seconds=60), now=late)
    assert replay == grant  # idempotent: the stored answer, byte for byte
    assert usable_grant(grant, [], now=T0) is True
    assert usable_grant(grant, [], now=T0, min_remaining_seconds=59) is True
    assert usable_grant(grant, [], now=T0, min_remaining_seconds=60) is False  # skew margin
    assert usable_grant(replay, [], now=late) is False
    assert ledger.status("pool-a", now=late)["outstanding"] == "11"  # expiry keeps exposure (C3)
    assert usable_grant({**grant, "expires_at": "soon"}, [], now=T0) is False
    assert (
        usable_grant({key: v for key, v in grant.items() if key != "expires_at"}, [], now=T0)
        is False
    )
    with pytest.raises(SpendEpochError, match="UTC offset"):
        usable_grant(grant, [], now=T0.replace(tzinfo=None))
    with pytest.raises(SpendEpochError, match="non-negative"):
        usable_grant(grant, [], now=T0, min_remaining_seconds=float("nan"))


def test_a_new_window_has_its_own_bound_keys_and_keeps_old_open_exposure(tmp_path: Path) -> None:
    """R4: idempotency and settled usage are per window; unresolved exposure carries over."""
    ledger = _ledger(tmp_path)
    spent = ledger.reserve(PEER, _request(key="k1", upper_bound="50"), now=T0)
    ledger.settle(PEER, _settle(str(spent["reservation_id"]), amount="45"), now=T0)
    held = ledger.reserve(PEER, _request(seat="PROJ/bob", key="k2", upper_bound="20"), now=T0)
    ledger.configure(_config(**NEXT_WINDOW, cause="next month"), now=IN_NEXT)

    reused = ledger.reserve(PEER, _request(key="k1", upper_bound="70"), now=IN_NEXT)
    assert reused["admitted"] is True and reused["reservation_id"] != spent["reservation_id"]
    status = ledger.status("pool-a", now=IN_NEXT)
    assert status["settled"] == "0"  # October's 45 is not charged to November
    assert status["outstanding"] == str(21 + 71)  # October's open grant still counts
    assert (
        ledger.reserve(PEER, _request(key="k3", upper_bound="9"), now=IN_NEXT)["admitted"] is False
    )
    query = {
        "pool_id": "pool-a",
        "seat": "PROJ/bob",
        "task": "t1",
        "operation": "call-1",
        "key": "k2",
    }
    assert ledger.query(PEER, query) == {"found": False}  # scoped to its own window
    old = ledger.settle(
        PEER, _settle(str(held["reservation_id"]), usage_ref="u9", amount="3"), now=IN_NEXT
    )
    assert old["settled"] is True
    assert ledger.status("pool-a", now=IN_NEXT)["outstanding"] == "71"


def test_the_pool_identity_cannot_change_under_unresolved_reservations(tmp_path: Path) -> None:
    """R4: no silent unit or billing change while old amounts are still open."""
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(), now=T0)
    for change in (
        {"unit": "EUR"},
        {"billing_surface": "batch"},
        {"account_ref": "acct-ref-2"},
        {"cost_basis": {"tax": "post_tax", "fixed_fee": "1", "minimum_charge": "2"}},
    ):
        with pytest.raises(SpendLedgerError, match="unresolved"):
            ledger.configure(_config(cause="change", **change), now=T0)
    ledger.configure(
        _config(hard_bound="90", cause="lower only"), now=T0
    )  # other fields may change
    ledger.settle(PEER, _settle(str(grant["reservation_id"])), now=T0)
    assert ledger.configure(_config(unit="EUR", cause="now resolved"), now=T0)["revision"] == 3


def test_a_legacy_grant_without_a_window_counts_in_the_current_window(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="30"), now=T0)
    ledger.settle(PEER, _settle(str(grant["reservation_id"]), amount="30"), now=T0)
    with sqlite3.connect(ledger.path) as connection:
        row = connection.execute("SELECT seq, body FROM events WHERE kind = 'grant'").fetchone()
        body: dict[str, Any] = json.loads(row[1])
        body.pop("window")
        connection.execute("UPDATE events SET body = ? WHERE seq = ?", (json.dumps(body), row[0]))
    ledger.configure(_config(**NEXT_WINDOW, cause="next month"), now=IN_NEXT)
    assert ledger.status("pool-a", now=IN_NEXT)["settled"] == "30"  # conservative
