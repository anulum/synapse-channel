# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — our own stack as the suite's system under test, through the six verbs
"""The reference system under test: this hub, its operation records and one Git executor.

The six verbs of the integrator contract, as the suite calls them:

1. ``register_executor``: the executor claims the variant's task;
2. ``propose``: it declares a Git publication with an authorisation intent;
3. ``obtain_authority``: a reviewer records an accepted verdict and an approver
   of the same project approves the intent's digest;
4. ``execute``: the executor asks the hub to open an attempt and, only on
   ``approved_fresh`` with an open attempt, moves the branch from exactly the
   base to the candidate;
5. ``follow``: the executor publishes its own outcome, which ends its attempt;
6. ``recover``: it asks again with the same request key and reads the stored
   answer with the attempt's present state.

The suite takes nothing from these calls as evidence of an effect; its own
observer reads the branch and the journal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND, format_approval_note
from synapse_channel.core.authorisation_intent import (
    INTENT_SCHEMA,
    compose_intent,
    parse_intent_statement,
)
from synapse_channel.core.execution_outcome import ExecutionOutcome, OutcomeState
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
from synapse_channel.effect_suite.workspace import BRANCH, CANDIDATE_PATH, PROJECT, REF, Repository
from synapse_channel.git.compare_and_swap import SwapResult, compare_and_swap

EXECUTOR = f"{PROJECT}/executor"
REVIEWER = f"{PROJECT}/reviewer"
APPROVER = f"{PROJECT}/approver"
REVIEW_INPUT = "1" * 64
"""The declared input the reviewer's verdict reads."""


@dataclass(frozen=True)
class Proposal:
    """What one declaration fixed: the operation, its candidate and its intent's digest."""

    operation_id: str
    candidate: str
    digest: str
    answer: str


@dataclass(frozen=True)
class Execution:
    """What one request to open an attempt led to, as the executor saw it."""

    answer: str
    attempt_state: str
    swap: SwapResult | None


