# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — model cost/token accounting CLI regressions

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import pytest

from hub_e2e_helpers import running_hub
from synapse_channel.cli_accounting import (
    _emit_usage,
    _load_budgets,
    _load_pricing,
    add_parsers,
)
from synapse_channel.core.accounting import ModelPrice, format_usage_note, run_accounting_report
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    add_parsers(sub)
    return parser


def _seed_store(db: Path) -> None:
    store = EventStore(db)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "author": "alpha",
            "kind": "usage",
            "task_id": "T",
            "text": format_usage_note(model="opus", input_tokens=1000, output_tokens=500),
        },
        durable=True,
    )
    store.close()


# ---------- pricing / budget loaders ----------


def test_load_pricing_and_budgets_none() -> None:
    assert _load_pricing(None) is None
    assert _load_budgets(None) is None


def test_load_pricing_valid(tmp_path: Path) -> None:
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps({"opus": {"input_per_1k": 2.0, "output_per_1k": 4.0}}))
    pricing = _load_pricing(str(path))
    assert pricing is not None
    assert pricing["opus"] == ModelPrice(input_per_1k=2.0, output_per_1k=4.0)


def test_load_budgets_valid(tmp_path: Path) -> None:
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"alpha": 12.5}))
    budgets = _load_budgets(str(path))
    assert budgets == {"alpha": 12.5}


def test_load_pricing_entry_not_object(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"opus": 3.0}))
    with pytest.raises(ValueError, match="must be an object"):
        _load_pricing(str(path))


def test_load_pricing_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(json.dumps([1, 2, 3]))
    with pytest.raises(ValueError, match="must contain a JSON object"):
        _load_pricing(str(path))


def test_load_budgets_rejects_bool_and_negative(tmp_path: Path) -> None:
    boolean = tmp_path / "b1.json"
    boolean.write_text(json.dumps({"alpha": True}))
    with pytest.raises(ValueError, match="non-negative number"):
        _load_budgets(str(boolean))
    negative = tmp_path / "b2.json"
    negative.write_text(json.dumps({"alpha": -1.0}))
    with pytest.raises(ValueError, match="non-negative number"):
        _load_budgets(str(negative))


def test_load_pricing_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not read JSON file"):
        _load_pricing(str(tmp_path / "missing.json"))


# ---------- report command ----------


def test_report_human_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    parser = _parser()
    args = parser.parse_args(["accounting", "report", str(db)])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "Model cost/token accounting" in out
    assert "alpha" in out


def test_report_json_output_with_pricing_and_budget(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"opus": {"input_per_1k": 1.0, "output_per_1k": 1.0}}))
    budget = tmp_path / "budget.json"
    budget.write_text(json.dumps({"alpha": 0.5}))
    parser = _parser()
    args = parser.parse_args(
        [
            "accounting",
            "report",
            str(db),
            "--json",
            "--pricing",
            str(pricing),
            "--budget",
            str(budget),
        ]
    )
    assert args.func(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["estimated_cost"] == pytest.approx(1.5)
    assert payload["budgets"][0]["over_budget"] is True


def test_report_missing_db_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["accounting", "report", str(tmp_path / "nope.db")])
    assert args.func(args) == 2
    assert "missing event store" in capsys.readouterr().err


def test_report_bad_pricing_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "hub.db"
    _seed_store(db)
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps([1, 2]))
    parser = _parser()
    args = parser.parse_args(["accounting", "report", str(db), "--pricing", str(pricing)])
    assert args.func(args) == 2


# ---------- record command ----------


def test_record_rejects_bad_model_returns_2(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(["accounting", "record", "--name", "alpha", "--model", "bad model"])
    assert args.func(args) == 2
    assert "model" in capsys.readouterr().err


def test_record_connect_failure_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    parser = _parser()
    args = parser.parse_args(
        [
            "accounting",
            "record",
            "--name",
            "alpha",
            "--model",
            "opus",
            "--uri",
            "ws://127.0.0.1:1",
            "--ready-timeout",
            "0.2",
        ]
    )
    assert args.func(args) == 1


async def test_record_emits_usage_persisted_and_reported(tmp_path: Path) -> None:
    db = tmp_path / "hub.db"
    store = EventStore(db)
    hub = SynapseHub(journal=store, hub_id="syn-test")
    async with running_hub(hub) as (_hub, uri):
        rc = await _emit_usage(
            uri=uri,
            name="alpha-rx",
            task_id="T",
            note=format_usage_note(model="opus", input_tokens=1000, output_tokens=200),
            token=None,
            ready_timeout=3.0,
        )
        assert rc == 0
        report = None
        for _ in range(150):
            try:
                report = run_accounting_report(db)
            except ValueError:
                report = None
            if report is not None and report.records:
                break
            await asyncio.sleep(0.02)
    assert report is not None and report.records
    record = report.records[0]
    assert record.agent == "alpha"  # the "-rx" waiter suffix is stripped on send
    assert record.model == "opus"
    assert record.total_tokens == 1200
