# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the `syn ack` task completion helper

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from hub_e2e_helpers import _free_port, connect_agent, running_hub
from synapse_channel import ack
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType


class AckIdentity:
    """Small identity object matching the ergonomic command contract."""

    project = "SYNAPSE-CHANNEL"
    identity = "SYNAPSE-CHANNEL/codex-ack"


class ScriptedAgent:
    """Fake acknowledgement client for confirmation-failure branches."""

    def __init__(
        self,
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        mode: str,
        uri: str,
        verbose: bool,
        token: str | None,
    ) -> None:
        self.name = name
        self.callback = callback
        self.mode = mode
        self.uri = uri
        self.verbose = verbose
        self.token = token
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
        """Stay alive until the acknowledgement flow cancels the connection."""
        while self.running:
            await asyncio.sleep(0.01)

    async def wait_until_ready(self, timeout: float = 5.0) -> bool:
        """Report a ready fake connection."""
        return True

    async def post_progress(self, task_id: str, text: str, *, kind: str = "note") -> None:
        """Emit the scripted progress response."""
        if self.mode == "progress_missing":
            return
        if self.mode == "progress_error":
            await self.callback(
                {
                    "type": MessageType.ERROR,
                    "target": self.name,
                    "text": "progress rejected",
                }
            )
            return
        if self.mode == "progress_control_error":
            await self.callback(
                {
                    "type": MessageType.ERROR,
                    "target": self.name,
                    "text": "rejected\x1b]52;c;YQ==\x07\nforged\u202e",
                }
            )
            return
        await self.callback(
            {
                "type": MessageType.LEDGER_PROGRESS_POSTED,
                "note": {"task_id": task_id, "author": self.name, "kind": kind, "text": text},
            }
        )

    async def update_ledger_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        suggested_owner: str | None = None,
    ) -> None:
        """Emit the scripted task-update response."""
        del suggested_owner
        if self.mode == "update_missing":
            return
        if self.mode == "update_error":
            await self.callback(
                {
                    "type": MessageType.ERROR,
                    "target": self.name,
                    "payload": "update rejected",
                }
            )
            return
        await self.callback(
            {
                "type": MessageType.LEDGER_TASK_UPDATED,
                "task": {"task_id": task_id, "status": status},
            }
        )


def _scripted_factory(mode: str) -> ack.AgentFactory:
    """Build a fake agent factory for one acknowledgement branch."""

    def factory(
        name: str,
        callback: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        uri: str,
        verbose: bool,
        token: str | None,
    ) -> ScriptedAgent:
        return ScriptedAgent(
            name,
            callback,
            mode=mode,
            uri=uri,
            verbose=verbose,
            token=token,
        )

    return factory


async def _declare_task(uri: str, task_id: str = "BUILD") -> None:
    """Declare one board task through the real hub protocol."""
    handle = await connect_agent("planner", uri)
    try:
        await handle.agent.post_task(task_id, title="Build feature")
        await handle.recorder.wait_for(
            lambda msg: msg.get("type") == MessageType.LEDGER_TASK_POSTED
        )
    finally:
        await handle.close()


def test_build_ack_text_requires_evidence_or_artifact() -> None:
    with pytest.raises(ValueError, match="evidence.*artifact"):
        ack.build_ack_text(evidence=(), artifacts=(), note="")


def test_build_ack_text_includes_evidence_artifact_and_note() -> None:
    text = ack.build_ack_text(
        evidence=("pytest tests/test_ack.py -q", "mypy src/synapse_channel/ack.py"),
        artifacts=("coverage.xml",),
        note="Task is complete.",
    )

    assert text == (
        "ack evidence: pytest tests/test_ack.py -q; mypy src/synapse_channel/ack.py\n"
        "ack artifacts: coverage.xml\n"
        "ack note: Task is complete."
    )


def test_build_ack_text_accepts_artifact_only() -> None:
    assert ack.build_ack_text(evidence=(), artifacts=(" report.json ",), note="") == (
        "ack artifacts: report.json"
    )


async def test_ack_task_rejects_empty_task_id(capsys: pytest.CaptureFixture[str]) -> None:
    code = await ack.ack_task(
        AckIdentity(),
        task_id=" ",
        evidence=("pytest",),
        artifacts=(),
        note="",
        uri="ws://hub",
        agent_factory=_scripted_factory("success"),
    )

    assert code == 2
    assert "task id is required" in capsys.readouterr().err


