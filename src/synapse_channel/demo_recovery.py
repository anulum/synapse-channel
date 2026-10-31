# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — crash-and-recovery demonstration
"""Show a conflict, a caught hand-off, a crash and a verified recovery on one real hub.

Two scripted in-process agents share a disposable Git workspace. The first one
holds the source file, writes half of its change and stops without releasing.
While its lease is live, the second agent can neither claim nor write the file.
Once the lease runs out, the second agent claims it under a new epoch, the
restarted first agent's release is refused because it no longer owns the task,
the half-written file is caught by result verification, and the finished change
is verified by its own test before release.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from synapse_channel import SynapseAgent, SynapseHub, __version__
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.release_verification import (
    build_verified_release_receipt,
    collect_git_state,
)
from synapse_channel.core.state import MINIMUM_TTL_SECONDS, GitContext
from synapse_channel.demo_runtime import (
    _SOURCE_PATH,
    _TEST_PATH,
    DemoInbox,
    _guard,
    _release_with_receipt,
    _seed_workspace,
    _start_local_hub,
)
from synapse_channel.result_verification import verify_file_change

RECOVERY_LEASE_SECONDS = MINIMUM_TTL_SECONDS
"""Lease of the agent that crashes; the hub's minimum lease, so the wait is short."""

RECLAIM_GRACE_SECONDS = 15.0
"""Time allowed after the lease should have expired before the demo fails loudly."""

RECOVERY_STEP_TITLES = {
    "01": "CONNECTED",
    "02": "CONFLICT REFUSED",
    "03": "HAND-OFF CAUGHT",
    "04": "CRASHED MID-CHANGE",
    "05": "HELD WHILE CRASHED",
    "06": "LEASE EXPIRED, RECLAIMED",
    "07": "OLD OWNER REFUSED",
    "08": "HALF-WRITTEN RESULT CAUGHT",
    "09": "RECOVERED AND VERIFIED",
}
"""Stable narration sequence of the recovery demonstration."""

_FIRST, _SECOND = "CLAUDE", "CODEX"
_TASK = "DEMO-RECOVERY"
_OVERLAP_TASK = "DEMO-RECOVERY-OVERLAP"
_PARTIAL = 'def coordination_status() -> str:\n    """Return the current handoff state."""\n'
_FINAL = _PARTIAL + '    return "recovered"\n'
_FINAL_TEST = (
    "import sys\nimport unittest\nfrom pathlib import Path\n\n"
    'sys.path.insert(0, str(Path(__file__).parents[1] / "src"))\n'
    "from shared import coordination_status\n\n\n"
    "class CoordinationStatusTest(unittest.TestCase):\n"
    "    def test_status(self) -> None:\n"
    '        self.assertEqual(coordination_status(), "recovered")\n\n\n'
    'if __name__ == "__main__":\n    unittest.main()\n'
)


@dataclass(frozen=True)
class RecoveryStep:
    """One observed milestone of the recovery demonstration.

    Attributes
    ----------
    key : str
        Stable two-digit sequence key.
    title : str
        Operator-facing milestone name.
    detail : str
        What the hub or the verifier actually answered.
    """

    key: str
    title: str
    detail: str


