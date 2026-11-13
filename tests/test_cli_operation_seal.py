# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse operation state`, `seal` and `unmark` against a real hub and repository
"""The seal command seals only an open attempt, in Git first; unmark waits for closure."""

from __future__ import annotations

import argparse
import asyncio
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import AgentHandle, close_agents, connect_agent, running_hub
from synapse_channel import cli_operation_seal as command
from synapse_channel.cli import build_parser
from synapse_channel.core.acl import OPERATION_SEAL, AclPolicy, AclRule
from synapse_channel.core.attempt_markers import (
    NOT_REACHED,
    UNKNOWN,
    AttemptMarker,
)
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.attempt_marker_changes import ChangeOutcome, Target, apply_effect
from synapse_channel.git.git_runtime import GitError, git_executable, run_git

RECOVERY = "R/recovery"


def _policy() -> AclPolicy:
    rules = list(scene.policy().rules)
    rules.append(AclRule(OPERATION_SEAL, "claim", "T*", "R", "the recovery role seals"))
    return AclPolicy(rules)


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    """Return a repository whose main holds B, and B and a child X of it."""
    git = git_executable()
    work = tmp_path / "system-of-record"
    work.mkdir(parents=True)
    run_git(git, work, ["init", "-q"])
    tree = run_git(git, work, ["write-tree"]).stdout.strip()
    b = run_git(git, work, ["commit-tree", tree, "-m", "b"]).stdout.strip()
    x = run_git(git, work, ["commit-tree", tree, "-p", b, "-m", "x"]).stdout.strip()
    run_git(git, work, ["update-ref", "refs/heads/main", b])
    return work, b, x


def _args(action: str, uri: str, *extra: str, name: str = RECOVERY) -> argparse.Namespace:
    argv = ["operation", action, "--task", scene.TASK, "--name", name, "--uri", uri]
    argv += ["--operation-id", scene.OPERATION, "--ready-timeout", "15", "--timeout", "15"]
    return build_parser(command="operation").parse_args([*argv, *extra])


def _marker_args(attempt: str, digest: str, work: Path, base: str, action: str) -> list[str]:
    args = ["--attempt-id", attempt, "--intent-sha256", digest, "--repository", str(work)]
    if action == "seal":
        args += ["--branch", "main", "--base", base]
    return args


def _left(frame: dict[str, Any]) -> bool:
    return (
        frame.get("type") == MessageType.PRESENCE_UPDATE
        and frame.get("event") == "left"
        and frame.get("agent") == RECOVERY
    )


async def _command(watcher: AgentHandle, run: Any) -> int:
    """Run one command as the recovery identity and wait until the hub has released the name."""
    earlier = {id(frame) for frame in watcher.recorder.messages}
    code: int = await run
    await watcher.recorder.wait_for(
        lambda frame: id(frame) not in earlier and _left(frame), timeout=scene.WAIT
    )
    return code


async def _opened(uri: str) -> tuple[int, str, str]:
    """Claim, declare, review, approve and open an attempt as the scenario's seats."""
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER)
    worker, reviewer, approver = [await connect_agent(name, uri) for name in names]
    try:
        epoch = await scene.claim(worker)
        digest = await scene.declare(worker, epoch, intent=scene.statement())
        await scene.review(reviewer, digest)
        await scene.approve(approver, digest)
        opened = await scene.attempt(worker, epoch, digest, "k1")
    finally:
        await close_agents(worker, reviewer, approver)
    return epoch, digest, str(opened["attempt_id"])