async def test_ack_task_posts_assessment_and_marks_done(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with running_hub(SynapseHub()) as (hub, uri):
        await _declare_task(uri)

        code = await ack.ack_task(
            AckIdentity(),
            task_id="BUILD",
            evidence=("pytest tests/test_ack.py -q",),
            artifacts=("coverage.xml",),
            note="Ready.",
            uri=uri,
        )

        snapshot = hub.blackboard.snapshot()

    assert code == 0
    task = next(item for item in snapshot["tasks"] if item["task_id"] == "BUILD")
    assert task["status"] == "done"
    note = snapshot["progress"][-1]
    assert note["task_id"] == "BUILD"
    assert note["author"] == "SYNAPSE-CHANNEL/codex-ack"
    assert note["kind"] == "assessment"
    assert "ack evidence: pytest tests/test_ack.py -q" in note["text"]
    assert "ack artifacts: coverage.xml" in note["text"]
    assert "acked BUILD -> status=done" in capsys.readouterr().out


async def test_ack_task_uses_token(capsys: pytest.CaptureFixture[str]) -> None:
    token = "s3cret"
    async with running_hub(SynapseHub(authenticator=TokenAuthenticator([token]))) as (_hub, uri):
        planner = await connect_agent("planner", uri, token=token)
        try:
            await planner.agent.post_task("TOKEN", title="Token task")
            await planner.recorder.wait_for(
                lambda msg: msg.get("type") == MessageType.LEDGER_TASK_POSTED
            )

            code = await ack.ack_task(
                AckIdentity(),
                task_id="TOKEN",
                evidence=("token-auth path",),
                artifacts=(),
                note="",
                uri=uri,
                token=token,
            )
        finally:
            await planner.close()

    assert code == 0
    assert "acked TOKEN -> status=done" in capsys.readouterr().out


async def test_ack_task_returns_one_when_hub_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await ack.ack_task(
        AckIdentity(),
        task_id="BUILD",
        evidence=("pytest",),
        artifacts=(),
        note="",
        uri=f"ws://127.0.0.1:{_free_port()}",
        ready_timeout=0.1,
    )

    assert code == 1
    assert "Could not reach hub" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("progress_missing", "no progress confirmation"),
        ("progress_error", "progress rejected"),
        ("update_missing", "no done confirmation"),
        ("update_error", "update rejected"),
    ],
)
async def test_ack_task_reports_confirmation_failures(
    mode: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await ack.ack_task(
        AckIdentity(),
        task_id="BUILD",
        evidence=("pytest",),
        artifacts=(),
        note="",
        uri="ws://hub",
        attempts=1,
        agent_factory=_scripted_factory(mode),
    )

    assert code == 1
    assert expected in capsys.readouterr().err


async def test_ack_task_makes_hub_error_controls_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = await ack.ack_task(
        AckIdentity(),
        task_id="BUILD",
        evidence=("pytest",),
        artifacts=(),
        note="",
        uri="ws://hub",
        attempts=1,
        agent_factory=_scripted_factory("progress_control_error"),
    )

    rendered = capsys.readouterr().err
    assert code == 1
    assert "rejected\\x1b]52;c;YQ==\\x07\\nforged\\u202e" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\u202e" not in rendered


def test_message_text_falls_back_to_message_repr() -> None:
    assert ack._message_text({"type": "error"}) == "{'type': 'error'}"


def test_main_requires_evidence_or_artifact(capsys: pytest.CaptureFixture[str]) -> None:
    code = ack.main(AckIdentity(), ["BUILD"])

    assert code == 2
    assert "evidence" in capsys.readouterr().err


def test_main_parses_ack_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    identity = AckIdentity()

    async def fake_ack_task(
        identity: AckIdentity,
        *,
        task_id: str,
        evidence: Sequence[str],
        artifacts: Sequence[str],
        note: str,
        uri: str,
        token: str | None,
        ready_timeout: float,
    ) -> int:
        seen.update(
            {
                "identity": identity,
                "task_id": task_id,
                "evidence": tuple(evidence),
                "artifacts": tuple(artifacts),
                "note": note,
                "uri": uri,
                "token": token,
                "ready_timeout": ready_timeout,
            }
        )
        return 0

    monkeypatch.setattr(ack, "ack_task", fake_ack_task)

    code = ack.main(
        identity,
        [
            "BUILD",
            "--evidence",
            "pytest",
            "--evidence",
            "mypy",
            "--artifact",
            "coverage.xml",
            "--note",
            "done",
            "--uri",
            "ws://hub",
            "--token",
            "tok",
            "--ready-timeout",
            "1.5",
        ],
    )

    assert code == 0
    assert seen == {
        "identity": identity,
        "task_id": "BUILD",
        "evidence": ("pytest", "mypy"),
        "artifacts": ("coverage.xml",),
        "note": "done",
        "uri": "ws://hub",
        "token": "tok",
        "ready_timeout": 1.5,
    }


def test_syn_ack_is_packaged_and_documented() -> None:
    root = Path(__file__).resolve().parents[1]
    try:
        toml_parser = importlib.import_module("tomllib")
    except ModuleNotFoundError:  # pragma: no cover
        toml_parser = importlib.import_module("tomli")
    pyproject = toml_parser.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["project"]["scripts"]

    assert scripts["syn-ack"] == "synapse_channel.ergonomics:alias_ack"
    assert "syn ack" in (root / "README.md").read_text(encoding="utf-8")
    assert "syn ack <task>" in (root / "docs/cli.md").read_text(encoding="utf-8")
    assert "syn ack edit-api" in (root / "docs/recipes.md").read_text(encoding="utf-8")
