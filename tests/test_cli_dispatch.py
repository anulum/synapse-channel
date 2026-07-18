# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the dispatcher CLI command

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_dispatch


def test_parser_dispatch_defaults() -> None:
    args = cli.build_parser().parse_args(["dispatch", "--project", "SYNAPSE-CHANNEL"])
    assert args.project == "SYNAPSE-CHANNEL"
    assert args.name == ""
    assert args.interval == 60.0
    assert args.once is False
    assert args.dry_run is False
    assert args.suggestion_ttl == 900.0
    assert args.capacity == 1
    assert args.max_attempts == 3
    assert args.outbox is None
    assert args.func is cli_dispatch._cmd_dispatch


def test_parser_dispatch_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "dispatch",
            "--project",
            "SYNAPSE-CHANNEL",
            "--name",
            "SYNAPSE-CHANNEL/dispatcher",
            "--interval",
            "30",
            "--once",
            "--dry-run",
            "--suggestion-ttl",
            "60",
            "--capacity",
            "2",
            "--max-attempts",
            "5",
            "--outbox",
            "/tmp/o.jsonl",
        ]
    )
    assert args.interval == 30.0
    assert args.once is True
    assert args.dry_run is True
    assert args.suggestion_ttl == 60.0
    assert args.capacity == 2
    assert args.max_attempts == 5
    assert args.outbox == "/tmp/o.jsonl"


def test_parser_dispatch_requires_project() -> None:
    parser = cli.build_parser()
    try:
        parser.parse_args(["dispatch"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - argparse always exits here
        raise AssertionError("dispatch without --project must exit 2")


def test_cmd_dispatch_builds_worker_and_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    built: dict[str, Any] = {}

    class _Worker:
        def __init__(self, **kwargs: Any) -> None:
            built.update(kwargs)
            self.name = kwargs.get("name") or f"{kwargs['project']}/dispatcher"

        async def run(self) -> int:
            return 0

    monkeypatch.setattr(cli_dispatch, "DispatcherWorker", _Worker)
    args = argparse.Namespace(
        project="SYNAPSE-CHANNEL",
        name="",
        uri="ws://example",
        token=None,
        interval=30.0,
        once=True,
        dry_run=True,
        suggestion_ttl=60.0,
        capacity=2,
        max_attempts=5,
        outbox="/tmp/o.jsonl",
        ready_timeout=5.0,
    )
    assert cli_dispatch._cmd_dispatch(args) == 0
    assert built["project"] == "SYNAPSE-CHANNEL"
    assert built["once"] is True
    assert built["dry_run"] is True
    assert built["capacity"] == 2
    assert built["max_attempts"] == 5
    assert built["outbox_path"] == Path("/tmp/o.jsonl")


def test_cmd_dispatch_keyboard_interrupt_is_clean(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _InterruptWorker:
        def __init__(self, **kwargs: Any) -> None:
            self.name = f"{kwargs['project']}/dispatcher"

        async def run(self) -> int:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_dispatch, "DispatcherWorker", _InterruptWorker)
    args = argparse.Namespace(
        project="SYNAPSE-CHANNEL",
        name="",
        uri="ws://example",
        token=None,
        interval=30.0,
        once=False,
        dry_run=False,
        suggestion_ttl=60.0,
        capacity=1,
        max_attempts=3,
        outbox=None,
        ready_timeout=5.0,
    )
    assert cli_dispatch._cmd_dispatch(args) == 0
    assert "stopped by user" in capsys.readouterr().out