@dataclass
class RecoveryResult:
    """Evidence of one complete recovery run.

    Attributes
    ----------
    version : str
        The package version that ran.
    steps : list[RecoveryStep]
        Observed milestones in order.
    first_epoch, recovered_epoch : int
        Lease epochs before the crash and after the reclaim.
    partial_result, final_result : str
        Result-verification verdicts for the half-written and the finished file.
    receipt : dict[str, Any]
        The release receipt the hub accepted for the recovered task.
    """

    version: str = __version__
    steps: list[RecoveryStep] = field(default_factory=list)
    first_epoch: int = 0
    recovered_epoch: int = 0
    partial_result: str = ""
    final_result: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        """Return whether every milestone and verdict was observed as required."""
        return (
            [step.key for step in self.steps] == list(RECOVERY_STEP_TITLES)
            and self.recovered_epoch > self.first_epoch > 0
            and self.partial_result != "verified"
            and self.final_result == "verified"
            and bool(self.receipt)
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the machine-readable evidence document."""
        return {
            "schema_version": 1,
            "version": self.version,
            "completed": self.completed,
            "steps": [step.__dict__ for step in self.steps],
            "first_epoch": self.first_epoch,
            "recovered_epoch": self.recovered_epoch,
            "partial_result": self.partial_result,
            "final_result": self.final_result,
            "receipt": self.receipt,
        }


def _answer(
    inbox: DemoInbox, kinds: tuple[str, ...], task_id: str
) -> Callable[[dict[str, Any]], bool]:
    """Match the hub's answer of one of ``kinds`` for ``task_id``."""
    return lambda message: message.get("type") in kinds and message.get("task_id") == task_id


class _Story:
    """Run the scenario steps against one hub and record what was observed."""

    def __init__(self, uri: str, workspace: Path, lease_seconds: float) -> None:
        self.uri = uri
        self.workspace = workspace
        self.lease_seconds = lease_seconds
        self.result = RecoveryResult()
        self.inboxes: dict[str, DemoInbox] = {}
        self.agents: dict[str, SynapseAgent] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    def note(self, key: str, detail: str) -> None:
        """Record and print one milestone."""
        step = RecoveryStep(key, RECOVERY_STEP_TITLES[key], detail)
        self.result.steps.append(step)
        print(f"• {step.key} {step.title}: {step.detail}")

    async def connect(self, name: str) -> SynapseAgent:
        """Connect one scripted agent under ``name`` and wait until it is ready."""
        inbox = DemoInbox()
        agent = SynapseAgent(name, inbox, uri=self.uri, verbose=False, machine_identity=False)
        self.inboxes[name], self.agents[name] = inbox, agent
        self.tasks[name] = asyncio.create_task(agent.connect())
        await agent.wait_until_ready(3.0)
        return agent

    async def ask(
        self, name: str, kinds: tuple[str, ...], task_id: str, send: Callable[[], Any]
    ) -> dict[str, Any]:
        """Send one request as ``name`` and return the hub's matching answer."""
        inbox = self.inboxes[name]
        start = len(inbox.messages)
        await send()
        return await inbox.wait_for(_answer(inbox, kinds, task_id), start=start)

    async def claim(self, name: str, task_id: str) -> dict[str, Any]:
        """Claim the source file for ``task_id`` as ``name``."""
        agent = self.agents[name]
        return await self.ask(
            name,
            (MessageType.CLAIM_GRANTED, MessageType.CLAIM_DENIED),
            task_id,
            lambda: agent.claim(
                task_id,
                ttl_seconds=self.lease_seconds,
                worktree=str(self.workspace.resolve()),
                paths=[_SOURCE_PATH.as_posix()],
                git=GitContext(branch="main", base="main", auto_release_on="manual").as_dict(),
            ),
        )

    async def guard(self, name: str, call_id: str) -> str:
        """Return the mutation verdict for ``name`` writing the source file."""
        verdict = await _guard(
            self.workspace, _SOURCE_PATH, call_id, provider=name, identity=name, uri=self.uri
        )
        return "allowed" if verdict.allowed else f"refused ({verdict.reason})"

    async def crash(self, name: str) -> None:
        """Stop ``name``'s connection abruptly, without a release."""
        self.agents[name].running = False
        task = self.tasks.pop(name)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def close(self) -> None:
        """Stop every connection still open."""
        for agent in self.agents.values():
            agent.running = False
        for task in self.tasks.values():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


def _require(condition: bool, failure: str) -> None:
    """Stop the demonstration with ``failure`` when a safety property did not hold."""
    if not condition:
        raise RuntimeError(failure)


async def _conflict_and_crash(story: _Story) -> None:
    """Run steps 01-04: two agents, a refused overlap, a caught hand-off and a crash."""
    await story.connect(_FIRST)
    await story.connect(_SECOND)
    story.note("01", f"{_FIRST} and {_SECOND} joined one hub over a disposable Git workspace")
    granted = await story.claim(_FIRST, _TASK)
    _require(granted.get("type") == MessageType.CLAIM_GRANTED, "the first claim was refused")
    story.result.first_epoch = int(granted.get("epoch") or 0)
    overlap = await story.claim(_SECOND, _OVERLAP_TASK)
    _require(overlap.get("type") == MessageType.CLAIM_DENIED, "an overlapping claim was granted")
    story.note("02", str(overlap.get("payload")))
    second = story.agents[_SECOND]
    stolen = await story.ask(
        _SECOND,
        (MessageType.HANDOFF_GRANTED, MessageType.HANDOFF_DENIED),
        _TASK,
        lambda: second.handoff(_TASK, _SECOND, note="take it over"),
    )
    _require(stolen.get("type") == MessageType.HANDOFF_DENIED, "a foreign hand-off succeeded")
    story.note("03", str(stolen.get("payload")))
    (story.workspace / _SOURCE_PATH).write_text(_PARTIAL, encoding="utf-8")
    await story.crash(_FIRST)
    story.note(
        "04",
        f"{_FIRST} wrote half of src/shared.py under epoch {story.result.first_epoch} "
        "and stopped without releasing",
    )


async def _hold_and_reclaim(story: _Story) -> None:
    """Run steps 05-07: hold the live lease, reclaim it after expiry, refuse the old owner."""
    verdict = await story.guard(_SECOND, "write-while-crashed")
    _require(verdict != "allowed", "the file was writable while its lease was live")
    held = await story.claim(_SECOND, _TASK)
    _require(held.get("type") == MessageType.CLAIM_DENIED, "a live lease was taken over")
    story.note("05", f"{_SECOND}'s write {verdict}; claim refused: {held.get('payload')}")
    started = time.monotonic()
    deadline = started + max(story.lease_seconds, MINIMUM_TTL_SECONDS) + RECLAIM_GRACE_SECONDS
    while True:
        await asyncio.sleep(1.0)
        reclaimed = await story.claim(_SECOND, _TASK)
        if reclaimed.get("type") == MessageType.CLAIM_GRANTED:
            break
        _require(time.monotonic() < deadline, "the expired lease could not be reclaimed")
    story.result.recovered_epoch = int(reclaimed.get("epoch") or 0)
    story.note(
        "06",
        f"after {time.monotonic() - started:.0f} s the lease expired; {_SECOND} holds "
        f"src/shared.py under epoch {story.result.recovered_epoch}",
    )
    await story.connect(_FIRST)
    first = story.agents[_FIRST]
    stale = await story.ask(
        _FIRST,
        (MessageType.RELEASE_GRANTED, MessageType.RELEASE_DENIED),
        _TASK,
        lambda: first.release(_TASK, epoch=story.result.first_epoch),
    )
    _require(stale.get("type") == MessageType.RELEASE_DENIED, "the stale owner released")
    story.note(
        "07",
        f"restarted {_FIRST}, holding only its expired epoch {story.result.first_epoch}, "
        f"tried to release; the hub refused by ownership: {stale.get('payload')}",
    )


async def _verify_and_release(story: _Story) -> None:
    """Run steps 08-09: catch the half-written file, then finish and verify it by its test."""
    source = story.workspace / _SOURCE_PATH
    wanted = hashlib.sha256(_FINAL.encode("utf-8")).hexdigest()
    tests = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
    partial = verify_file_change(source, wanted, operation_id=_TASK)
    story.result.partial_result = partial.result
    _require(partial.result != "verified", "a half-written file was reported as done")
    story.note("08", f"result verification of the half-written file: {partial.result}")
    _require(
        await story.guard(_SECOND, "write-after-reclaim") == "allowed", "reclaim gave no write"
    )
    source.write_text(_FINAL, encoding="utf-8")
    (story.workspace / _TEST_PATH).write_text(_FINAL_TEST, encoding="utf-8")
    final = verify_file_change(source, wanted, operation_id=_TASK, tests=tests, cwd=story.workspace)
    story.result.final_result = final.result
    _require(final.result == "verified", f"the finished change did not verify: {final.result}")
    state = collect_git_state(story.workspace)
    receipt = build_verified_release_receipt(
        task_id=_TASK,
        owner=_SECOND,
        commands=[tests, ["git", "diff", "--check"]],
        artifacts=[source, story.workspace / _TEST_PATH],
        changed_files=state.changed_files,
        git_head=state.head,
        git_tree=state.tree,
        cwd=story.workspace,
        command_timeout_seconds=30.0,
    )
    _require(not receipt["known_failures"], "; ".join(receipt["known_failures"]))
    second = story.agents[_SECOND]
    story.result.receipt = await _release_with_receipt(
        second, story.inboxes[_SECOND], _TASK, receipt
    )
    story.note(
        "09", f"{_SECOND} finished the change; its own test passed and the hub took the release"
    )


async def run_recovery_demo(
    workspace: Path, *, lease_seconds: float = RECOVERY_LEASE_SECONDS
) -> RecoveryResult:
    """Run the complete crash-and-recovery story against a fresh local hub.

    Parameters
    ----------
    workspace : pathlib.Path
        Empty directory that becomes the disposable Git workspace.
    lease_seconds : float, optional
        Lease requested by the agent that crashes. The hub raises anything
        below its minimum to that minimum.

    Returns
    -------
    RecoveryResult
        Every observed milestone and verdict; ``completed`` is true.

    Raises
    ------
    RuntimeError
        If any safety property does not hold.
    """
    _seed_workspace(workspace)
    hub = SynapseHub(hub_id="recovery-demo")
    server, uri = await _start_local_hub(hub, 0)
    story = _Story(uri, workspace, lease_seconds)
    try:
        await _conflict_and_crash(story)
        await _hold_and_reclaim(story)
        await _verify_and_release(story)
    finally:
        await story.close()
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server
    _require(story.result.completed, "the recovery story ended without every invariant")
    return story.result


def run_installed_recovery_demo(output_dir: Path | None = None) -> Path:
    """Run the recovery demonstration and write its evidence document.

    Parameters
    ----------
    output_dir : pathlib.Path or None, optional
        Directory for ``recovery-demo.json``. A temporary directory is used
        when omitted.

    Returns
    -------
    pathlib.Path
        The written evidence document.
    """
    target = output_dir or Path(tempfile.mkdtemp(prefix="synapse-recovery-demo-"))
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="synapse-recovery-workspace-") as scratch:
        result = asyncio.run(run_recovery_demo(Path(scratch)))
    evidence = target / "recovery-demo.json"
    evidence.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
    return evidence
