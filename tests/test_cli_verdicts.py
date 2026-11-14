# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse verdict` against a real hub and its event store
"""Record evidence, a verdict and a correction through the CLI, then report them."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from hub_e2e_helpers import close_agents, connect_agent, free_port, running_hub
from synapse_channel.cli import build_parser, main
from synapse_channel.cli_hub_config import build_cli_hub_config
from synapse_channel.cli_verdicts import post_record
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.verdict_records import (
    CORRECTION_NOTE_KIND,
    CorrectionRecord,
    correction_id,
    format_correction,
)

RAW = "1" * 64
RAW_FIXED = "2" * 64
ABSTRACT = "3" * 64


def _run(argv: list[str]) -> int:
    """Run the real CLI entry point, which manages its own event loop."""
    return main(argv)


async def _cli(*argv: str) -> int:
    return await asyncio.to_thread(_run, list(argv))


async def test_cli_records_and_status_reports_the_stale_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every subcommand works end to end; a foreign correction is refused with exit 1."""
    db = tmp_path / "hub.db"
    store = EventStore(db)
    members = tmp_path / "members.txt"
    members.write_text(f"{RAW}\n", encoding="utf-8")
    manifest = "4" * 64
    async with running_hub(SynapseHub(journal=store)) as (_hub, uri):
        common = ["--uri", uri, "--task", "STUDY"]
        writer = ["--name", "LAB/writer", *common]
        assert (
            await _cli("verdict", "evidence", "--sha256", RAW, "--namespace", "LAB", *writer) == 0
        )
        assert (
            await _cli(
                "verdict",
                "evidence",
                "--sha256",
                manifest,
                "--namespace",
                "LAB",
                "--members-file",
                str(members),
                *writer,
            )
            == 0
        )
        assert (
            await _cli(
                "verdict",
                "record",
                "--namespace",
                "LAB",
                "--subject",
                ABSTRACT,
                "--outcome",
                "accepted",
                "--input",
                manifest,
                "--author",
                "LAB/writer",
                "--sequence",
                "1",
                "--name",
                "LAB/reviewer",
                *common,
            )
            == 0
        )
        foreign = [
            "verdict",
            "correct",
            "--namespace",
            "LAB",
            "--corrects",
            RAW,
            "--replacement",
            RAW_FIXED,
            "--reason",
            "not mine",
            "--name",
            "OTHER/seat",
            *common,
        ]
        assert await _cli(*foreign) == 1
        approver = await connect_agent("LAB/approver", uri)
        approval = format_approval_note(subject=ABSTRACT, state="approved", reason="")
        await approver.agent.post_progress(ABSTRACT, approval, kind=APPROVAL_NOTE_KIND)
        await approver.recorder.wait_for(lambda m: m.get("type") == "ledger_progress_posted")
        fix = [
            "verdict",
            "correct",
            "--namespace",
            "LAB",
            "--corrects",
            RAW,
            "--replacement",
            RAW_FIXED,
            "--reason",
            "re-exported",
            *writer,
        ]
        assert await _cli(*fix) == 0
        try:
            # Granted while fresh; a second approval after the correction is refused.
            await approver.agent.post_progress(ABSTRACT, approval, kind=APPROVAL_NOTE_KIND)
            await approver.recorder.wait_for(lambda m: m.get("type") == "error")
        finally:
            await close_agents(approver)
    store.close()
    captured = capsys.readouterr()
    assert "correction-not-authorised" in captured.err
    assert await _cli("verdict", "status", str(db), "--json") == 0
    report = json.loads(capsys.readouterr().out)
    fix_id = correction_id(CorrectionRecord("LAB", RAW, RAW_FIXED, "re-exported"))
    assert report["subjects"][f"LAB:{ABSTRACT}"]["passed"] is False
    assert report["verdicts"][0]["unacknowledged_corrections"] == [fix_id]
    assert len(report["verdict_stale_events"]) == 1
    assert len(report["refused_records"]) == 2
    assert report["granted_then_stale"][0]["subject"] == ABSTRACT
    assert await _cli("verdict", "status", str(db), "--subject", ABSTRACT) == 0
    rendered = capsys.readouterr().out
    assert "state=stale" in rendered
    assert f"granted then stale: {ABSTRACT}" in rendered


async def test_cli_refusals_before_any_hub_contact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Malformed records, missing stores, unreachable hubs and silent hubs exit non-zero."""
    bad = ["verdict", "evidence", "--sha256", "XYZ", "--namespace", "LAB"]
    assert await _cli(*bad, "--name", "LAB/w", "--task", "T") == 2
    assert "lowercase hexadecimal" in capsys.readouterr().err
    assert await _cli("verdict", "status", str(tmp_path / "absent.db")) == 2
    assert "missing event store" in capsys.readouterr().err
    unreachable = f"ws://127.0.0.1:{free_port()}"
    body = format_correction(CorrectionRecord("LAB", RAW, RAW_FIXED, "x"))
    assert (
        await post_record(
            uri=unreachable,
            name="LAB/w",
            task_id="T",
            kind=CORRECTION_NOTE_KIND,
            text=body,
            token=None,
            ready_timeout=0.2,
            answer_timeout=0.2,
        )
        == 1
    )
    async with running_hub() as (_hub, uri):
        # A zero answer budget expires before the hub can reply.
        assert (
            await post_record(
                uri=uri,
                name="LAB/w",
                task_id="T",
                kind="note",
                text="waiting for nothing",
                token=None,
                ready_timeout=5.0,
                answer_timeout=0.0,
            )
            == 1
        )
    assert "no answer from the hub" in capsys.readouterr().err


def test_hub_options_build_the_verdict_policy() -> None:
    """Hub flags reach the hub's verdict policy; a hub without them keeps the default."""
    args = build_parser().parse_args(
        [
            "hub",
            "--verdict-authority",
            "LAB=OTHER/auditor",
            "--verdict-gate",
            "off",
            "--allow-unrecorded-verdict-inputs",
        ]
    )
    auth = build_cli_hub_config(args, HubConfig()).auth
    assert auth.verdict_authority == {"LAB": frozenset({"OTHER/auditor"})}
    assert auth.verdict_gate_enabled is False
    assert auth.allow_unrecorded_verdict_inputs is True
    hub = SynapseHub(verdict_authority=auth.verdict_authority, verdict_gate_enabled=False)
    assert hub.verdict_policy.gate_enabled is False
    hub.verdict_policy = replace(hub.verdict_policy, gate_enabled=True)
    assert hub.verdict_policy.gate_enabled is True
    with pytest.raises(SystemExit):
        build_parser().parse_args(["hub", "--verdict-authority", "missing-separator"])
