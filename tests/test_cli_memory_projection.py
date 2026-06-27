# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the memory recall CLI command

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_memory_projection
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _recall_ns(**overrides: Any) -> argparse.Namespace:
    """Return an argparse namespace shaped like ``memory-recall``."""
    base: dict[str, Any] = {
        "db": "events.db",
        "query": "memory recall",
        "since_seq": 0,
        "limit": 5,
        "json": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_store(path: Path) -> None:
    """Write one memory finding into ``path``."""
    store = EventStore(path)
    store.append(
        EventKind.FINDING,
        {
            "statement": "Memory projection keeps provenance attached to recall hits.",
            "provenance": {"actor": "codex-a"},
        },
        ts=1.0,
        durable=True,
    )
    store.close()


def test_memory_recall_parser_wires_command() -> None:
    args = cli.build_parser().parse_args(
        ["memory-recall", "hub.db", "projection provenance", "--since-seq", "2", "--limit", "3"]
    )

    assert args.command == "memory-recall"
    assert args.db == "hub.db"
    assert args.query == "projection provenance"
    assert args.since_seq == 2
    assert args.limit == 3
    assert args.func is cli_memory_projection._cmd_memory_recall


def test_cmd_memory_recall_prints_human_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    assert cli_memory_projection._cmd_memory_recall(_recall_ns(db=str(db))) == 0

    out = capsys.readouterr().out
    assert "Memory recall for: memory recall" in out
    assert "finding.statement" in out
    assert "codex-a" in out


def test_cmd_memory_recall_prints_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "events.db"
    _seed_store(db)

    assert cli_memory_projection._cmd_memory_recall(_recall_ns(db=str(db), json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["hits"][0]["actor"] == "codex-a"
    assert payload["hits"][0]["matched_tokens"] == ["memory", "recall"]


def test_cmd_memory_recall_reports_input_error(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli_memory_projection._cmd_memory_recall(_recall_ns(db="/no/such/events.db"))

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "missing event store:" in captured.err
