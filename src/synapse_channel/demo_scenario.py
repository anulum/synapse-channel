# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — golden-demo scenario
"""Orchestrate the claim, conflict, refusal, handoff, and receipt story."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel import SynapseAgent, SynapseHub
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_verification import (
    VerifiedReleaseReceipt,
    build_verified_release_receipt,
    collect_git_state,
)
from synapse_channel.core.state import GitContext
from synapse_channel.dashboard import DashboardSnapshot, fetch_dashboard_snapshot
from synapse_channel.demo_runtime import (
    _SOURCE_PATH,
    _TEST_PATH,
    DemoInbox,
    _await_listening,
    _guard,
    _post_story,
    _release_with_receipt,
    _seed_workspace,
)
from synapse_channel.file_claim_guard import GuardVerdict

_CLAUDE_TASK = "DEMO-CLAUDE"
_CODEX_TASK = "DEMO-CODEX"
_CONFLICT_TASK = "DEMO-CONFLICT"


@dataclass(frozen=True)
class DemoStep:
    """One dashboard-visible milestone in the golden demo.

    Attributes
    ----------
    key : str
        Stable two-digit sequence key.
    title : str
        Concise uppercase operator-facing milestone.
    detail : str
        Evidence-backed explanation rendered in console and dashboard output.
    status : str
        Completion state used by the aggregate invariant. Defaults to
        ``"passed"``.
    """

    key: str
    title: str
    detail: str
    status: str = "passed"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serialisable representation of the milestone."""
        return {
            "key": self.key,
            "title": self.title,
            "detail": self.detail,
            "status": self.status,
        }

    def progress_text(self) -> str:
        """Return the exact progress-ledger text rendered by the dashboard."""
        return f"{self.key} {self.title} — {self.detail}"


