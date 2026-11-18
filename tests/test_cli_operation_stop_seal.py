# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse operation stop --seal` against a real hub and repository
"""A stop seals each listed attempt in Git first, records what it read back, trusts no report."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_operation_seal as seal_command
from synapse_channel import cli_operation_stop as stop_command
from synapse_channel import cli_operation_stop_seal as command
from synapse_channel.cli import build_parser
from synapse_channel.core.acl import OPERATION_RESUME, OPERATION_SEAL, AclPolicy, AclRule
from synapse_channel.core.attempt_markers import BASE_GONE, OTHER_PARTY_FIRST, AttemptMarker
from synapse_channel.core.execution_outcome import OutcomeState
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.operation_attempt_ledger import AttemptLedger
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.core.result_verification_record import ResultVerification
from synapse_channel.git.attempt_marker_changes import Target, apply_effect
from synapse_channel.git.git_runtime import GitError, git_executable, run_git

RECOVERY = "R/recovery"
SCOPE = "project:P"


def _policy(*tasks: str) -> AclPolicy:
    rules = list(scene.policy().rules)
    for task in tasks or ("T*",):
        rules.append(AclRule(OPERATION_SEAL, "claim", task, "R", "the recovery role seals"))
    rules.append(AclRule(OPERATION_RESUME, "stop-scope", "*", "H", "persons resume"))
    return AclPolicy(rules)


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    """Return a repository whose main holds B, and B and a child X of it."""
    git = git_executable()
    work = tmp_path / "system-of-record"
    work.mkdir(parents=True)
    for args in (
        ["init", "-q"],
        ["config", "user.name", "stop test"],
        ["config", "user.email", "stop-test@example.invalid"],
    ):
        assert run_git(git, work, args).returncode == 0
    tree = run_git(git, work, ["write-tree"]).stdout.strip()
    b = run_git(git, work, ["commit-tree", tree, "-m", "b"]).stdout.strip()
    x = run_git(git, work, ["commit-tree", tree, "-p", b, "-m", "x"]).stdout.strip()
    run_git(git, work, ["update-ref", "refs/heads/main", b])
    return work, b, x


async def _declared(
    worker: AgentHandle,
    epoch: int,
    task: str,
    operation_id: str,
    b: str,
    x: str,
    *,
    git: bool,
    assurance: str = "cooperative",
) -> str:
    """Declare a Git publication (or a file effect) with an intent; return the kept digest."""
    if git:
        effect: dict[str, Any] = {
            "effect": "git_publication",
            "effect_target": {"repository": "origin", "branch": "main", "revision": x},
            "intent": scene.statement(
                pre_state=b,
                changed_files=[{"path": "a.txt", "mode": "100644"}],
                required_assurance=assurance,
            ),
        }
    else:
        effect = {
            "effect": "file",
            "effect_target": {"path": "out/result.txt", "sha256": scene.TARGET_SHA},
            "intent": scene.statement(),
        }
    result = await scene.record(
        worker, record="declare", task_id=task, operation_id=operation_id, epoch=epoch, **effect
    )
    assert result["status"] == "accepted", result
    return str(result["intent_sha256"])


async def _attempt(
    worker: AgentHandle, epoch: int, task: str, operation_id: str, digest: str
) -> str:
    key = f"open-{operation_id}"
    fields: dict[str, Any] = {
        "task_id": task,
        "operation_id": operation_id,
        "epoch": epoch,
        "intent_sha256": digest,
        "idem_key": key,
    }
    opened = await scene.answer(
        worker,
        lambda: worker.agent.send_message(MessageType.OPERATION_ATTEMPT, **fields),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_ATTEMPT_RESULT
            and frame.get("request_key") == key
        ),
    )
    assert opened["answer"] == "approved_fresh", opened
    return str(opened["attempt_id"])


def _left(name: str) -> Callable[[dict[str, Any]], bool]:
    """Return a predicate for the hub's notice that ``name`` has left."""

    def left(frame: dict[str, Any]) -> bool:
        """Match the presence update that reports ``name`` gone."""
        return (
            frame.get("type") == MessageType.PRESENCE_UPDATE
            and frame.get("event") == "left"
            and frame.get("agent") == name
        )

    return left


