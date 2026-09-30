# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — F02 review findings R1, R2 and R4 on real ledgers
"""Exact arithmetic (R1), expiry at the use boundary (R2), window scope and migration (R4)."""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from decimal import Context, Decimal, localcontext
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


def test_a_legacy_grant_without_a_window_belongs_to_the_window_it_was_decided_in(
    tmp_path: Path,
) -> None:
    ledger = _ledger(tmp_path)
    grant = ledger.reserve(PEER, _request(upper_bound="30"), now=T0)
    ledger.settle(PEER, _settle(str(grant["reservation_id"]), amount="30"), now=T0)
    with sqlite3.connect(ledger.path) as connection:
        row = connection.execute("SELECT seq, body FROM events WHERE kind = 'grant'").fetchone()
        body: dict[str, Any] = json.loads(row[1])
        body.pop("window")
        connection.execute("UPDATE events SET body = ? WHERE seq = ?", (json.dumps(body), row[0]))
    assert ledger.status("pool-a", now=T0)["settled"] == "30"
    ledger.configure(_config(**NEXT_WINDOW, cause="next month"), now=IN_NEXT)
    assert ledger.status("pool-a", now=IN_NEXT)["settled"] == "0"  # October's charge


@pytest.mark.parametrize(
    ("configured", "match"),
    [(False, "precedes its pool"), (True, "KeyError")],
)
def test_a_malformed_grant_record_fails_closed(
    tmp_path: Path, configured: bool, match: str
) -> None:
    """A grant before any configuration, or one missing fields, refuses the pool."""
    home = tmp_path / "spend"
    home.mkdir(mode=0o700)
    ledger = SpendLedger(home / "ledger.sqlite3", owner_hub_id=OWNER)
    if configured:
        ledger.configure(_config(), now=T0)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            "INSERT INTO events (pool_id, kind, recorded_at, body) VALUES (?, ?, ?, ?)",
            ("pool-a", "grant", T0.isoformat(), json.dumps({"reservation_id": "rsv-x"})),
        )
    with pytest.raises(SpendLedgerError, match=match):
        ledger.status("pool-a", now=T0)


TINY = Context(prec=3)  # an ambient context that rounds almost everything
BIG = "100000000000"
TINY_FEE = "0.000000000000000001"


def test_headroom_is_exact_even_under_a_rounding_ambient_context(tmp_path: Path) -> None:
    """Review follow-up F2: the sign of an operand must not round outside EXACT."""
    ledger = _ledger(
        tmp_path,
        hard_bound=BIG + ".000000000000000001",
        cost_basis={"tax": "pre_tax", "fixed_fee": TINY_FEE, "minimum_charge": "0"},
    )
    with localcontext(TINY):
        grant = ledger.reserve(PEER, _request(upper_bound=BIG), now=T0)
        status = ledger.status("pool-a", now=T0)
        refused = ledger.reserve(PEER, _request(key="k2", upper_bound="0"), now=T0)
    assert grant["admitted"] is True
    assert status["outstanding"] == status["hard_bound"] == BIG + ".000000000000000001"
    assert Decimal(str(status["headroom"])) == 0
    assert refused == {"admitted": False, "reason": NOT_ADMITTED}  # the fee alone exceeds


def test_a_settlement_overrun_is_reported_exactly_and_replayed_identically(
    tmp_path: Path,
) -> None:
    """Review follow-up F2: the stored settle answer agrees with the folded overrun."""
    ledger = _ledger(
        tmp_path,
        hard_bound=BIG,
        cost_basis={"tax": "pre_tax", "fixed_fee": "0", "minimum_charge": "0"},
    )
    with localcontext(TINY):
        grant = ledger.reserve(PEER, _request(upper_bound=BIG), now=T0)
        rid = str(grant["reservation_id"])
        first = ledger.settle(PEER, _settle(rid, amount=BIG, final=False), now=T0)
        over = _settle(rid, usage_ref="u2", amount=TINY_FEE, final=True)
        answer = ledger.settle(PEER, over, now=T0)
        replay = ledger.settle(PEER, over, now=T0)
        status = ledger.status("pool-a", now=T0)
    assert first["overrun"] is False
    assert answer["overrun"] is True and replay == answer
    assert status["overruns"] == [rid]
    assert status["settled"] == BIG + ".000000000000000001"


FIXTURE = Path(__file__).parent / "fixtures" / "spend_ledger_core_0_99_35.json"