async def test_state_then_seal_then_a_second_seal_refused_on_a_live_hub(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work, b, _x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            _epoch, digest, attempt = await _opened(uri)
            watcher = await connect_agent("X/watcher", uri)
            try:
                marker_args = _marker_args(attempt, digest, work, b, "seal")
                listed = await _command(watcher, command.show_state(_args("state", uri)))
                listed_out = capsys.readouterr().out.splitlines()
                sealed = await _command(
                    watcher, command.seal_attempt(_args("seal", uri, *marker_args))
                )
                sealed_out = capsys.readouterr().out.splitlines()
                again = await _command(
                    watcher, command.seal_attempt(_args("seal", uri, *marker_args))
                )
                again_out = capsys.readouterr().out.splitlines()
                as_json = await _command(watcher, command.show_state(_args("state", uri, "--json")))
                json_out = capsys.readouterr().out
                unmark_args = _marker_args(attempt, digest, work, b, "unmark")
                early = await _command(
                    watcher, command.unmark_attempt(_args("unmark", uri, *unmark_args))
                )
                early_out = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
    finally:
        store.close()
    marker = AttemptMarker(scene.OPERATION, attempt, digest)
    held = run_git(git_executable(), work, ["rev-parse", marker.ref]).stdout.strip()
    assert listed == command.EXIT_DONE
    assert listed_out == [
        f"operation {scene.OPERATION}: 1 attempt(s)",
        f"  {attempt} open by {scene.WORKER}",
    ]
    assert sealed == command.EXIT_DONE
    assert sealed_out[0].startswith(f"attempt {attempt} sealed at {marker.ref}; recorded at seq ")
    assert held == marker.objects("sha1").sealed
    assert (again, again_out) == (
        command.EXIT_REFUSED,
        [f"attempt {attempt} is sealed; nothing sealed"],
    )
    assert as_json == command.EXIT_DONE
    assert '"state": "sealed"' in json_out and '"closed": false' in json_out
    assert (early, early_out) == (
        command.EXIT_REFUSED,
        [f"operation {scene.OPERATION} is not closed; the marker stays"],
    )


async def test_an_effect_that_landed_first_is_not_sealed_and_unmark_follows_custody(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    work, b, x = _repository(tmp_path)
    store = EventStore(tmp_path / "hub.db")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_h, uri):
            _epoch, digest, attempt = await _opened(uri)
            marker = AttemptMarker(scene.OPERATION, attempt, digest)
            apply_effect(Target(work, "main", b), marker, candidate=x, git=git_executable())
            watcher = await connect_agent("X/watcher", uri)
            try:
                seal_args = _marker_args(attempt, digest, work, b, "seal")
                first = await _command(
                    watcher, command.seal_attempt(_args("seal", uri, *seal_args))
                )
                first_out = capsys.readouterr().out.splitlines()
                checker = await connect_agent(scene.CHECKER, uri)
                try:
                    verified = await scene.record(
                        checker, **scene.verification("observed", "verified")
                    )
                finally:
                    await close_agents(checker)
                unmark_args = _marker_args(attempt, digest, work, b, "unmark")
                removed = await _command(
                    watcher, command.unmark_attempt(_args("unmark", uri, *unmark_args))
                )
                removed_out = capsys.readouterr().out.splitlines()
                gone = await _command(
                    watcher, command.unmark_attempt(_args("unmark", uri, *unmark_args))
                )
                gone_out = capsys.readouterr().out.splitlines()
            finally:
                await close_agents(watcher)
    finally:
        store.close()
    assert (first, first_out) == (
        command.EXIT_REFUSED,
        [f"the effect of attempt {attempt} landed first; nothing sealed"],
    )
    assert verified["status"] == "accepted"
    assert (removed, removed_out) == (command.EXIT_DONE, [f"applied marker {marker.ref} removed"])
    assert (gone, gone_out) == (
        command.EXIT_REFUSED,
        [f"marker {marker.ref} not removed: the marker is absent"],
    )


class _Fake:
    """A client whose hub answers each request type with a scripted frame, or not at all."""

    def __init__(
        self, callback: Any, replies: Mapping[str, dict[str, Any] | None], ready: bool
    ) -> None:
        self.callback = callback
        self.replies = replies
        self.ready = ready
        self.running = True
        self.last_close_code: int | None = None
        self.last_close_reason = ""
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> None:
        await asyncio.Event().wait()

    async def wait_until_ready(self, timeout: float) -> bool:
        del timeout
        return self.ready

    async def request_who(self, hub: str | None = None) -> None:
        del hub
        await self.callback({"type": MessageType.WHO_SNAPSHOT})

    async def send_message(self, msg_type: str, **fields: Any) -> None:
        self.sent.append({"type": msg_type, **fields})
        reply = self.replies.get(msg_type)
        await self.callback({"type": MessageType.PRESENCE_UPDATE, "request_id": "other"})
        if reply is not None:
            await self.callback({**reply, "request_id": fields["request_id"]})


def _state_reply(state: str = "open", **changes: Any) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "type": MessageType.OPERATION_STATE_RESULT,
        "status": "accepted",
        "closed": False,
        "attempts": [{"attempt_id": "a" * 32, "executor": scene.WORKER, "state": state}],
    }
    reply.update(changes)
    return reply