async def _opened(
    uri: str,
    b: str,
    x: str,
    plan: tuple[tuple[str, str, bool], ...] = (("T1", "op-1", True),),
    *,
    assurance: str = "cooperative",
) -> list[tuple[str, str, str, str]]:
    """Open one attempt per (task, operation, git) in ``plan``; return each with its ids."""
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER)
    observer = await connect_agent("X/opened-observer", uri)
    worker, reviewer, approver = [await connect_agent(name, uri) for name in names]
    opened = []
    try:
        for task, operation_id, git in plan:
            epoch = await scene.claim(worker, task)
            digest = await _declared(
                worker, epoch, task, operation_id, b, x, git=git, assurance=assurance
            )
            await scene.review(reviewer, digest)
            await scene.approve(approver, digest)
            attempt = await _attempt(worker, epoch, task, operation_id, digest)
            opened.append((task, operation_id, digest, attempt))
    finally:
        await close_agents(worker, reviewer, approver)
        # A caller may connect one of these names at once; it is free only once the hub
        # has seen this connection leave, or that connect is refused as a live holder.
        try:
            for name in names:
                await observer.recorder.wait_for(_left(name), timeout=scene.WAIT)
        finally:
            await close_agents(observer)
    return opened


def _args(uri: str, work: Path, *extra: str) -> argparse.Namespace:
    argv = ["operation", "stop", "--name", RECOVERY, "--uri", uri, "--scope", SCOPE]
    argv += ["--reason", "an incident", "--ready-timeout", "15", "--timeout", "15"]
    argv += ["--idem-key", "s1", "--seal", "--repository", str(work), *extra]
    return build_parser(command="operation").parse_args(argv)


async def _command(watcher: AgentHandle, run: Any) -> int:
    """Run one command as the recovery identity and wait until the hub has released the name."""
    earlier = {id(frame) for frame in watcher.recorder.messages}
    code: int = await run
    await watcher.recorder.wait_for(
        lambda frame: (
            id(frame) not in earlier
            and frame.get("type") == MessageType.PRESENCE_UPDATE
            and frame.get("event") == "left"
            and frame.get("agent") == RECOVERY
        ),
        timeout=scene.WAIT,
    )
    return code


def _rows(store: EventStore, kind: str) -> list[dict[str, Any]]:
    return [dict(row.payload) for row in store.read_since(0, kinds=(kind,))]


def _stop_seq(store: EventStore) -> int:
    return store.read_since(0, kinds=(EventKind.OPERATION_STOPPED,))[0].seq