@dataclass
class ReferenceAdapter:
    """Our stack, driven through the six verbs over one hub.

    Parameters
    ----------
    repository : Repository
        The system of record.
    executor, reviewer, approver : HubSession
        Connected sessions of the three identities.
    task_id : str
        The task the operations belong to.
    deadline : int, optional
        The intents' deadline in UNIX seconds; an hour after the adapter was made.
    """

    repository: Repository
    executor: HubSession
    reviewer: HubSession
    approver: HubSession
    task_id: str
    epoch: int = 0
    proposals: dict[str, Proposal] = field(default_factory=dict)
    deadline: int = field(default_factory=lambda: int(time.time()) + 3600)

    async def register_executor(self) -> int:
        """Claim the task and keep the granted epoch."""
        grant = await self.executor.after(
            lambda: self.executor.agent.claim(self.task_id, note="effect suite"),
            lambda frame: (
                frame.get("type") == MessageType.CLAIM_GRANTED
                and frame.get("task_id") == self.task_id
            ),
            what="a claim grant",
        )
        self.epoch = int(grant["epoch"])
        return self.epoch

    def statement(self, candidate: str, *, tool_version: str = "1.0.0") -> dict[str, Any]:
        """Return the intent statement of publishing ``candidate`` onto the branch from B."""
        return {
            "schema": INTENT_SCHEMA,
            "pre_state": self.repository.commits.b,
            "changed_files": [{"path": CANDIDATE_PATH, "mode": "100644"}],
            "recovery_class": "reversible",
            "deadline": self.deadline,
            "budget": "not_applicable",
            "egress": [],
            "tool": {"name": "effect-suite-executor", "version": tool_version},
            "arguments_sha256": "e" * 64,
            "rule_generation": "prose-rules:2026-11-12T06:16:35Z",
            "required_assurance": "cooperative",
        }

    def target(self, candidate: str, *, branch: str = BRANCH) -> dict[str, str]:
        """Return the Git publication's identifiers."""
        return {"repository": "system-of-record", "branch": branch, "revision": candidate}

    def digest_of(
        self,
        operation_id: str,
        candidate: str,
        *,
        branch: str = BRANCH,
        tool_version: str = "1.0.0",
    ) -> str:
        """Return the digest the hub would compose for these identifiers and this executor."""
        statement = parse_intent_statement(
            self.statement(candidate, tool_version=tool_version), "git_publication"
        )
        return compose_intent(
            statement,
            task_id=self.task_id,
            operation_id=operation_id,
            effect="git_publication",
            target=self.target(candidate, branch=branch),
            actor=EXECUTOR,
        ).sha256

    async def propose(self, operation_id: str, candidate: str) -> Proposal:
        """Declare the publication of ``candidate`` with its intent and keep the answer."""
        result = await self.executor.ask(
            MessageType.OPERATION_RECORD,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_RECORD_RESULT
                and frame.get("request_id") == f"declare-{operation_id}-{len(self.proposals)}"
            ),
            what="a declaration answer",
            record="declare",
            task_id=self.task_id,
            operation_id=operation_id,
            epoch=self.epoch,
            effect="git_publication",
            effect_target=self.target(candidate),
            intent=self.statement(candidate),
            request_id=f"declare-{operation_id}-{len(self.proposals)}",
        )
        status = str(result.get("status") or "")
        answer = f"declare:{status}" if status == "accepted" else f"declare:{result.get('reason')}"
        proposal = Proposal(operation_id, candidate, str(result.get("intent_sha256") or ""), answer)
        self.proposals.setdefault(operation_id, proposal)
        return proposal

    async def _note(self, session: HubSession, kind: str, text: str) -> bool:
        """Post one progress note; return whether the hub posted it."""
        frame = await session.after(
            lambda: session.agent.post_progress(self.task_id, text, kind=kind),
            lambda f: (
                (f.get("type") == "ledger_progress_posted" and f["note"]["text"] == text)
                or (f.get("type") == MessageType.ERROR and kind in str(f.get("payload", "")))
            ),
            what=f"a {kind} note answer",
        )
        return frame.get("type") == "ledger_progress_posted"

    async def obtain_authority(
        self, digest: str, *, review: bool = True, reason: str = "effect suite"
    ) -> bool:
        """Review (when asked) and approve ``digest``; return whether the approval was posted."""
        if review:
            for item in (REVIEW_INPUT, digest):
                await self._note(
                    self.reviewer,
                    EVIDENCE_NOTE_KIND,
                    format_evidence(EvidenceRecord(item, PROJECT)),
                )
            verdict = VerdictRecord(PROJECT, digest, "accepted", (REVIEW_INPUT,), (), REVIEWER, 1)
            await self._note(self.reviewer, VERDICT_NOTE_KIND, format_verdict(verdict))
        text = format_approval_note(subject=f"sha256:{digest}", state="approved", reason=reason)
        return await self._note(self.approver, APPROVAL_NOTE_KIND, text)

    def _attempt_fields(
        self, operation_id: str, digest: str, key: str, epoch: int | None
    ) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "operation_id": operation_id,
            "epoch": self.epoch if epoch is None else epoch,
            "intent_sha256": digest,
            "idem_key": key,
        }

    async def request(
        self,
        operation_id: str,
        digest: str,
        key: str,
        *,
        epoch: int | None = None,
        session: HubSession | None = None,
    ) -> dict[str, Any]:
        """Ask the hub to open an attempt and return its whole answer frame.

        ``session`` asks as another identity, for example one without the claim.
        """
        asker = self.executor if session is None else session
        return await asker.ask(
            MessageType.OPERATION_ATTEMPT,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
                and frame.get("request_key") == key
            ),
            what="an attempt answer",
            **self._attempt_fields(operation_id, digest, key, epoch),
        )

    async def requests_in_flight(
        self, operation_id: str, digest: str, keys: list[str]
    ) -> list[dict[str, Any]]:
        """Send every request before reading any answer; return the answers in sending order."""
        mark = self.executor.mark()
        for key in keys:
            await self.executor.agent.send_message(
                MessageType.OPERATION_ATTEMPT,
                target="System",
                **self._attempt_fields(operation_id, digest, key, None),
            )
        answers = await self.executor.wait_count(
            mark,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
                and frame.get("operation_id") == operation_id
            ),
            len(keys),
            what="the attempt answers",
        )
        return answers

    def publish(self, candidate: str) -> SwapResult:
        """Move the branch from exactly the base to ``candidate``: the one effect."""
        repository = self.repository
        return compare_and_swap(
            repository.path, REF, new=candidate, expected=repository.commits.b, git=repository.git
        )

    async def execute(
        self,
        operation_id: str,
        digest: str,
        key: str,
        *,
        decline: bool = False,
    ) -> Execution:
        """Ask; publish only on a fresh approval with an open attempt; then report the outcome.

        ``decline`` leaves the branch alone after the attempt opened, and the
        outcome then says the effect was not started.
        """
        answer = await self.request(operation_id, digest, key)
        name = str(answer.get("answer") or "")
        state = str(answer.get("live", {}).get("attempt_state") or "none")
        if name != "approved_fresh" or state != "open":
            return Execution(name, state, None)
        swap = None if decline else self.publish(self.proposals[operation_id].candidate)
        await self.follow(operation_id, swap)
        return Execution(name, state, swap)

    async def follow(self, operation_id: str, swap: SwapResult | None) -> str:
        """Publish the executor's own outcome, which ends its attempt; return the hub's status."""
        candidate = self.proposals[operation_id].candidate
        if swap is None:
            state = OutcomeState.NOT_STARTED
        elif swap.applied or swap.head == candidate:
            state = OutcomeState.CONFIRMED_DONE
        else:
            state = OutcomeState.CONFIRMED_FAILED
        report = ExecutionOutcome(operation_id, "worker_process", state, "effect suite executor")
        result = await self.executor.ask(
            MessageType.OPERATION_RECORD,
            lambda frame: (
                frame.get("type") == MessageType.OPERATION_RECORD_RESULT
                and frame.get("request_id") == f"outcome-{operation_id}"
            ),
            what="an outcome answer",
            record="outcome",
            task_id=self.task_id,
            operation_id=operation_id,
            report=report.to_json(),
            request_id=f"outcome-{operation_id}",
        )
        return str(result.get("status") or "")

    async def recover(self, operation_id: str, digest: str, key: str) -> dict[str, Any]:
        """Ask again with the same key: the stored answer and the attempt's present state."""
        return await self.request(operation_id, digest, key)
