# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse
import json

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_locking
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


def test_parser_release() -> None:
    args = cli.build_parser().parse_args(["release", "studio-panel-enrich", "--name", "USER"])
    assert args.task_id == "studio-panel-enrich"
    assert args.name == "USER"
    assert args.func is cli_locking._cmd_release


def test_parser_release_accepts_receipt_fields() -> None:
    args = cli.build_parser().parse_args(
        [
            "release",
            "studio-panel-enrich",
            "--name",
            "USER",
            "--evidence",
            "pytest tests/test_cli_locking_release.py -q",
            "--artifact",
            "coverage.xml",
            "--known-failure",
            "mkdocs pending on unrelated branch",
            "--changed-file",
            "src/synapse_channel/cli_locking.py",
            "--generated-artifact",
            "docs/_generated/capability_manifest.json",
            "--approval",
            "reviewed-by=owner",
            "--confidence",
            "medium",
            "--freshness-seconds",
            "30",
            "--receipt-json",
        ]
    )

    assert args.evidence == ["pytest tests/test_cli_locking_release.py -q"]
    assert args.artifacts == ["coverage.xml"]
    assert args.known_failures == ["mkdocs pending on unrelated branch"]
    assert args.changed_files == ["src/synapse_channel/cli_locking.py"]
    assert args.generated_artifacts == ["docs/_generated/capability_manifest.json"]
    assert args.approvals == ["reviewed-by=owner"]
    assert args.confidence == "medium"
    assert args.freshness_seconds == 30.0
    assert args.receipt_json is True


async def _claim(uri: str, owner: str, task_id: str) -> AgentHandle:
    handle = await connect_agent(owner, uri)
    await handle.agent.claim(task_id, worktree=task_id, paths=[])
    await handle.recorder.wait_for(
        lambda message: (
            message.get("type") == MessageType.CLAIM_GRANTED and message.get("task_id") == task_id
        )
    )
    return handle


async def test_release_granted(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        holder = await _claim(uri, "USER", "studio-panel-enrich")
        await close_agents(holder)
        code = await cli_locking._release(
            uri=uri,
            name="USER",
            task_id="studio-panel-enrich",
        )

    assert code == 0
    assert "studio-panel-enrich" not in hub.state.claims
    assert "released 'studio-panel-enrich'" in capsys.readouterr().out


async def test_release_prints_machine_readable_receipt(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        holder = await _claim(uri, "USER", "studio-panel-enrich")
        await close_agents(holder)
        code = await cli_locking._release(
            uri=uri,
            name="USER",
            task_id="studio-panel-enrich",
            evidence=["pytest tests/test_cli_locking_release.py -q"],
            artifacts=["coverage.xml"],
            known_failures=["mkdocs pending on unrelated branch"],
            changed_files=["src/synapse_channel/cli_locking.py"],
            generated_artifacts=["docs/_generated/capability_manifest.json"],
            approvals=["reviewed-by=owner"],
            confidence="medium",
            freshness_seconds=30.0,
            receipt_json=True,
        )

    assert code == 0
    assert "studio-panel-enrich" not in hub.state.claims
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {
        "artifacts": ["coverage.xml"],
        "approvals": ["reviewed-by=owner"],
        "changed_files": ["src/synapse_channel/cli_locking.py"],
        "confidence": "medium",
        "evidence": ["pytest tests/test_cli_locking_release.py -q"],
        "epistemic_reasons": ["known failures declared", "positive evidence present"],
        "epistemic_status": "degraded",
        "freshness_seconds": 30.0,
        "generated_artifacts": ["docs/_generated/capability_manifest.json"],
        "known_failures": ["mkdocs pending on unrelated branch"],
        "owner": "USER",
        "released": True,
        "task_id": "studio-panel-enrich",
    }
    assert hub.blackboard.progress[-1].as_dict() == {
        "author": "USER",
        "kind": "assessment",
        "posted_at": hub.blackboard.progress[-1].posted_at,
        "task_id": "studio-panel-enrich",
        "text": (
            "release receipt: evidence=pytest tests/test_cli_locking_release.py -q; "
            "artifacts=coverage.xml; known_failures=mkdocs pending on unrelated branch; "
            "changed_files=src/synapse_channel/cli_locking.py; "
            "generated_artifacts=docs/_generated/capability_manifest.json; "
            "approvals=reviewed-by=owner; confidence=medium; freshness_seconds=30.0; "
            "epistemic_status=degraded; "
            "epistemic_reasons=known failures declared, positive evidence present"
        ),
    }


async def test_release_denied_for_non_owner(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        holder = await _claim(uri, "SCPN-MIF-CORE", "studio-panel-enrich")
        try:
            code = await cli_locking._release(
                uri=uri,
                name="USER",
                task_id="studio-panel-enrich",
            )
        finally:
            await close_agents(holder)

    assert code == 1
    out = capsys.readouterr().out
    assert "release refused for 'studio-panel-enrich'" in out
    assert "owned by SCPN-MIF-CORE" in out


async def test_release_selects_matching_task(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        holder = await connect_agent("USER", uri)
        await holder.agent.claim("other", worktree="other", paths=[])
        await holder.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == "other"
            )
        )
        await holder.agent.claim("t", worktree="t", paths=[])
        await holder.recorder.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED and message.get("task_id") == "t"
            )
        )
        await close_agents(holder)
        code = await cli_locking._release(uri=uri, name="USER", task_id="t")

    assert code == 0
    assert "released 't'" in capsys.readouterr().out
    assert "t" not in hub.state.claims
    assert "other" in hub.state.claims


async def test_release_reports_unreachable(capsys: pytest.CaptureFixture[str]) -> None:
    code = await cli_locking._release(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        task_id="t",
        ready_timeout=0.1,
        attempts=1,
    )
    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


async def test_release_denies_missing_claim(capsys: pytest.CaptureFixture[str]) -> None:
    async with running_hub(SynapseHub()) as (_hub, uri):
        code = await cli_locking._release(uri=uri, name="USER", task_id="t", attempts=2)

    assert code == 1
    assert "release refused for 't'" in capsys.readouterr().out


def test_cmd_release_dispatches_real_command(capsys: pytest.CaptureFixture[str]) -> None:
    ns = argparse.Namespace(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="USER",
        task_id="t",
        token=None,
        ready_timeout=0.1,
        evidence=[],
        artifacts=[],
        known_failures=[],
        changed_files=[],
        generated_artifacts=[],
        approvals=[],
        confidence="",
        freshness_seconds=None,
        receipt_json=False,
    )
    assert cli_locking._cmd_release(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out