async def test_a_reported_attempt_is_sealed_anyway_and_the_late_effect_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The executor says nothing applied; the stop does not read it, seals, and the push fails."""
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, digest, attempt)] = await _opened(uri, b, x)
            watcher = await connect_agent("X/watcher", uri)
            try:
                fields = {"scope": SCOPE, "reason": "an incident", "idem_key": "s1"}
                recorded = await _command(
                    watcher,
                    stop_command.request_stop(
                        uri=uri,
                        actor=RECOVERY,
                        message_type=MessageType.OPERATION_STOP,
                        fields=fields,
                        token=None,
                        ready_timeout=scene.WAIT,
                        result_timeout=scene.WAIT,
                        json_output=False,
                    ),
                )
                worker = await connect_agent(scene.WORKER, uri)
                try:
                    report = scene.outcome(OutcomeState.NOT_STARTED, operation_id)
                    reported = await scene.record(worker, **report)
                finally:
                    await close_agents(worker)
                ledger = hub.operation_records.attempts
                seq = _stop_seq(store)
                closed_by_report = ledger.stop_closed(seq)
                state_after_report = ledger.attempt_state(operation_id, attempt)
                capsys.readouterr()
                sealed = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                printed = capsys.readouterr().out.splitlines()
                closed = ledger.stop_closed(seq)
            finally:
                await close_agents(watcher)
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
        seals = _rows(store, EventKind.OPERATION_SEALED)
    finally:
        store.close()
    git = git_executable()
    marker = AttemptMarker(operation_id, attempt, digest)
    late = apply_effect(Target(work, "main", b), marker, candidate=x, git=git)
    branch = run_git(git, work, ["rev-parse", "refs/heads/main"]).stdout.strip()
    assert recorded == stop_command.EXIT_RECORDED and reported["status"] == "accepted"
    assert (state_after_report, closed_by_report) == ("ended", False)
    assert sealed == seal_command.EXIT_DONE, printed
    assert printed[0] == (
        f"operation stop stop_recorded for {SCOPE}: "
        f"the scope {SCOPE} is stopped; 1 open attempt(s) listed"
    )
    assert printed[1].startswith(f"attempt {attempt}: sealed recorded at seq ")
    assert printed[2:] == [
        f"stop at journal seq {seq}: 1 of 1 listed attempt(s) closed",
        "request key: s1",
    ]
    assert closed is True and seals == []
    assert [(row["stop_seq"], row["result"], row["marker_object"]) for row in readbacks] == [
        (seq, "sealed", marker.objects("sha1").sealed)
    ]
    assert late.answer == OTHER_PARTY_FIRST and branch == b


async def test_an_attempt_reported_before_the_stop_is_listed_sealed_and_its_late_effect_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An outcome sent before the stop does not drop the attempt from listed; the seal stops it."""
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, digest, attempt)] = await _opened(uri, b, x)
            worker = await connect_agent(scene.WORKER, uri)
            try:
                reported = await scene.record(
                    worker, **scene.outcome(OutcomeState.NOT_STARTED, operation_id)
                )
            finally:
                await close_agents(worker)
            ledger = hub.operation_records.attempts
            state_before_stop = ledger.attempt_state(operation_id, attempt)
            watcher = await connect_agent("X/watcher", uri)
            try:
                capsys.readouterr()
                sealed = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                printed = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
            seq = _stop_seq(store)
            closed = ledger.stop_closed(seq)
            after_seal_states = [row["state"] for row in _states(store)]
            checker = await connect_agent(scene.CHECKER, uri)
            try:
                reconciled = await scene.record(checker, **_observed(operation_id))
            finally:
                await close_agents(checker)
        stopped_rows = _rows(store, EventKind.OPERATION_STOPPED)
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
        seals = _rows(store, EventKind.OPERATION_SEALED)
        final_states = [row["state"] for row in _states(store)]
    finally:
        store.close()
    git = git_executable()
    marker = AttemptMarker(operation_id, attempt, digest)
    late = apply_effect(Target(work, "main", b), marker, candidate=x, git=git)
    branch = run_git(git, work, ["rev-parse", "refs/heads/main"]).stdout.strip()
    assert (reported["status"], state_before_stop) == ("accepted", "ended")
    assert late.answer == OTHER_PARTY_FIRST and branch == b
    assert [item["attempt_id"] for item in stopped_rows[0]["listed"]] == [attempt]
    assert sealed == seal_command.EXIT_DONE, printed
    assert printed[0] == (
        f"operation stop stop_recorded for {SCOPE}: "
        f"the scope {SCOPE} is stopped; 1 open attempt(s) listed"
    )
    assert closed is True and seals == []
    assert [(row["stop_seq"], row["result"], row["marker_object"]) for row in readbacks] == [
        (seq, "sealed", marker.objects("sha1").sealed)
    ]
    assert after_seal_states == ["no new effect"]
    assert reconciled["status"] == "accepted" and final_states == ["no new effect", "at rest"]


async def test_an_unmarked_attempt_reported_before_the_stop_holds_stop_requested_and_keeps_actor(
    tmp_path: Path,
) -> None:
    """A file attempt ended by report keeps the stop at stop requested and blocks self-resume."""
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    project_resumes = AclRule(OPERATION_RESUME, "stop-scope", "project:*", "P", "project")
    policy = AclPolicy([*_policy().rules, project_resumes])
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=policy)) as (hub, uri):
            [(_task, operation_id, _digest, attempt)] = await _opened(
                uri, b, x, (("T1", "op-file", False),)
            )
            worker = await connect_agent(scene.WORKER, uri)
            try:
                reported = await scene.record(
                    worker, **scene.outcome(OutcomeState.CONFIRMED_DONE, operation_id)
                )
            finally:
                await close_agents(worker)
            watcher = await connect_agent("X/watcher", uri)
            try:
                code = await _command(watcher, command.stop_and_seal(_args(uri, work)))
            finally:
                await close_agents(watcher)
            seq = _stop_seq(store)
            stop_state = hub.operation_records.attempts.stop_state(seq)
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent("H/operator", uri)
            checker = await connect_agent(scene.CHECKER, uri)
            try:
                self_resume = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_RESUME,
                        scope=SCOPE,
                        reason="self",
                        case="only_person",
                        idem_key="r-self",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                lifted = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_RESUME,
                        scope=SCOPE,
                        reason="operator",
                        case="another_person",
                        idem_key="r-op",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                file_obs = ResultVerification(
                    operation_id,
                    "file_change",
                    "confirmed_done",
                    "absent",
                    "not_verified",
                    "checked",
                ).to_json()
                observed = await scene.record(
                    checker,
                    record="verification",
                    task_id="T1",
                    operation_id=operation_id,
                    verification=file_obs,
                )
                after_obs = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_STOP,
                        scope=SCOPE,
                        reason="after reconciliation",
                        idem_key="s2",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
            finally:
                await close_agents(worker, operator, checker)
        states_during_first = _states(store)
    finally:
        store.close()
    assert reported["status"] == "accepted"
    assert code == seal_command.EXIT_REFUSED
    assert stop_state is not None and stop_state.state == "stop requested"
    assert stop_state.blocking == ((operation_id, attempt, "unsealable"),)
    assert self_resume["answer"] == "actor_of_scope"
    assert (lifted["answer"], lifted["resumed_from"]) == ("resumed", "stop requested")
    assert observed["status"] == "accepted"
    assert (after_obs["answer"], after_obs["state"], after_obs["listed"]) == (
        "stop_recorded",
        "at rest",
        [],
    )
    assert [row["state"] for row in states_during_first] == ["at rest"]


