# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — model cost/token accounting regressions

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.accounting import (
    USAGE_NOTE_KIND,
    AccountingTotals,
    BudgetStatus,
    ModelPrice,
    UsageRecord,
    accounting_to_json,
    build_accounting_report,
    format_usage_note,
    parse_usage_note,
    render_human,
    run_accounting_report,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore, StoredEvent


def _usage_event(
    *,
    seq: int,
    author: str,
    model: str,
    calls: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost: float | None = None,
    task_id: str = "TASK",
    ts: float = 1.0,
) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=ts,
        kind=EventKind.LEDGER_PROGRESS,
        payload={
            "author": author,
            "kind": USAGE_NOTE_KIND,
            "task_id": task_id,
            "text": format_usage_note(
                model=model,
                calls=calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
            ),
        },
    )


# ---------- format_usage_note ----------


def test_format_usage_note_minimal_and_full() -> None:
    minimal = format_usage_note(model="claude-opus-4-8")
    assert minimal == "usage model=claude-opus-4-8 calls=1 input_tokens=0 output_tokens=0"
    full = format_usage_note(
        model="  gpt-5.5  ", calls=2, input_tokens=1200, output_tokens=300, cost=0.5
    )
    assert "model=gpt-5.5" in full
    assert "calls=2" in full
    assert "input_tokens=1200 output_tokens=300" in full
    assert "cost_usd=0.500000" in full


@pytest.mark.parametrize(
    "kwargs",
    [
        {"model": ""},
        {"model": "   "},
        {"model": "two words"},
        {"model": "m", "calls": -1},
        {"model": "m", "input_tokens": -5},
        {"model": "m", "output_tokens": -5},
        {"model": "m", "cost": -0.01},
    ],
)
def test_format_usage_note_rejects_bad_input(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        format_usage_note(**kwargs)  # type: ignore[arg-type]


# ---------- parse_usage_note ----------


def test_parse_usage_note_roundtrip_with_cost() -> None:
    parsed = parse_usage_note(
        format_usage_note(model="m", calls=3, input_tokens=10, output_tokens=20, cost=1.25)
    )
    assert parsed == {
        "model": "m",
        "calls": 3,
        "input_tokens": 10,
        "output_tokens": 20,
        "cost": 1.25,
    }


def test_parse_usage_note_without_cost_has_no_cost_key() -> None:
    parsed = parse_usage_note(format_usage_note(model="m"))
    assert parsed is not None
    assert "cost" not in parsed


@pytest.mark.parametrize(
    "text",
    [
        "",
        "note model=m",
        "usage calls=1",
        "usage model=",
    ],
)
def test_parse_usage_note_rejects_non_usage(text: str) -> None:
    assert parse_usage_note(text) is None


def test_parse_usage_note_ignores_malformed_and_unknown_pairs() -> None:
    parsed = parse_usage_note("usage model=m stray calls=abc cost_usd=-2 extra=ok")
    assert parsed is not None
    assert parsed["model"] == "m"
    assert parsed["calls"] == 1  # bad int falls back to default
    assert "cost" not in parsed  # negative cost rejected


def test_parse_usage_note_clamps_negative_int_to_zero() -> None:
    parsed = parse_usage_note("usage model=m input_tokens=-9")
    assert parsed is not None
    assert parsed["input_tokens"] == 0


def test_parse_usage_note_drops_non_numeric_cost() -> None:
    parsed = parse_usage_note("usage model=m cost_usd=notanumber")
    assert parsed is not None
    assert "cost" not in parsed


# ---------- ModelPrice / UsageRecord ----------


def test_model_price_estimate() -> None:
    price = ModelPrice(input_per_1k=1.0, output_per_1k=2.0)
    assert price.estimate(1000, 500) == pytest.approx(2.0)


def test_usage_record_estimated_cost_paths() -> None:
    record = UsageRecord(
        agent="a",
        model="m",
        task_id="t",
        calls=1,
        input_tokens=1000,
        output_tokens=1000,
        recorded_cost=9.0,
        seq=1,
        ts=1.0,
    )
    assert record.total_tokens == 2000
    # pricing wins when the model is priced
    priced = record.estimated_cost({"m": ModelPrice(input_per_1k=1.0, output_per_1k=1.0)})
    assert priced == pytest.approx(2.0)
    # unpriced model falls back to recorded cost
    assert record.estimated_cost({"other": ModelPrice(0.0, 0.0)}) == pytest.approx(9.0)
    # no pricing and no recorded cost -> zero
    bare = UsageRecord("a", "m", "t", 1, 0, 0, None, 1, 1.0)
    assert bare.estimated_cost(None) == 0.0


# ---------- BudgetStatus / AccountingTotals ----------


def test_budget_status_remaining_and_over() -> None:
    under = BudgetStatus(agent="a", budget=10.0, spent=4.0)
    assert under.remaining == pytest.approx(6.0)
    assert under.over_budget is False
    over = BudgetStatus(agent="a", budget=10.0, spent=12.0)
    assert over.remaining == 0.0
    assert over.over_budget is True


def test_accounting_totals_total_tokens() -> None:
    totals = AccountingTotals(calls=1, input_tokens=3, output_tokens=4, estimated_cost=0.0)
    assert totals.total_tokens == 7


# ---------- build_accounting_report ----------


def test_build_report_aggregates_agents_models_totals_and_budgets() -> None:
    events = [
        _usage_event(seq=1, author="alpha", model="opus", input_tokens=1000, output_tokens=1000),
        _usage_event(seq=2, author="alpha", model="haiku", calls=2, input_tokens=500),
        _usage_event(seq=3, author="beta", model="opus", output_tokens=2000, cost=0.0),
        # noise that must be ignored:
        StoredEvent(4, 5.0, EventKind.CHAT, {"text": "hi"}),
        StoredEvent(5, 5.0, EventKind.LEDGER_PROGRESS, {"kind": "note", "text": "not usage"}),
        StoredEvent(
            6, 5.0, EventKind.LEDGER_PROGRESS, {"kind": USAGE_NOTE_KIND, "text": "usage model="}
        ),
    ]
    pricing = {"opus": ModelPrice(input_per_1k=2.0, output_per_1k=4.0)}
    report = build_accounting_report(events, pricing=pricing, budgets={"alpha": 1.0, "gamma": 5.0})

    assert report.generated_from_seq == 6
    assert len(report.records) == 3

    by_agent = report.summary_by_agent
    # alpha opus: 1000 in * 2.0/1k + 1000 out * 4.0/1k = 6.0; haiku unpriced -> 0.0
    assert by_agent["alpha"].calls == 3
    assert by_agent["alpha"].total_tokens == 2500
    assert by_agent["alpha"].estimated_cost == pytest.approx(6.0)
    assert by_agent["alpha"].counterparts == ("haiku", "opus")
    # beta opus: 2000 out * 4.0/1k = 8.0 (pricing wins over recorded 0.0)
    assert by_agent["beta"].estimated_cost == pytest.approx(8.0)

    models = {summary.key: summary for summary in report.models}
    assert models["opus"].counterparts == ("alpha", "beta")
    assert models["opus"].estimated_cost == pytest.approx(14.0)

    assert report.totals.calls == 4
    assert report.totals.estimated_cost == pytest.approx(14.0)

    budgets = {status.agent: status for status in report.budgets}
    assert budgets["alpha"].over_budget is True  # spent 2.0 >= 1.0
    assert budgets["gamma"].spent == 0.0  # declared but unused
    assert budgets["gamma"].over_budget is False


def test_build_report_without_budgets_is_empty_tuple() -> None:
    report = build_accounting_report([_usage_event(seq=1, author="a", model="m")])
    assert report.budgets == ()


# ---------- run_accounting_report ----------


def test_run_report_missing_store_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing event store"):
        run_accounting_report(tmp_path / "nope.db")


def test_run_report_reads_real_store(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "alpha",
            "kind": USAGE_NOTE_KIND,
            "task_id": "T",
            "text": format_usage_note(model="opus", input_tokens=2000),
        },
        durable=True,
    )
    store.close()
    report = run_accounting_report(db, pricing={"opus": ModelPrice(1.0, 1.0)})
    assert report.totals.estimated_cost == pytest.approx(2.0)
    assert report.records[0].agent == "alpha"