async def _fake_run(
    action: str,
    replies: Mapping[str, dict[str, Any] | None],
    tmp_path: Path,
    *,
    ready: bool = True,
    fakes: list[_Fake] | None = None,
) -> int:
    def factory(_name: str, callback: Any, **_kwargs: Any) -> Any:
        fake = _Fake(callback, replies, ready)
        if fakes is not None:
            fakes.append(fake)
        return fake

    work, b, _x = _repository(tmp_path)
    marker_args = _marker_args("a" * 32, "d" * 64, work, b, action)
    args = _args(action, "ws://hub", *([] if action == "state" else marker_args))
    args.timeout = args.ready_timeout = 0.2
    runner = {"state": command.show_state, "seal": command.seal_attempt}.get(
        action, command.unmark_attempt
    )
    return await runner(args, factory)


async def test_old_hubs_silence_refusals_and_a_closed_door_have_their_own_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unknown = {"type": MessageType.ERROR, "payload": "Unknown message type 'operation_state'."}
    state = MessageType.OPERATION_STATE
    assert await _fake_run("state", {state: unknown}, tmp_path / "1") == command.EXIT_UNSUPPORTED
    assert capsys.readouterr().out == (
        "operation state refused (unsupported_by_hub): this hub does not know the seal\n"
    )
    limited = {"type": MessageType.ERROR, "error_code": "rate_limited", "payload": "slow down"}
    assert await _fake_run("state", {state: limited}, tmp_path / "2") == command.EXIT_REFUSED
    assert capsys.readouterr().out == "operation state refused (rate_limited): slow down\n"
    refused = _state_reply(status="refused", reason="not_authorised", payload="no grant")
    assert await _fake_run("state", {state: refused}, tmp_path / "3") == command.EXIT_REFUSED
    assert capsys.readouterr().out == "operation state refused (not_authorised): no grant\n"
    assert await _fake_run("seal", {state: None}, tmp_path / "4") == command.EXIT_NO_VERDICT
    assert capsys.readouterr().out == "operation state failed: the hub returned no answer\n"
    fakes: list[_Fake] = []
    closed = await _fake_run("state", {}, tmp_path / "5", ready=False, fakes=fakes)
    assert closed == command.EXIT_NO_VERDICT and fakes[0].sent == []
    assert capsys.readouterr().out.strip() != ""