@dataclass(frozen=True)
class GoldenDemoResult:
    """Evidence returned by the complete five-minute coordination scenario.

    Attributes
    ----------
    steps : tuple[DemoStep, ...]
        Ordered dashboard-visible milestones.
    guard_before_handoff, guard_after_handoff : GuardVerdict
        Mutation-authority decisions proving denial before and allowance after
        the atomic handoff.
    receipt : VerifiedReleaseReceipt
        Observed command, artifact, Git, and epistemic release evidence.
    dashboard : DashboardSnapshot
        Live hub snapshot used to render the static operator story.
    narration : tuple[str, ...]
        Human-readable console lines in execution order.
    """

    steps: tuple[DemoStep, ...]
    guard_before_handoff: GuardVerdict
    guard_after_handoff: GuardVerdict
    receipt: VerifiedReleaseReceipt
    dashboard: DashboardSnapshot
    narration: tuple[str, ...]

    @property
    def completed(self) -> bool:
        """Return whether every safety and evidence invariant passed."""
        return (
            all(step.status == "passed" for step in self.steps)
            and not self.guard_before_handoff.allowed
            and self.guard_after_handoff.allowed
            and self.receipt["epistemic_status"] == "supported"
            and not self.receipt["known_failures"]
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete machine-readable demo evidence document."""
        return {
            "schema_version": 1,
            "completed": self.completed,
            "steps": [step.to_dict() for step in self.steps],
            "guard": {
                "before_handoff": {
                    "allowed": self.guard_before_handoff.allowed,
                    "reason": self.guard_before_handoff.reason,
                },
                "after_handoff": {
                    "allowed": self.guard_after_handoff.allowed,
                    "reason": self.guard_after_handoff.reason,
                },
            },
            "receipt": dict(self.receipt),
            "dashboard": self.dashboard.to_dict(),
            "narration": list(self.narration),
        }


def _require_separate_claims(claude: GuardVerdict, codex: GuardVerdict) -> None:
    """Require both disjoint provider mutations to be authorised."""
    if not claude.allowed or not codex.allowed:
        raise RuntimeError("separate claim ownership was not enforceable")


def _require_denied(verdict: GuardVerdict) -> None:
    """Require an unsafe pre-handoff mutation to be denied."""
    if verdict.allowed:
        raise RuntimeError("unsafe Codex mutation was not refused")


def _require_allowed(verdict: GuardVerdict) -> None:
    """Require the post-handoff mutation authority transfer to succeed."""
    if not verdict.allowed:
        raise RuntimeError(f"handoff did not transfer mutation authority: {verdict.reason}")


def _require_clean_receipt(receipt: VerifiedReleaseReceipt) -> None:
    """Require every observed release command and artifact check to pass."""
    if receipt["known_failures"]:
        raise RuntimeError("; ".join(receipt["known_failures"]))


def _require_completed(result: GoldenDemoResult) -> None:
    """Require the aggregate golden-demo invariant before returning evidence."""
    if not result.completed:
        raise RuntimeError("golden demo completed without satisfying every invariant")


async def _record_step(
    agent: SynapseAgent,
    inbox: DemoInbox,
    step: DemoStep,
    *,
    task_id: str = _CLAUDE_TASK,
) -> None:
    """Write one scenario milestone to the shared progress ledger."""
    await _post_story(agent, inbox, task_id=task_id, text=step.progress_text())


async def _run_golden_scenario(port: int, workspace: Path) -> GoldenDemoResult:
    """Execute the golden scenario against one prepared workspace."""
    _seed_workspace(workspace)
    log: list[str] = []
    steps: list[DemoStep] = []

    def narrate(step: DemoStep) -> None:
        steps.append(step)
        line = f"• {step.key} {step.title}: {step.detail}"
        log.append(line)
        print(line)

    hub = SynapseHub(hub_id="demo-hub")
    server = asyncio.create_task(hub.serve("localhost", port))
    uri = f"ws://localhost:{port}"
    claude_rx, codex_rx = DemoInbox(), DemoInbox()
    claude = SynapseAgent("CLAUDE", claude_rx, uri=uri, verbose=False, machine_identity=False)
    codex = SynapseAgent("CODEX", codex_rx, uri=uri, verbose=False, machine_identity=False)
    connections: list[asyncio.Task[None]] = []

    try:
        await _await_listening(port)
        connections = [
            asyncio.create_task(claude.connect()),
            asyncio.create_task(codex.connect()),
        ]
        await claude.wait_until_ready(3.0)
        await codex.wait_until_ready(3.0)

        await claude.post_task(_CLAUDE_TASK, title="Implement the shared change")
        await claude.post_task(_CODEX_TASK, title="Verify the shared change")
        await codex_rx.wait_for(
            lambda message: (
                message.get("type") == MessageType.LEDGER_TASK_POSTED
                and message.get("task", {}).get("task_id") == _CODEX_TASK
            )
        )
        installed = DemoStep(
            "01",
            "INSTALLED",
            "the demo started a disposable real Git workspace",
        )
        narrate(installed)
        await _record_step(claude, codex_rx, installed)

        connected = DemoStep(
            "02", "CONNECTED", "Claude and Codex are simultaneously online on one local hub"
        )
        narrate(connected)
        await _record_step(claude, codex_rx, connected)

        git_context = GitContext(branch="main", base="main", auto_release_on="manual").as_dict()
        root = str(workspace.resolve())
        await claude.claim(
            _CLAUDE_TASK,
            worktree=root,
            paths=[_SOURCE_PATH.as_posix()],
            git=git_context,
        )
        await codex.claim(
            _CODEX_TASK,
            worktree=root,
            paths=[_TEST_PATH.as_posix()],
            git=git_context,
        )
        await claude_rx.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == _CLAUDE_TASK
            )
        )
        await codex_rx.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_GRANTED
                and message.get("task_id") == _CODEX_TASK
            )
        )

        claude_safe = await _guard(
            workspace,
            _SOURCE_PATH,
            "claude-source",
            provider="Claude Code",
            identity="CLAUDE",
            uri=uri,
        )
        codex_safe = await _guard(
            workspace,
            _TEST_PATH,
            "codex-test",
            provider="Codex CLI",
            identity="CODEX",
            uri=uri,
        )
        _require_separate_claims(claude_safe, codex_safe)
        (workspace / _SOURCE_PATH).write_text(
            'def coordination_status() -> str:\n    """Return the current handoff state."""\n'
            '    return "claude-owned"\n',
            encoding="utf-8",
        )
        (workspace / _TEST_PATH).write_text(
            "import sys\n"
            "import unittest\n"
            "from pathlib import Path\n\n"
            'sys.path.insert(0, str(Path(__file__).parents[1] / "src"))\n'
            "from shared import coordination_status\n\n\n"
            "class CoordinationStatusTest(unittest.TestCase):\n"
            "    def test_status(self) -> None:\n"
            '        self.assertEqual(coordination_status(), "handoff-complete")\n\n\n'
            'if __name__ == "__main__":\n'
            "    unittest.main()\n",
            encoding="utf-8",
        )
        separate = DemoStep(
            "03",
            "SEPARATE CLAIMS",
            "Claude owns src/shared.py while Codex owns tests/test_shared.py",
        )
        narrate(separate)
        await _record_step(claude, codex_rx, separate)

        start = len(codex_rx.messages)
        await codex.claim(
            _CONFLICT_TASK,
            worktree=root,
            paths=[_SOURCE_PATH.as_posix()],
            git=git_context,
        )
        denied = await codex_rx.wait_for(
            lambda message: (
                message.get("type") == MessageType.CLAIM_DENIED
                and message.get("task_id") == _CONFLICT_TASK
            ),
            start=start,
        )
        conflict = DemoStep(
            "04",
            "CONFLICT REFUSED",
            str(denied.get("payload") or "overlapping file claim denied"),
        )
        narrate(conflict)
        await _record_step(codex, claude_rx, conflict)

        guard_before = await _guard(
            workspace,
            _SOURCE_PATH,
            "codex-before-handoff",
            provider="Codex CLI",
            identity="CODEX",
            uri=uri,
        )
        _require_denied(guard_before)
        refused = DemoStep("05", "MUTATION DENIED", guard_before.reason)
        narrate(refused)
        await _record_step(codex, claude_rx, refused)

        start = len(codex_rx.messages)
        await claude.handoff(
            _CLAUDE_TASK,
            "CODEX",
            note="implementation ready for verification and closeout",
        )
        await codex_rx.wait_for(
            lambda message: (
                message.get("type") == MessageType.HANDOFF_GRANTED
                and message.get("task_id") == _CLAUDE_TASK
                and message.get("owner") == "CODEX"
            ),
            start=start,
        )
        guard_after = await _guard(
            workspace,
            _SOURCE_PATH,
            "codex-after-handoff",
            provider="Codex CLI",
            identity="CODEX",
            uri=uri,
        )
        _require_allowed(guard_after)
        handed_off = DemoStep(
            "06",
            "HANDOFF",
            "Claude atomically transferred src/shared.py authority to Codex",
        )
        narrate(handed_off)
        await _record_step(claude, codex_rx, handed_off)

        (workspace / _SOURCE_PATH).write_text(
            'def coordination_status() -> str:\n    """Return the current handoff state."""\n'
            '    return "handoff-complete"\n',
            encoding="utf-8",
        )
        git_state = collect_git_state(workspace)
        receipt = build_verified_release_receipt(
            task_id=_CLAUDE_TASK,
            owner="CODEX",
            commands=[
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
                ["git", "diff", "--check"],
            ],
            artifacts=[workspace / _SOURCE_PATH, workspace / _TEST_PATH],
            changed_files=git_state.changed_files,
            git_head=git_state.head,
            git_tree=git_state.tree,
            cwd=workspace,
            command_timeout_seconds=30.0,
        )
        _require_clean_receipt(receipt)
        await _release_with_receipt(codex, codex_rx, _CODEX_TASK, receipt)
        await _release_with_receipt(codex, codex_rx, _CLAUDE_TASK, receipt)
        await codex.update_ledger_task(_CODEX_TASK, status="done")
        await codex.update_ledger_task(_CLAUDE_TASK, status="done")

        verified = DemoStep(
            "07",
            "VERIFIED RECEIPT",
            "real unittest and git diff checks passed; both changed files were SHA-256 recorded",
        )
        narrate(verified)
        await _record_step(codex, claude_rx, verified)

        dashboard = await fetch_dashboard_snapshot(
            uri=uri,
            name="DEMO/DASHBOARD",
            token=None,
            ready_timeout=3.0,
            response_timeout=3.0,
        )
        result = GoldenDemoResult(
            steps=tuple(steps),
            guard_before_handoff=guard_before,
            guard_after_handoff=guard_after,
            receipt=receipt,
            dashboard=dashboard,
            narration=tuple(log),
        )
        _require_completed(result)
        return result
    finally:
        claude.running = False
        codex.running = False
        for task in (*connections, server):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
