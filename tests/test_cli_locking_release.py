# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the lease-serialising CLI commands (lock/release)

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest

from hub_e2e_helpers import AgentHandle, _free_port, close_agents, connect_agent, running_hub
from synapse_channel import cli, cli_locking
from synapse_channel.cli_locking import AgentFactory
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


def test_parser_release_accepts_verified_receipt_file() -> None:
    args = cli.build_parser().parse_args(
        [
            "release",
            "studio-panel-enrich",
            "--name",
            "USER",
            "--receipt",
            "verified-receipt.json",
        ]
    )

    assert args.receipt == "verified-receipt.json"


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


async def test_release_ingests_verified_receipt_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    receipt_path = tmp_path / "verified-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "task_id": "studio-panel-enrich",
                "owner": "USER",
                "released": True,
                "evidence": ["command: pytest tests/test_cli_locking_release.py -q exit=0"],
                "artifacts": ["coverage.xml sha256=abc size=4"],
                "known_failures": [],
                "changed_files": ["src/synapse_channel/cli_locking.py"],
                "generated_artifacts": ["docs/_generated/capability_manifest.json"],
                "approvals": ["reviewed-by=owner"],
                "confidence": "observed",
                "freshness_seconds": 0.0,
                "verification": {
                    "commands": [],
                    "artifacts": [],
                    "changed_files": ["src/synapse_channel/cli_locking.py"],
                    "git_head": "abc",
                    "git_tree": "def",
                    "timestamp": 123.0,
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    async with running_hub(SynapseHub()) as (hub, uri):
        holder = await _claim(uri, "USER", "studio-panel-enrich")
        await close_agents(holder)
        code = await cli_locking._release(
            uri=uri,
            name="USER",
            task_id="studio-panel-enrich",
            receipt=receipt_path,
            receipt_json=True,
        )

    assert code == 0
    assert "studio-panel-enrich" not in hub.state.claims
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["evidence"] == ["command: pytest tests/test_cli_locking_release.py -q exit=0"]
    assert receipt["artifacts"] == ["coverage.xml sha256=abc size=4"]
    assert receipt["changed_files"] == ["src/synapse_channel/cli_locking.py"]
    assert receipt["confidence"] == "observed"


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
        receipt=None,
        receipt_json=False,
    )
    assert cli_locking._cmd_release(ns) == 1
    assert "Could not reach hub" in capsys.readouterr().out


# --- receipt validation branches ----------------------------------------------


def test_load_release_receipt_rejects_a_non_object(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt must be a JSON object"):
        cli_locking._load_release_receipt(path)


def test_receipt_list_rejects_a_non_string_list() -> None:
    with pytest.raises(ValueError, match="'evidence' must be a list of strings"):
        cli_locking._receipt_list({"evidence": [1, 2]}, "evidence", None)
    with pytest.raises(ValueError, match="'evidence' must be a list of strings"):
        cli_locking._receipt_list({"evidence": "not-a-list"}, "evidence", None)


def test_receipt_freshness_rejects_a_non_number() -> None:
    with pytest.raises(ValueError, match="'freshness_seconds' must be a number"):
        cli_locking._receipt_freshness({"freshness_seconds": "soon"}, None)
    assert cli_locking._receipt_freshness({"freshness_seconds": 5}, None) == 5.0
    assert cli_locking._receipt_freshness({}, 7.0) == 7.0
    assert cli_locking._receipt_freshness({}, None) is None


def test_validate_receipt_identity_rejects_mismatches() -> None:
    with pytest.raises(ValueError, match="task_id 'OTHER' does not match 'T1'"):
        cli_locking._validate_release_receipt_identity(
            {"task_id": "OTHER"}, task_id="T1", name="alice"
        )
    with pytest.raises(ValueError, match="owner 'bob' does not match 'alice'"):
        cli_locking._validate_release_receipt_identity(
            {"task_id": "T1", "owner": "bob"}, task_id="T1", name="alice"
        )
    # matching or absent identity fields pass silently
    cli_locking._validate_release_receipt_identity(
        {"task_id": "T1", "owner": "alice"}, task_id="T1", name="alice"
    )
    cli_locking._validate_release_receipt_identity({}, task_id="T1", name="alice")


async def test_release_rejects_a_malformed_receipt_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A receipt naming another task must refuse the release before connecting."""
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"task_id": "OTHER"}), encoding="utf-8")
    code = await cli_locking._release(
        uri=f"ws://127.0.0.1:{_free_port()}",
        name="alice",
        task_id="T1",
        receipt=str(receipt),
    )
    assert code == 1
    assert "invalid release receipt for 'T1'" in capsys.readouterr().out


# --- scripted-agent paths a live hub cannot exercise deterministically ---


class _ScriptedReleaseAgent:
    """Replays crafted release verdicts through the collector, without a hub."""

    frames: tuple[dict[str, Any], ...] = ()

    def __init__(self, name: str, callback: Any, **_kwargs: Any) -> None:
        self.name = name
        self.callback = callback
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
        # Park until teardown cancels the connect task (cancellable hang).
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return True

    async def release(self, task_id: str, **_kwargs: Any) -> None:
        del task_id
        for frame in self.frames:
            await self.callback(frame)


async def test_release_receipt_json_falls_back_when_the_hub_echoes_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A grant without a receipt still prints valid receipt JSON — and grants
    for another task or another owner never count as ours."""

    class _GrantWithoutReceipt(_ScriptedReleaseAgent):
        frames = (
            {"type": MessageType.RELEASE_GRANTED, "task_id": "other", "owner": "X"},
            {"type": MessageType.RELEASE_GRANTED, "task_id": "g", "owner": "someone-else"},
            {"type": MessageType.RELEASE_GRANTED, "task_id": "g", "owner": "X", "receipt": "no"},
        )

    code = await cli_locking._release(
        uri="ws://unused",
        name="X",
        task_id="g",
        receipt_json=True,
        agent_factory=cast("AgentFactory", _GrantWithoutReceipt),
        poll_interval=0.001,
    )
    assert code == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["task_id"] == "g"
    assert receipt["owner"] == "X"


async def test_release_with_no_verdict_explains_the_silent_outcome(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hub that never answers the release yields the silent-outcome guidance."""
    code = await cli_locking._release(
        uri="ws://unused",
        name="X",
        task_id="g",
        agent_factory=cast("AgentFactory", _ScriptedReleaseAgent),
        attempts=2,
        poll_interval=0.001,
    )
    assert code == 1
    assert "release refused for 'g': no response from hub" in capsys.readouterr().out