async def test_an_open_attempt_is_sealed_with_the_stop_s_sequence_then_read_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, _digest, attempt)] = await _opened(uri, b, x)
            watcher = await connect_agent("X/watcher", uri)
            try:
                sealed = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                again = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                printed = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
            state = hub.operation_records.attempts.attempt_state(operation_id, attempt)
            closed = hub.operation_records.attempts.stop_closed(_stop_seq(store))
        seals = _rows(store, EventKind.OPERATION_SEALED)
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
        seq = _stop_seq(store)
    finally:
        store.close()
    assert (sealed, again, state, closed) == (
        seal_command.EXIT_DONE,
        seal_command.EXIT_DONE,
        "sealed",
        True,
    )
    assert [(row["attempt_id"], row["stop_seq"]) for row in seals] == [(attempt, seq)]
    assert [row["result"] for row in readbacks] == ["sealed"]
    assert f"attempt {attempt}: a closing read-back is already recorded" in printed


async def test_an_effect_that_landed_first_is_recorded_applied_and_closes_the_attempt(
    tmp_path: Path,
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, digest, attempt)] = await _opened(uri, b, x)
            marker = AttemptMarker(operation_id, attempt, digest)
            apply_effect(Target(work, "main", b), marker, candidate=x, git=git_executable())
            watcher = await connect_agent("X/watcher", uri)
            try:
                code = await _command(watcher, command.stop_and_seal(_args(uri, work)))
            finally:
                await close_agents(watcher)
            closed = hub.operation_records.attempts.stop_closed(_stop_seq(store))
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
    finally:
        store.close()
    assert (code, closed) == (seal_command.EXIT_DONE, True)
    assert [(row["result"], row["marker_object"]) for row in readbacks] == [
        ("applied", marker.objects("sha1").applied)
    ]