async def test_the_seal_record_refused_after_the_seal_says_where_the_seal_is(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = {
        "type": MessageType.OPERATION_RECORD_RESULT,
        "status": "refused",
        "reason": "seal_not_observed",
        "payload": "the seal record cannot be kept: seal_not_observed",
    }
    replies = {MessageType.OPERATION_STATE: _state_reply(), MessageType.OPERATION_RECORD: record}
    fakes: list[_Fake] = []
    assert await _fake_run("seal", replies, tmp_path, fakes=fakes) == command.EXIT_REFUSED
    marker = AttemptMarker(scene.OPERATION, "a" * 32, "d" * 64)
    assert capsys.readouterr().out.splitlines() == [
        "seal record refused (seal_not_observed): "
        "the seal record cannot be kept: seal_not_observed",
        f"the seal is in place at {marker.ref}; the hub has no record of it yet",
    ]
    sent = fakes[0].sent[-1]
    assert (sent["record"], sent["seal"]["observation"], sent["seal"]["object_format"]) == (
        "seal",
        "landed",
        "sha1",
    )
    assert sent["seal"]["marker_object"] == marker.objects("sha1").sealed


async def test_git_answers_other_than_a_landed_seal_send_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replies = {MessageType.OPERATION_STATE: _state_reply()}

    def answered(answer: str, marker: str | None) -> Any:
        return lambda *_a, **_k: ChangeOutcome(answer, False, marker, None, "remote hung up")

    def failing(*_a: Any, **_k: Any) -> Any:
        raise GitError("git push did not finish within 30 seconds")

    for name, behaviour, code, line in (
        (
            "not-reached",
            answered(NOT_REACHED, None),
            command.EXIT_NO_VERDICT,
            "the seal did not reach the repository; send it again: remote hung up",
        ),
        (
            "unknown",
            answered(UNKNOWN, "f" * 40),
            command.EXIT_UNKNOWN,
            f"seal state unknown: the marker holds {'f' * 40}",
        ),
        (
            "git-error",
            failing,
            command.EXIT_UNKNOWN,
            "seal state unknown: git push did not finish within 30 seconds",
        ),
    ):
        monkeypatch.setattr(command, "seal", behaviour)
        fakes: list[_Fake] = []
        assert await _fake_run("seal", replies, tmp_path / name, fakes=fakes) == code, name
        assert capsys.readouterr().out.splitlines() == [line], name
        assert [frame["type"] for frame in fakes[0].sent] == [MessageType.OPERATION_STATE]


async def test_unmark_reports_a_marker_it_may_not_remove_and_a_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    closed = {MessageType.OPERATION_STATE: _state_reply("closed", closed=True)}
    marker = AttemptMarker(scene.OPERATION, "a" * 32, "d" * 64)
    assert await _fake_run("unmark", closed, tmp_path / "1") == command.EXIT_REFUSED
    assert capsys.readouterr().out == f"marker {marker.ref} not removed: the marker is absent\n"

    def failing(*_a: Any, **_k: Any) -> Any:
        raise GitError("git ls-remote failed: exit code 128")

    monkeypatch.setattr(command, "remove_applied_marker", failing)
    assert await _fake_run("unmark", closed, tmp_path / "2") == command.EXIT_UNKNOWN
    assert capsys.readouterr().out == "marker state unknown: git ls-remote failed: exit code 128\n"
    no_answer = {MessageType.OPERATION_STATE: None}
    assert await _fake_run("unmark", no_answer, tmp_path / "3") == command.EXIT_NO_VERDICT
    capsys.readouterr()
    silent = _state_reply("closed")
    del silent["closed"]
    unsaid = {MessageType.OPERATION_STATE: silent}
    assert await _fake_run("unmark", unsaid, tmp_path / "4") == command.EXIT_REFUSED
    assert capsys.readouterr().out == (
        f"operation {scene.OPERATION} is not closed; the marker stays\n"
    )


def test_inputs_are_refused_before_the_hub_and_the_commands_dispatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = _args(
        "seal",
        "ws://127.0.0.1:1",
        "--attempt-id",
        "x",
        "--intent-sha256",
        "d" * 64,
        "--repository",
        ".",
        "--branch",
        "main",
        "--base",
        "b" * 40,
    )
    assert command.cmd_seal(bad) == command.EXIT_UNUSABLE
    assert capsys.readouterr().out == (
        "refused before contacting the hub: attempt id 'x' does not match [0-9a-f]{32}\n"
    )
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    good = _args(
        "unmark",
        "ws://127.0.0.1:1",
        "--attempt-id",
        "a" * 32,
        "--intent-sha256",
        "d" * 64,
        "--repository",
        ".",
    )
    assert command.cmd_unmark(good) == command.EXIT_UNUSABLE
    assert capsys.readouterr().out == (
        "refused before contacting the hub: "
        "this needs a git executable on PATH, and none was found\n"
    )
    monkeypatch.undo()
    for dispatch, args in (
        (command.cmd_state, _args("state", "ws://127.0.0.1:1")),
        (command.cmd_unmark, good),
        (
            command.cmd_seal,
            _args(
                "seal",
                "ws://127.0.0.1:1",
                "--attempt-id",
                "a" * 32,
                "--intent-sha256",
                "d" * 64,
                "--repository",
                ".",
                "--branch",
                "main",
                "--base",
                "b" * 40,
            ),
        ),
    ):
        args.ready_timeout = 0.2
        assert dispatch(args) == command.EXIT_NO_VERDICT
        assert capsys.readouterr().out.strip() != ""


async def test_an_attempt_the_hub_does_not_list_is_not_sealed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = _state_reply(attempts=["not an attempt", {"attempt_id": "b" * 32, "state": "open"}])
    fakes: list[_Fake] = []
    replies = {MessageType.OPERATION_STATE: other}
    assert await _fake_run("seal", replies, tmp_path, fakes=fakes) == command.EXIT_REFUSED
    assert capsys.readouterr().out == f"attempt {'a' * 32} is none; nothing sealed\n"
    assert [frame["type"] for frame in fakes[0].sent] == [MessageType.OPERATION_STATE]