def _legacy_ledger(tmp_path: Path) -> tuple[SpendLedger, dict[str, Any]]:
    """Restore the rows a released Core 0.99.35 ledger wrote, byte for byte."""
    dump: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    home = tmp_path / "legacy"
    home.mkdir(mode=0o700)
    path = home / "ledger.sqlite3"
    with sqlite3.connect(path) as connection:
        for statement in dump["schema"]:
            connection.execute(statement)
        connection.executemany(
            "INSERT INTO events (seq, pool_id, kind, recorded_at, body) VALUES (?, ?, ?, ?, ?)",
            dump["events"],
        )
        connection.executemany(
            "INSERT INTO operations (scope, digest, response) VALUES (?, ?, ?)",
            dump["operations"],
        )
        connection.execute(f"PRAGMA user_version={dump['user_version']}")
    path.chmod(0o600)
    return SpendLedger(path, owner_hub_id=OWNER), dump


def test_a_core_0_99_35_ledger_is_migrated_without_losing_an_answer(tmp_path: Path) -> None:
    """Review follow-up F1: a retry after the upgrade replays; it never grants twice."""
    ledger, dump = _legacy_ledger(tmp_path)
    answers = dump["answers"]
    assert dump["user_version"] == 1
    with sqlite3.connect(ledger.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    base = {"pool_id": "pool-a", "seat": "PROJ/alice", "task": "t1", "operation": "call-1"}
    assert ledger.query(PEER, {**base, "key": "k1"}) == {"found": True, "response": answers["k1"]}
    later = T0 + timedelta(minutes=5)
    assert ledger.reserve(PEER, _request(), now=later) == answers["k1"]
    assert ledger.reserve(PEER, _request(key="k2", upper_bound="200"), now=later) == answers["k2"]
    assert ledger.reserve(PEER, _request(pool_id="pool-z", key="kz"), now=later) == answers["kz"]
    bob = _request(seat="PROJ/bob", key="k3", upper_bound="5")
    assert ledger.reserve(PEER, bob, now=later) == answers["k3"]
    settle = _settle(str(answers["k1"]["reservation_id"]), final=False)
    assert ledger.settle(PEER, settle, now=later) == answers["settle_u1"]
    events = ledger.audit("pool-a")
    assert len([e for e in events if e["kind"] == "grant"]) == 2  # nothing new was granted
    assert len(events) == len([e for e in dump["events"] if e[1] == "pool-a"])
    status = ledger.status("pool-a", now=later)
    assert (status["settled"], status["outstanding"]) == ("5", "12")  # (11 - 5) + 6
    SpendLedger(ledger.path, owner_hub_id=OWNER)  # a second open finds version 2: no-op

    ledger.configure(_config(**NEXT_WINDOW, hard_bound="90", cause="next month"), now=IN_NEXT)
    fresh = ledger.reserve(PEER, _request(), now=IN_NEXT)
    assert fresh["admitted"] is True and fresh["reservation_id"] != answers["k1"]["reservation_id"]
    assert ledger.status("pool-a", now=IN_NEXT)["settled"] == "0"


@pytest.mark.parametrize(
    ("statement", "match"),
    [
        (
            "INSERT INTO operations (scope, digest, response) VALUES "
            "('reserve' || char(0) || 'hub-peer' || char(0) || 'pool-a' || char(0) || 'PROJ/x'"
            " || char(0) || 't' || char(0) || 'o' || char(0) || 'k', 'd',"
            ' \'{"admitted": false, "reason": "not-admitted"}\')',
            "no matching ledger event",
        ),
        (
            'UPDATE events SET body = \'{"pool_id": "pool-a"}\' WHERE seq = 1',
            "cannot be read for migration",
        ),
        ("UPDATE events SET body = '[]' WHERE kind = 'refusal'", "cannot be read for migration"),
    ],
)
def test_a_legacy_ledger_that_cannot_be_matched_is_refused(
    tmp_path: Path, statement: str, match: str
) -> None:
    dump: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    home = tmp_path / "legacy"
    home.mkdir(mode=0o700)
    path = home / "ledger.sqlite3"
    with sqlite3.connect(path) as connection:
        for sql in dump["schema"]:
            connection.execute(sql)
        connection.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", dump["events"])
        connection.executemany("INSERT INTO operations VALUES (?, ?, ?)", dump["operations"])
        connection.execute(statement)
        connection.execute("PRAGMA user_version=1")
    path.chmod(0o600)
    with pytest.raises(SpendLedgerError, match=match):
        SpendLedger(path, owner_hub_id=OWNER)
    with sqlite3.connect(path) as connection:  # the failed migration changed nothing
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