async def test_a_file_effect_is_unsealable_and_an_unreached_seal_is_unread_until_a_rerun(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    plan = (("T1", "op-git", True), ("T2", "op-file", False))
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [git_op, file_op] = await _opened(uri, b, x, plan)
            watcher = await connect_agent("X/watcher", uri)
            missing = str(tmp_path / "no-such-remote")
            try:
                unreached = await _command(
                    watcher, command.stop_and_seal(_args(uri, work, "--remote", missing))
                )
                first = capsys.readouterr().out.splitlines()
                rerun = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                second = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
            closed = hub.operation_records.attempts.stop_closed(_stop_seq(store))
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
    finally:
        store.close()
    assert (unreached, rerun, closed) == (
        seal_command.EXIT_REFUSED,
        seal_command.EXIT_REFUSED,
        False,
    )
    by_attempt = [(row["attempt_id"], row["result"], row.get("reason")) for row in readbacks]
    assert by_attempt[0][:2] == (git_op[3], "unread") and by_attempt[0][2] in (
        "not_reached",
        "unknown",
    )
    assert by_attempt[1:] == [
        (file_op[3], "unsealable", None),
        (git_op[3], "sealed", None),
        (file_op[3], "unsealable", None),
    ]
    assert first[-2].endswith("0 of 2 listed attempt(s) closed")
    assert second[-2].endswith("1 of 2 listed attempt(s) closed")


async def test_a_stopper_without_the_seal_grant_on_one_task_leaves_that_attempt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    plan = (("T1", "op-1", True), ("U2", "op-2", True))
    try:
        hub = SynapseHub(journal=store, acl_policy=_policy("T1"))
        async with running_hub(hub) as (_h, uri):
            [first, second] = await _opened(uri, b, x, plan)
            watcher = await connect_agent("X/watcher", uri)
            try:
                code = await _command(watcher, command.stop_and_seal(_args(uri, work)))
                printed = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
        readbacks = _rows(store, EventKind.OPERATION_STOP_READBACK)
    finally:
        store.close()
    assert code == seal_command.EXIT_REFUSED
    assert f"attempt {second[3]}: not_permitted on task U2" in printed
    assert [(row["attempt_id"], row["result"]) for row in readbacks] == [(first[3], "sealed")]


async def test_the_hub_keeps_a_read_back_only_as_it_recomputes_it(tmp_path: Path) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            [(task, operation_id, digest, attempt)] = await _opened(uri, b, x)
            recovery = await connect_agent(RECOVERY, uri)
            try:
                stop = await scene.answer(
                    recovery,
                    lambda: recovery.agent.send_message(
                        MessageType.OPERATION_STOP, scope=SCOPE, reason="r", idem_key="s1"
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                seq = int(stop["seq"])
                marker = AttemptMarker(operation_id, attempt, digest)
                objects = marker.objects("sha1")
                held = {"marker_ref": marker.ref, "object_format": "sha1"}

                async def readback(**body: Any) -> dict[str, Any]:
                    return await scene.record(
                        recovery,
                        record="stop_readback",
                        task_id=task,
                        operation_id=operation_id,
                        readback={"stop_seq": seq, "attempt_id": attempt, **body},
                    )

                answers = [
                    await readback(result="applied", marker_object=objects.sealed, **held),
                    await readback(result="sealed", marker_object=objects.sealed, **held),
                    await readback(result="unsealable"),
                    await readback(result="unread", reason="unknown", stop_seq=seq + 1000),
                ]
                seal = {
                    "attempt_id": attempt,
                    "marker_object": objects.sealed,
                    "observation": "landed",
                    "stop_seq": seq + 1000,
                    **held,
                }
                wrong_stop = await scene.record(
                    recovery, record="seal", task_id=task, operation_id=operation_id, seal=seal
                )
                kept = await readback(result="applied", marker_object=objects.applied, **held)
                closed_twice = await readback(
                    result="applied", marker_object=objects.applied, **held
                )
                unread = {"stop_seq": seq, "attempt_id": attempt, "result": "unread"}
                unknown = await scene.record(
                    recovery,
                    record="stop_readback",
                    task_id=task,
                    operation_id="op-none",
                    readback={**unread, "reason": "unknown"},
                )
                elsewhere = await scene.record(
                    recovery,
                    record="stop_readback",
                    task_id="T9",
                    operation_id=operation_id,
                    readback={**unread, "reason": "unknown"},
                )
            finally:
                await close_agents(recovery)
            worker = await connect_agent(scene.WORKER, uri)
            try:
                ungranted = await scene.record(
                    worker,
                    record="stop_readback",
                    task_id=task,
                    operation_id=operation_id,
                    readback={**unread, "reason": "unknown"},
                )
            finally:
                await close_agents(worker)
    finally:
        store.close()
    assert (unknown["reason"], elsewhere["reason"]) == ("unknown_operation", "task_mismatch")
    assert ungranted["reason"] == "not_authorised"
    assert [answer["reason"] for answer in answers] == [
        "readback_mismatch",
        "attempt_open",
        "readback_mismatch",
        "stop_unknown",
    ]
    assert wrong_stop["reason"] == "stop_unknown"
    assert kept["status"] == "accepted"
    assert closed_twice["reason"] == "readback_closed"


async def test_seal_without_a_repository_or_git_and_a_stop_not_recorded_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = build_parser().parse_args(
        ["operation", "stop", "--name", RECOVERY, "--scope", SCOPE, "--reason", "r", "--seal"]
    )
    assert parsed.func(parsed) == stop_command.EXIT_UNUSABLE
    assert (
        capsys.readouterr().out == "refused before contacting the hub: --seal needs --repository\n"
    )

    def no_git() -> str:
        raise GitError("git is not installed")

    monkeypatch.setattr(command, "git_executable", no_git)
    assert (
        await command.stop_and_seal(_args("ws://127.0.0.1:1", tmp_path))
        == seal_command.EXIT_UNUSABLE
    )
    assert "git is not installed" in capsys.readouterr().out
    monkeypatch.undo()
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            watcher = await connect_agent("X/watcher", uri)
            try:
                first = await _command(watcher, command.stop_and_seal(_args(uri, tmp_path)))
                capsys.readouterr()
                fresh = _args(uri, tmp_path)
                fresh.idem_key = "s2"
                second = await _command(watcher, command.stop_and_seal(fresh))
                printed = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
    finally:
        store.close()
    assert first == seal_command.EXIT_DONE
    assert second == seal_command.EXIT_REFUSED
    assert printed[0].startswith(f"operation stop already_stopped for {SCOPE}: ")
    assert printed[-1] == "request key: s2"


class _ScriptedHub:
    """A client whose hub answers each request with a scripted frame, or not at all."""

    def __init__(self, callback: Any, script: dict[str, dict[str, Any]], ready: bool) -> None:
        self.callback = callback
        self.script = script
        self.ready = ready
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""

    async def connect(self) -> None:
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def request_who(self, hub: str | None = None) -> None:
        del hub
        await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def send_message(self, msg_type: str, **fields: Any) -> None:
        reply = self.script.get(str(fields.get("record") or msg_type))
        if reply is None:
            return
        frame = dict(reply)
        if "request_id" in fields:
            frame["request_id"] = fields["request_id"]
        if "idem_key" in fields:
            frame["request_key"] = fields["idem_key"]
        await self.callback(frame)


ATTEMPT = "a" * 32
LISTED = {"operation_id": "op-1", "attempt_id": ATTEMPT, "task_id": "T1", "executor": "P/w"}
RECORDED = {
    "type": MessageType.OPERATION_STOP_RESULT,
    "answer": "stop_recorded",
    "payload": "stopped",
    "seq": 5,
    "listed": [LISTED],
}


def _state(**changes: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "type": MessageType.OPERATION_STATE_RESULT,
        "status": "accepted",
        "effect": "git_publication",
        "effect_target": {"repository": "origin", "branch": "main", "revision": "c" * 40},
        "intent_sha256": "d" * 64,
        "attempts": [{"attempt_id": ATTEMPT, "state": "open"}],
    }
    reply.update(changes)
    return reply


def _result(status: str = "accepted", reason: str = "") -> dict[str, Any]:
    return {
        "type": MessageType.OPERATION_RECORD_RESULT,
        "status": status,
        "reason": reason,
        "payload": reason or "kept",
        "seq": 9,
    }


async def _scripted(
    tmp_path: Path, script: dict[str, dict[str, Any]], *, ready: bool = True
) -> int:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> Any:
        return _ScriptedHub(callback, script, ready)

    work, _b, _x = _repository(tmp_path)
    args = _args("ws://hub", work)
    args.timeout = args.ready_timeout = 0.2
    return await command.stop_and_seal(args, factory)


async def test_a_silent_or_unknowing_hub_and_each_refusal_of_an_attempt_have_their_answers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = MessageType.OPERATION_STOP
    state = MessageType.OPERATION_STATE
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_stop'."}
    assert await _scripted(tmp_path / "1", {}) == seal_command.EXIT_NO_VERDICT
    assert capsys.readouterr().out.splitlines() == [
        "operation stop failed: the hub returned no answer",
        "request key: s1",
    ]
    assert await _scripted(tmp_path / "2", {stop: unknown}) == seal_command.EXIT_UNSUPPORTED
    capsys.readouterr()
    assert await _scripted(tmp_path / "3", {}, ready=False) == seal_command.EXIT_NO_VERDICT
    capsys.readouterr()
    gone = _state(status="refused", reason="unknown_operation", payload="no such operation")
    assert await _scripted(tmp_path / "4", {stop: RECORDED, state: gone}) == (
        seal_command.EXIT_REFUSED
    )
    assert f"attempt {ATTEMPT}: operation state refused (unknown_operation)" in (
        capsys.readouterr().out
    )
    nameless = _state(intent_sha256="")
    assert await _scripted(tmp_path / "5", {stop: RECORDED, state: nameless}) == (
        seal_command.EXIT_REFUSED
    )
    assert f"attempt {ATTEMPT}: no marker can be named" in capsys.readouterr().out
    refused_seal = {stop: RECORDED, state: _state(), "seal": _result("refused", "attempt_ended")}
    assert await _scripted(tmp_path / "6", refused_seal) == seal_command.EXIT_REFUSED
    assert "; not recorded" in capsys.readouterr().out
    unsealable = {
        stop: RECORDED,
        state: _state(effect="file"),
        "stop_readback": _result("refused", "readback_mismatch"),
    }
    assert await _scripted(tmp_path / "7", unsealable) == seal_command.EXIT_REFUSED
    assert "read-back refused (readback_mismatch)" in capsys.readouterr().out
    monkeypatch.setattr(
        command, "seal", lambda *_a, **_k: SimpleNamespace(answer=BASE_GONE, marker=None)
    )
    unread = {stop: RECORDED, state: _state(), "stop_readback": _result()}
    assert await _scripted(tmp_path / "8", unread) == seal_command.EXIT_REFUSED
    assert f"attempt {ATTEMPT}: the seal was not read back (base_gone)" in capsys.readouterr().out


def test_the_stop_command_dispatches_a_seal_with_a_key_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[argparse.Namespace] = []

    async def capture(args: argparse.Namespace) -> int:
        seen.append(args)
        return seal_command.EXIT_DONE

    monkeypatch.setattr(stop_command, "stop_and_seal", capture)
    argv = ["operation", "stop", "--name", RECOVERY, "--scope", SCOPE, "--reason", "r"]
    parsed = build_parser().parse_args([*argv, "--seal", "--repository", "/repo"])
    assert parsed.func(parsed) == seal_command.EXIT_DONE
    assert len(seen[0].idem_key) == 32 and seen[0].repository == "/repo"


def _observed(operation_id: str, effect: str = "absent") -> dict[str, Any]:
    """Return a Git verification record of ``operation_id`` whose effect is ``effect``."""
    body = ResultVerification(
        operation_id, "git_publication", "confirmed_done", effect, "not_verified", "stop test"
    ).to_json()
    return {
        "record": "verification",
        "task_id": "T1",
        "operation_id": operation_id,
        "verification": body,
    }


def _states(store: EventStore) -> list[dict[str, Any]]:
    return _rows(store, EventKind.OPERATION_STOP_STATE)


async def test_a_cooperative_stop_reports_each_advance_once_and_never_isolated(
    tmp_path: Path,
) -> None:
    """Isolated intents still read cooperative; only another identity's observation rests it."""
    work, b, x = _repository(tmp_path)
    path = tmp_path / "hub.db"
    store = EventStore(path)
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, _digest, _attempt)] = await _opened(
                uri, b, x, assurance="isolated"
            )
            watcher = await connect_agent("X/watcher", uri)
            try:
                code = await _command(watcher, command.stop_and_seal(_args(uri, work)))
            finally:
                await close_agents(watcher)
            after_seal = list(_states(store))
            worker = await connect_agent(scene.WORKER, uri)
            checker = await connect_agent(scene.CHECKER, uri)
            try:
                own = await scene.record(worker, **_observed(operation_id))
                after_own = list(_states(store))
                first = await scene.record(checker, **_observed(operation_id))
                second = await scene.record(checker, **_observed(operation_id))
            finally:
                await close_agents(worker, checker)
            seq = _stop_seq(store)
            live = hub.operation_records.attempts.stop_state(seq)
        rows = _states(store)
    finally:
        store.close()
    store = EventStore(path)
    try:
        restored = AttemptLedger(store).stop_state(seq)
    finally:
        store.close()
    assert code == seal_command.EXIT_DONE
    assert [row["state"] for row in after_seal] == ["no new effect"]
    assert (own["status"], after_own) == ("accepted", after_seal)
    assert (first["status"], second["status"]) == ("accepted", "accepted")
    assert [row["state"] for row in rows] == ["no new effect", "at rest"]
    says = "governed effects only: a writer that does not ask the hub is not stopped by this stop"
    for row in rows:
        assert (row["stop_seq"], row["scope"], row["epoch"]) == (seq, SCOPE, 1)
        assert (row["profile"], row["profile_says"]) == ("cooperative", says)
    assert rows[0]["counts"]["sealed"] == 1 and rows[1]["counts"]["reconciled"] == 1
    assert rows[1]["blocking"] == []
    assert live is not None and restored == live


async def test_an_unsealable_attempt_holds_the_stop_and_a_resume_keeps_the_state_it_found(
    tmp_path: Path,
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    plan = (("T1", "op-git", True), ("T2", "op-file", False))
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [git_op, file_op] = await _opened(uri, b, x, plan)
            watcher = await connect_agent("X/watcher", uri)
            try:
                code = await _command(watcher, command.stop_and_seal(_args(uri, work)))
            finally:
                await close_agents(watcher)
            worker = await connect_agent(scene.WORKER, uri)
            operator = await connect_agent("H/operator", uri)
            try:
                state = await scene.answer(
                    worker,
                    lambda: worker.agent.send_message(
                        MessageType.OPERATION_STATE,
                        task_id="T2",
                        operation_id="op-file",
                        request_id="r1",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STATE_RESULT,
                )
                resumed = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_RESUME,
                        scope=SCOPE,
                        reason="r",
                        case="another_person",
                        idem_key="r1",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
            finally:
                await close_agents(worker, operator)
            checker = await connect_agent(scene.CHECKER, uri)
            try:
                await scene.record(checker, **_observed(git_op[1]))
            finally:
                await close_agents(checker)
            frozen = hub.operation_records.attempts.stop_state(_stop_seq(store))
        rows = _states(store)
    finally:
        store.close()
    assert code == seal_command.EXIT_REFUSED and rows == []
    by = state["stopped_by"]
    assert (by["state"], by["profile"]) == ("stop requested", "cooperative")
    awaiting = "awaiting an independent observation"
    assert by["blocking"] == [
        {"operation_id": git_op[1], "attempt_id": git_op[3], "reason": awaiting},
        {"operation_id": file_op[1], "attempt_id": file_op[3], "reason": "unsealable"},
    ]
    assert resumed["resumed_from"] == "stop requested"
    assert frozen is not None and frozen.state == "stop requested"


async def test_a_resume_after_no_new_effect_records_it_and_the_state_stands(
    tmp_path: Path,
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (hub, uri):
            [(_task, operation_id, _digest, _attempt)] = await _opened(uri, b, x)
            watcher = await connect_agent("X/watcher", uri)
            try:
                await _command(watcher, command.stop_and_seal(_args(uri, work)))
            finally:
                await close_agents(watcher)
            operator = await connect_agent("H/operator", uri)
            checker = await connect_agent(scene.CHECKER, uri)
            try:
                resumed = await scene.answer(
                    operator,
                    lambda: operator.agent.send_message(
                        MessageType.OPERATION_RESUME,
                        scope=SCOPE,
                        reason="r",
                        case="only_person",
                        idem_key="r1",
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
                later = await scene.record(checker, **_observed(operation_id))
            finally:
                await close_agents(operator, checker)
            stands = hub.operation_records.attempts.stop_state(_stop_seq(store))
        rows = _states(store)
    finally:
        store.close()
    assert (resumed["answer"], resumed["resumed_from"]) == ("resumed", "no new effect")
    assert later["status"] == "accepted"
    assert [row["state"] for row in rows] == ["no new effect"]
    assert stands is not None and stands.state == "no new effect"


async def test_a_stop_that_lists_nothing_is_at_rest_in_its_own_transaction(
    tmp_path: Path,
) -> None:
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            recovery = await connect_agent(RECOVERY, uri)
            try:
                stop = await scene.answer(
                    recovery,
                    lambda: recovery.agent.send_message(
                        MessageType.OPERATION_STOP, scope=SCOPE, reason="r", idem_key="s1"
                    ),
                    lambda frame: frame.get("type") == MessageType.OPERATION_STOP_RESULT,
                )
            finally:
                await close_agents(recovery)
        stopped = store.read_since(0, kinds=(EventKind.OPERATION_STOPPED,))
        states = store.read_since(0, kinds=(EventKind.OPERATION_STOP_STATE,))
    finally:
        store.close()
    assert (stop["answer"], stop["state"], stop["listed"]) == ("stop_recorded", "at rest", [])
    assert [row.seq for row in stopped] == [stop["seq"]]
    assert [row.seq for row in states] == [stop["seq"] + 1]
    report = dict(states[0].payload)
    assert report == {
        "scope": SCOPE,
        "epoch": 1,
        "state": "at rest",
        "profile": "cooperative",
        "profile_says": (
            "governed effects only: a writer that does not ask the hub is not stopped by this stop"
        ),
        "blocking": [],
        "counts": {
            "listed": 0,
            "sealed": 0,
            "applied": 0,
            "unsealable": 0,
            "unread": 0,
            "reconciled": 0,
        },
    }
