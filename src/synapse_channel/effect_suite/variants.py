# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — what the suite does to the system under test in each variant
"""Drive the system under test through one variant and keep the answers it got.

Every driver works on one :class:`Stage`: a fresh system of record, a served
hub, the system under test and the suite's observer. It records each answer the
hub gave in order. What the variant must show is in
:mod:`synapse_channel.effect_suite.cases`; reading the result is the observer's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from synapse_channel.effect_suite.observer import Observer
from synapse_channel.effect_suite.reference_adapter import Proposal, ReferenceAdapter
from synapse_channel.effect_suite.session import HubSession
from synapse_channel.effect_suite.workspace import REF, Repository, git_out

OPERATION = "suite-op-1"


@dataclass
class Stage:
    """One variant's system of record, system under test, observer and answers."""

    repository: Repository
    sut: ReferenceAdapter
    observer: Observer
    stranger: HubSession
    answers: list[str] = field(default_factory=list)

    async def propose(self, operation_id: str = OPERATION, candidate: str = "") -> Proposal:
        """Declare a publication of ``candidate`` (X by default) and keep the answer."""
        proposal = await self.sut.propose(operation_id, candidate or self.repository.commits.x)
        self.answers.append(proposal.answer)
        return proposal

    async def approved(self, operation_id: str = OPERATION) -> Proposal:
        """Declare and obtain a reviewed approval of the intent."""
        proposal = await self.propose(operation_id)
        await self.sut.obtain_authority(proposal.digest)
        return proposal

    async def execute(self, proposal: Proposal, key: str, *, decline: bool = False) -> None:
        """Run the executor once and keep the hub's answer."""
        execution = await self.sut.execute(
            proposal.operation_id, proposal.digest, key, decline=decline
        )
        self.answers.append(execution.answer)

    async def ask(
        self,
        operation_id: str,
        digest: str,
        key: str,
        *,
        epoch: int | None = None,
        session: HubSession | None = None,
    ) -> None:
        """Send one request without executing and keep the hub's answer."""
        frame = await self.sut.request(operation_id, digest, key, epoch=epoch, session=session)
        self.answers.append(str(frame.get("answer")))

    async def observe(self, operation_id: str = OPERATION) -> None:
        """Let the observer publish its verification of the operation."""
        await self.observer.observe(operation_id, self.sut.proposals[operation_id].candidate)

    def move_branch(self, commit: str) -> None:
        """Move the branch from B to ``commit`` as someone other than the executor."""
        git_out(
            self.repository.path,
            ["update-ref", REF, commit, self.repository.commits.b],
            git=self.repository.git,
        )


Driver = Callable[[Stage], Awaitable[None]]


async def _published(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.execute(proposal, "k1")
    await stage.observe()


async def _unapproved(stage: Stage) -> None:
    proposal = await stage.propose()
    await stage.execute(proposal, "k1")


async def _approved_without_review(stage: Stage) -> None:
    proposal = await stage.propose()
    await stage.sut.obtain_authority(proposal.digest, review=False)
    await stage.execute(proposal, "k1")


async def _dry_run(stage: Stage) -> None:
    await stage.propose()


async def _unentitled(stage: Stage) -> None:
    proposal = await stage.propose()
    await stage.ask(OPERATION, proposal.digest, "stranger-1", session=stage.stranger)
    await stage.ask(OPERATION, proposal.digest, "stale-1", epoch=stage.sut.epoch + 7)


def _altered(**change: str) -> Driver:
    async def drive(stage: Stage) -> None:
        proposal = await stage.approved()
        commits = stage.repository.commits
        candidate = commits.x_other if change.get("candidate") else commits.x
        digest = stage.sut.digest_of(
            OPERATION,
            candidate,
            branch=change.get("branch", "main"),
            tool_version=change.get("tool_version", "1.0.0"),
        )
        await stage.ask(proposal.operation_id, digest, "k1")

    return drive


def _base_moved(role: str) -> Driver:
    async def drive(stage: Stage) -> None:
        proposal = await stage.approved()
        commits = stage.repository.commits
        stage.move_branch(commits.o if role == "o" else commits.a)
        await stage.execute(proposal, "k1")
        await stage.observe()

    return drive


async def _declared_twice(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.propose()
    await stage.execute(proposal, "k1")
    await stage.observe()


async def _started_again(stage: Stage) -> None:
    await _published(stage)
    proposal = stage.sut.proposals[OPERATION]
    await stage.execute(proposal, "k2")


async def _in_flight(stage: Stage, keys: list[str]) -> None:
    proposal = await stage.approved()
    frames = await stage.sut.requests_in_flight(OPERATION, proposal.digest, keys)
    stage.answers.extend(str(frame.get("answer")) for frame in frames)
    swap = stage.sut.publish(proposal.candidate)
    await stage.sut.follow(OPERATION, swap)
    await stage.observe()


async def _two_at_once(stage: Stage) -> None:
    await _in_flight(stage, ["k1", "k2"])


async def _repeated_unread(stage: Stage) -> None:
    await _in_flight(stage, ["k1", "k1"])


async def _other_operation(stage: Stage) -> None:
    first = await stage.approved()
    await stage.propose("suite-op-2")
    await stage.ask("suite-op-2", first.digest, "k1")


async def _declined_then_again(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.execute(proposal, "k1", decline=True)
    await stage.observe()
    await stage.execute(proposal, "k2")


async def _second_approval_while_open(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.ask(OPERATION, proposal.digest, "k1")
    await stage.sut.obtain_authority(proposal.digest, review=False, reason="a second approval")
    await stage.ask(OPERATION, proposal.digest, "k2")
    await stage.sut.follow(OPERATION, stage.sut.publish(proposal.candidate))
    await stage.observe()


async def _crash_and_restart(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.ask(OPERATION, proposal.digest, "k1")
    stage.sut.publish(proposal.candidate)
    recovered = await stage.sut.recover(OPERATION, proposal.digest, "k1")
    stage.answers.append(str(recovered.get("answer")))
    await stage.sut.follow(OPERATION, stage.sut.publish(proposal.candidate))
    await stage.observe()


async def _ended_unobserved(stage: Stage) -> None:
    proposal = await stage.approved()
    await stage.execute(proposal, "k1", decline=True)
    await stage.sut.obtain_authority(proposal.digest, review=False, reason="a new approval")
    await stage.execute(proposal, "k2")


DRIVERS: dict[str, Driver] = {
    "1": _published,
    "2a": _unapproved,
    "2b": _approved_without_review,
    "2c": _approved_without_review,
    "2d": _dry_run,
    "2e": _unentitled,
    "3a": _altered(candidate="other"),
    "3b": _altered(branch="other"),
    "3c": _altered(tool_version="2.0.0"),
    "3d": _base_moved("o"),
    "3e": _base_moved("a"),
    "4a": _declared_twice,
    "4b": _started_again,
    "4c": _two_at_once,
    "4d": _other_operation,
    "4e": _repeated_unread,
    "4f": _declined_then_again,
    "4g": _second_approval_while_open,
    "4h": _crash_and_restart,
    "4i": _ended_unobserved,
}
"""The driver of every applicable variant, by name."""

VERDICT_GATE_OFF = frozenset({"2c"})
"""Variants whose hub runs with the verdict gate switched off."""
