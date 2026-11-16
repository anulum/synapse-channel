# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — ops run through a real hub's governed operations
"""Declare, approve and run one catalogue operation through a real, serving hub.

The hub has the verdict gate on and the effect suite's access policy. The
executor claims a ``suite-*`` task, declares through ``synapse ops run --hub
--declare``, another identity of its project approves the intent's digest after a
review, and ``--intent-sha256`` runs the operation inside the one attempt the
hub opens. The command runs in a worker thread while the hub serves.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.verdict_records import (
    EVIDENCE_NOTE_KIND,
    VERDICT_NOTE_KIND,
    EvidenceRecord,
    VerdictRecord,
    format_evidence,
    format_verdict,
)
from synapse_channel.effect_suite.session import HubSession
from synapse_channel.effect_suite.workspace import PROJECT, SuiteHub, suite_hub
from test_cli_ops_hub import DEADLINE, _command
from test_operations_runner import World

EXECUTOR = f"{PROJECT}/executor"
REVIEWER = f"{PROJECT}/reviewer"
APPROVER = f"{PROJECT}/approver"
TASK = "suite-ops-1"
REVIEW_INPUT = "1" * 64


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return World(tmp_path, monkeypatch)


@pytest.fixture
async def hub(tmp_path: Path) -> AsyncIterator[SuiteHub]:
    async with suite_hub(tmp_path) as serving:
        yield serving


async def _claim(hub: SuiteHub) -> int:
    """Claim the task as the executor and return the epoch; the claim outlives the session."""
    async with HubSession(EXECUTOR, hub.uri) as session:
        grant = await session.after(
            lambda: session.agent.claim(TASK, note="ops run through the hub"),
            lambda frame: (
                frame.get("type") == MessageType.CLAIM_GRANTED and frame.get("task_id") == TASK
            ),
            what="a claim grant",
        )
    return int(grant["epoch"])


async def _cli(world: World, hub: SuiteHub, epoch: int, *step: str) -> int:
    """Run ``synapse ops run --hub`` in a worker thread and return its exit status."""
    hub_options = ("--hub", hub.uri, "--task", TASK, "--epoch", str(epoch), "--name", EXECUTOR)
    argv = _command(world, *hub_options, "--intent-deadline", str(DEADLINE), *step)
    return await asyncio.to_thread(main, argv)


async def _note(name: str, hub: SuiteHub, kind: str, text: str) -> None:
    """Post one progress note on the task as ``name`` and wait until the hub posted it."""
    async with HubSession(name, hub.uri) as session:
        await session.after(
            lambda: session.agent.post_progress(TASK, text, kind=kind),
            lambda frame: (
                frame.get("type") == "ledger_progress_posted" and frame["note"]["text"] == text
            ),
            what=f"a {kind} note",
        )


async def _approve(hub: SuiteHub, digest: str, *, approver: str = APPROVER) -> None:
    """Review the intent, then approve its digest as ``approver``."""
    for item in (REVIEW_INPUT, digest):
        await _note(
            REVIEWER, hub, EVIDENCE_NOTE_KIND, format_evidence(EvidenceRecord(item, PROJECT))
        )
    verdict = VerdictRecord(PROJECT, digest, "accepted", (REVIEW_INPUT,), (), REVIEWER, 1)
    await _note(REVIEWER, hub, VERDICT_NOTE_KIND, format_verdict(verdict))
    approval = format_approval_note(subject=f"sha256:{digest}", state="approved", reason="ops run")
    await _note(approver, hub, APPROVAL_NOTE_KIND, approval)


def _line(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, lines
    result: dict[str, Any] = json.loads(lines[0])
    return result


async def _declared(
    world: World, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    """Claim and declare; return the epoch and the digest the hub kept."""
    epoch = await _claim(hub)
    status = await _cli(world, hub, epoch, "--declare")
    answer = _line(capsys)
    assert (status, answer["status"]) == (0, "accepted"), answer
    return epoch, str(answer["intent_sha256"])


async def test_an_approved_run_goes_through_the_hub_once(
    world: World, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> None:
    epoch, digest = await _declared(world, hub, capsys)
    await _approve(hub, digest)

    status = await _cli(world, hub, epoch, "--intent-sha256", digest)
    report = _line(capsys)
    assert status == 0, json.dumps(report, sort_keys=True)
    assert (report["attempt"]["answer"], report["outcome_published"]["status"]) == (
        "approved_fresh",
        "accepted",
    )
    assert world.invocations() == ["setting set mode strict"]
    again = await _cli(world, hub, epoch, "--intent-sha256", digest)
    assert again == 2 and "changed since the intent was declared" in capsys.readouterr().err
    assert world.invocations() == ["setting set mode strict"]


@pytest.mark.parametrize("approver", [None, EXECUTOR])
async def test_without_another_identitys_approval_nothing_runs(
    world: World, hub: SuiteHub, capsys: pytest.CaptureFixture[str], approver: str | None
) -> None:
    epoch, digest = await _declared(world, hub, capsys)
    if approver is not None:
        await _approve(hub, digest, approver=approver)

    status = await _cli(world, hub, epoch, "--intent-sha256", digest)
    answer = _line(capsys)["attempt"]
    assert status == 1 and answer["answer"] != "approved_fresh", answer
    assert world.invocations() == []


async def test_a_missing_grant_keeps_the_approval_for_a_later_run(
    world: World, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> None:
    epoch, digest = await _declared(world, hub, capsys)
    await _approve(hub, digest)
    world.grant(("operation-read",))

    refused = await _cli(world, hub, epoch, "--intent-sha256", digest)
    assert refused == 2 and "holds no unexpired grant" in capsys.readouterr().err
    world.grant(("operation-read", "operation-change"))
    status = await _cli(world, hub, epoch, "--intent-sha256", digest)
    assert status == 0 and _line(capsys)["attempt"]["answer"] == "approved_fresh"


async def test_a_program_replaced_after_approval_is_refused_before_the_hub(
    world: World, hub: SuiteHub, capsys: pytest.CaptureFixture[str]
) -> None:
    epoch, digest = await _declared(world, hub, capsys)
    await _approve(hub, digest)
    world.write_program("# replaced\n")

    status = await _cli(world, hub, epoch, "--intent-sha256", digest)
    assert status == 2 and "refused" in capsys.readouterr().err
    assert world.invocations() == []