# ---------- json + human ----------


def test_accounting_to_json_shape() -> None:
    report = build_accounting_report(
        [_usage_event(seq=1, author="a", model="m", input_tokens=10, output_tokens=5, cost=0.2)],
        budgets={"a": 1.0},
    )
    payload = accounting_to_json(report)
    assert cast(str, payload["note"]).startswith("opt-in usage evidence")
    assert cast(dict[str, object], payload["totals"])["total_tokens"] == 15
    agents = cast("list[dict[str, object]]", payload["agents"])
    models = cast("list[dict[str, object]]", payload["models"])
    budgets = cast("list[dict[str, object]]", payload["budgets"])
    records = cast("list[dict[str, object]]", payload["records"])
    assert agents[0]["key"] == "a"
    assert models[0]["key"] == "m"
    assert budgets[0]["agent"] == "a"
    assert records[0]["recorded_cost"] == pytest.approx(0.2)


def test_render_human_empty() -> None:
    text = render_human(build_accounting_report([]))
    assert "No recorded model usage" in text


def test_render_human_with_budgets_marks_over() -> None:
    report = build_accounting_report(
        [_usage_event(seq=1, author="a", model="m", input_tokens=10, cost=5.0)],
        budgets={"a": 1.0},
    )
    text = render_human(report)
    assert "By agent" in text
    assert "By model" in text
    assert "Budgets (evidence, not enforcement)" in text
    assert "OVER" in text
