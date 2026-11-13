# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — a Git verification reads the attempt's marker beside the branch
"""Each reading of an attempt's branch and marker becomes its own verification record.

The remote is a local bare repository of each object format; no test touches a
hosted service. The last test runs the whole recovery on a real hub: the seal,
the verification that reads it, and the fresh attempt it allows.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import operation_attempt_scenario as scene
from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel import result_verification as verifier
from synapse_channel.cli import main
from synapse_channel.core.acl import OPERATION_SEAL, AclPolicy, AclRule
from synapse_channel.core.attempt_markers import LANDED, AttemptMarker, AttemptMarkerError
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType
from synapse_channel.git.attempt_marker_changes import Target, apply_effect, seal
from synapse_channel.git.git_runtime import git_executable, run_git
from synapse_channel.result_verification import verify_git_publication
from synapse_channel.result_verification_attempt import (
    AttemptCheck,
    identifier_length,
    listed_refs,
)

OPERATION, DIGEST = "op:1", "e" * 64
_IDENTITY = ["-c", "user.name=seal test", "-c", "user.email=seal-test@example.invalid"]


@dataclass(frozen=True)
class World:
    """A work repository with A, B, X after B, Y after X and O beside X; a bare remote at B."""

    work: Path
    remote: Path
    git: str
    fmt: str
    b: str
    x: str
    y: str
    o: str

    def out(self, repository: Path, *args: str) -> str:
        completed = run_git(self.git, repository, [*_IDENTITY, *args])
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    def move(self, commit: str) -> None:
        self.out(self.remote, "update-ref", "refs/heads/main", commit)

    @property
    def target(self) -> Target:
        return Target(self.work, "main", self.b, str(self.remote))


@pytest.fixture(params=["sha1", "sha256"])
def world(request: pytest.FixtureRequest, tmp_path: Path) -> World:
    fmt, git = str(request.param), git_executable()
    work, remote = tmp_path / "work", tmp_path / "remote.git"
    work.mkdir()
    assert run_git(git, work, ["init", "-q", f"--object-format={fmt}"]).returncode == 0
    init = ["init", "-q", "--bare", f"--object-format={fmt}", str(remote)]
    assert run_git(git, tmp_path, init).returncode == 0
    probe = World(work, remote, git, fmt, "", "", "", "")
    tree = probe.out(work, "write-tree")

    def commit(message: str, *parents: str) -> str:
        return probe.out(work, "commit-tree", tree, "-m", message, *[f"-p{p}" for p in parents])

    a = commit("a")
    b = commit("b", a)
    x = commit("x", b)
    y = commit("y", x)
    o = commit("o", b)
    for keep in (y, o):
        probe.out(work, "push", "-q", str(remote), f"{keep}:refs/keep/{keep}")
    made = World(work, remote, git, fmt, b, x, y, o)
    made.move(b)
    return made


def _attempt(number: int) -> str:
    return f"{number:032x}"


def _marker(number: int) -> AttemptMarker:
    return AttemptMarker(OPERATION, _attempt(number), DIGEST)


def _verify(world: World, number: int, commit: str | None = None) -> dict[str, Any]:
    check = AttemptCheck(_attempt(number), DIGEST, world.b)
    record = verify_git_publication(
        world.work, str(world.remote), "main", commit or world.x, operation_id=OPERATION,
        attempt=check,
    )  # fmt: skip
    return record.to_json()


def _row(record: dict[str, Any]) -> tuple[str, str, str]:
    return record["effect"], record["result"], record["observation"]


def _evidence(world: World, number: int, marker: str, head: str) -> dict[str, str]:
    return {
        "remote": str(world.remote),
        "branch": "main",
        "requested_commit": world.x,
        "base": world.b,
        "attempt_id": _attempt(number),
        "marker_ref": _marker(number).ref,
        "object_format": world.fmt,
        "marker_object": marker,
        "remote_head": head,
    }


def test_each_reading_of_an_attempt_becomes_its_row(world: World) -> None:
    apply_effect(world.target, _marker(1), candidate=world.x, git=world.git)
    present = _verify(world, 1)
    world.move(world.y)
    ahead = _verify(world, 1)
    world.move(world.o)
    moved = _verify(world, 1)
    seal(world.target, _marker(2), git=world.git)
    sealed = _verify(world, 2)
    world.move(world.b)
    waiting = _verify(world, 3)
    world.move(world.x)
    outside = _verify(world, 3)
    tree = world.out(world.remote, "rev-parse", "refs/heads/main^{tree}")
    world.out(world.remote, "update-ref", _marker(4).ref, tree)
    foreign = _verify(world, 4)
    assert [_row(r) for r in (present, ahead, moved, sealed, waiting, outside, foreign)] == [
        ("observed", "verified", "attempt_landed_and_present"),
        ("observed", "verified", "attempt_landed_and_present"),
        ("absent", "not_verified", "attempt_landed_then_the_branch_moved"),
        ("absent", "not_verified", "attempt_sealed_never_lands"),
        ("unknown", "not_checked", "attempt_may_still_land"),
        ("observed", "not_checked", "published_outside_the_protocol"),
        ("unknown", "not_checked", "attempt_state_unknown"),
    ]
    applied = _marker(1).objects(world.fmt).applied
    assert present == {
        "schema": "synapse.result-verification.v1",
        "operation_id": OPERATION,
        "verifier": "git_publication",
        "communication": "not_supplied",
        "effect": "observed",
        "result": "verified",
        "observation": "attempt_landed_and_present",
        "evidence": _evidence(world, 1, applied, world.x),
    }
    assert sealed["evidence"] == _evidence(world, 2, _marker(2).objects(world.fmt).sealed, world.o)
    assert waiting["evidence"] == _evidence(world, 3, "", world.b)


def test_a_head_this_repository_lacks_decides_only_where_ancestry_does_not(world: World) -> None:
    apply_effect(world.target, _marker(1), candidate=world.x, git=world.git)
    seal(world.target, _marker(2), git=world.git)
    tree = world.out(world.remote, "rev-parse", f"{world.x}^{{tree}}")
    elsewhere = world.out(world.remote, "commit-tree", tree, "-p", world.x, "-m", "z")
    world.move(elsewhere)
    assert run_git(world.git, world.work, ["cat-file", "-e", elsewhere]).returncode != 0
    assert [_row(_verify(world, number)) for number in (1, 2, 3)] == [
        ("unknown", "not_checked", "attempt_state_unknown"),
        ("absent", "not_verified", "attempt_sealed_never_lands"),
        ("unknown", "not_checked", "attempt_state_unknown"),
    ]


def test_a_branch_deleted_after_its_effect_reads_as_moved(world: World) -> None:
    apply_effect(world.target, _marker(1), candidate=world.x, git=world.git)
    world.out(world.remote, "update-ref", "-d", "refs/heads/main")
    record = _verify(world, 1)
    assert _row(record) == ("absent", "not_verified", "attempt_landed_then_the_branch_moved")
    assert "remote_head" not in record["evidence"]


def test_refs_named_to_match_the_patterns_are_listed_and_ignored(world: World) -> None:
    marker = _marker(1)
    apply_effect(world.target, marker, candidate=world.x, git=world.git)
    decoys = ["refs/heads/x/refs/heads/main", f"refs/heads/z/{marker.ref}"]
    for decoy in decoys:
        world.out(world.remote, "update-ref", decoy, world.o)
    listing = world.out(
        world.work, "ls-remote", "--", str(world.remote), "refs/heads/main", marker.ref
    )
    assert sorted(line.split("\t")[1] for line in listing.splitlines()) == sorted(
        ["refs/heads/main", marker.ref, *decoys]
    )
    assert _row(_verify(world, 1)) == ("observed", "verified", "attempt_landed_and_present")


def test_without_an_attempt_the_record_is_exactly_the_old_one(world: World) -> None:
    world.move(world.x)
    if world.fmt == "sha256":
        with pytest.raises(ValueError) as refused:
            verify_git_publication(
                world.work, str(world.remote), "main", world.x, operation_id=OPERATION
            )
        assert str(refused.value) == "the commit is a full 40-hex identity"
        return
    record = verify_git_publication(
        world.work, str(world.remote), "main", world.x, operation_id="p"
    )
    assert record.to_json() == {
        "schema": "synapse.result-verification.v1",
        "operation_id": "p",
        "verifier": "git_publication",
        "communication": "not_supplied",
        "effect": "observed",
        "result": "verified",
        "observation": "remote_head_is_the_commit",
        "evidence": {
            "remote": str(world.remote),
            "branch": "main",
            "requested_commit": world.x,
            "remote_head": world.x,
        },
    }


def test_identifiers_of_another_format_than_the_repository_are_refused(world: World) -> None:
    other = 64 if world.fmt == "sha1" else 40
    with pytest.raises(ValueError) as refused:
        verify_git_publication(
            world.work, str(world.remote), "main", "a" * other, operation_id=OPERATION,
            attempt=AttemptCheck(_attempt(1), DIGEST, "b" * other),
        )  # fmt: skip
    assert str(refused.value) == f"the commit and the base are not {world.fmt} object ids"


def test_the_listing_is_keyed_by_the_exact_ref_and_a_malformed_line_is_unrecognised() -> None:
    wanted = ("refs/heads/main", "refs/synapse/attempts/" + "1" * 32)
    main_line, marker_line = f"{'a' * 40}\trefs/heads/main", f"{'b' * 40}\t{wanted[1]}"
    other = f"{'c' * 40}\trefs/heads/x/refs/heads/main"
    assert listed_refs("", wanted, 40) == {}
    assert listed_refs(f"{other}\n\n{main_line}\n{marker_line}\n", wanted, 40) == {
        wanted[0]: "a" * 40,
        wanted[1]: "b" * 40,
    }
    for listing in (
        "a" * 40,
        f"{'a' * 64}\trefs/heads/main",
        f"{'A' * 40}\trefs/heads/main",
        f"{'g' * 40}\trefs/heads/x",
        f"{main_line}\n{main_line}",
    ):
        assert listed_refs(listing, wanted, 40) is None, listing


@pytest.mark.parametrize(
    ("commit", "base", "message"),
    [
        ("a" * 39, "b" * 40, "the commit is a full commit identity of 40 or 64 lower-case hex"),
        ("a" * 40, "B" * 40, "the base is a full commit identity of 40 or 64 lower-case hex"),
        ("a" * 40, "b" * 64, "the commit and the base are of different object formats"),
    ],
)
def test_commit_and_base_must_share_one_format(commit: str, base: str, message: str) -> None:
    with pytest.raises(ValueError) as refused:
        identifier_length(commit, base)
    assert str(refused.value) == message
    assert identifier_length("a" * 64, "b" * 64) == 64


def test_every_input_is_refused_before_git_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[Any, ...]] = []
    monkeypatch.setattr(verifier, "_git", lambda *args, **_kwargs: ran.append(args))
    with pytest.raises(AttemptMarkerError) as expected:
        AttemptMarker(OPERATION, "A" * 32, DIGEST)
    cases: list[tuple[dict[str, Any], str]] = [
        ({"attempt": AttemptCheck("A" * 32, DIGEST, "b" * 40)}, str(expected.value)),
        ({"branch": "a..b"}, "the branch or remote is not a plain name"),
        ({"remote": "-x"}, "the branch or remote is not a plain name"),
        ({"timeout": 0.0}, "the git timeout is a positive, finite number of seconds"),
        ({"commit": "a" * 41}, "the commit is a full commit identity of 40 or 64 lower-case hex"),
    ]
    for changes, message in cases:
        arguments: dict[str, Any] = {
            "repo": Path("."), "remote": "origin", "branch": "main", "commit": "a" * 40,
            "operation_id": OPERATION, "timeout": 5.0,
            "attempt": AttemptCheck(_attempt(1), DIGEST, "b" * 40),
        }  # fmt: skip
        arguments.update(changes)
        with pytest.raises(ValueError) as refused:
            verify_git_publication(**arguments)
        assert str(refused.value) == message, changes
    assert ran == []


def _scripted(
    format_answer: subprocess.CompletedProcess[str] | Exception,
    listing: subprocess.CompletedProcess[str] | Exception,
) -> Any:
    def run(_repo: Path, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        answer = format_answer if args[0] == "rev-parse" else listing
        if isinstance(answer, Exception):
            raise answer
        return answer

    return run


def _done(code: int, out: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, "")


@pytest.mark.parametrize(
    ("format_answer", "listing", "observation", "format_kept"),
    [
        (_done(0, "sha1\n"), _done(128, ""), "remote_not_reachable", True),
        (_done(0, "sha1\n"), OSError("no git"), "remote_not_reachable", False),
        (
            _done(0, "sha1\n"),
            subprocess.TimeoutExpired(["git"], 1.0),
            "remote_not_reachable",
            False,
        ),
        (_done(128, ""), _done(0, ""), "object_format_not_read", False),
        (_done(0, "md5\n"), _done(0, ""), "object_format_not_read", False),
        (_done(0, "sha1\n"), _done(0, "nonsense\n"), "remote_answer_not_recognised", True),
    ],
)
def test_a_listing_that_cannot_be_read_is_not_checked(
    monkeypatch: pytest.MonkeyPatch,
    format_answer: subprocess.CompletedProcess[str] | Exception,
    listing: subprocess.CompletedProcess[str] | Exception,
    observation: str,
    format_kept: bool,
) -> None:
    monkeypatch.setattr(verifier, "_git", _scripted(format_answer, listing))
    record = verify_git_publication(
        Path("."), "origin", "main", "a" * 40, operation_id=OPERATION,
        attempt=AttemptCheck(_attempt(1), DIGEST, "b" * 40),
    )  # fmt: skip
    assert (record.effect, record.result, record.observation) == (
        "unknown",
        "not_checked",
        observation,
    )
    assert ("object_format" in record.evidence) == format_kept
    assert "marker_object" not in record.evidence


def test_the_command_reads_the_attempt_and_refuses_a_partial_set(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    seal(world.target, _marker(2), git=world.git)
    base = ["verify-result", "git", "--repo", str(world.work), "--remote", str(world.remote)]
    base += ["--branch", "main", "--commit", world.x, "--operation-id", OPERATION]
    attempt = ["--attempt-id", _attempt(2), "--intent-sha256", DIGEST, "--base", world.b]
    assert main([*base, *attempt]) == 1
    printed = json.loads(capsys.readouterr().out)
    assert _row(printed) == ("absent", "not_verified", "attempt_sealed_never_lands")
    for partial in (attempt[:2], attempt[:4], attempt[2:]):
        assert main([*base, *partial]) == 2
        assert capsys.readouterr().err == (
            "verify-result: give --attempt-id, --intent-sha256 and --base together, "
            "or none of them\n"
        )


def _policy() -> AclPolicy:
    rules = list(scene.policy().rules)
    rules.append(AclRule(OPERATION_SEAL, "claim", "T*", "R", "the recovery role seals"))
    return AclPolicy(rules)


async def _state(handle: Any) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    return await scene.answer(
        handle,
        lambda: handle.agent.send_message(
            MessageType.OPERATION_STATE,
            request_id=request_id,
            task_id=scene.TASK,
            operation_id=scene.OPERATION,
        ),
        lambda frame: (
            frame.get("type") == MessageType.OPERATION_STATE_RESULT
            and frame.get("request_id") == request_id
        ),
    )


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    git = git_executable()
    work = tmp_path / "system-of-record"
    work.mkdir()
    run_git(git, work, ["init", "-q"])
    tree = run_git(git, work, ["write-tree"]).stdout.strip()
    b = run_git(git, work, [*_IDENTITY, "commit-tree", tree, "-m", "b"]).stdout.strip()
    x = run_git(git, work, [*_IDENTITY, "commit-tree", tree, "-p", b, "-m", "x"]).stdout.strip()
    run_git(git, work, ["update-ref", "refs/heads/main", b])
    return work, b, x


async def test_a_sealed_attempt_verified_by_another_identity_lets_a_fresh_attempt_open(
    tmp_path: Path,
) -> None:
    work, b, x = _repository(tmp_path)
    git = git_executable()
    store = EventStore(tmp_path / "hub.db")
    names = (scene.WORKER, scene.REVIEWER, scene.APPROVER, scene.CHECKER, "R/recovery")
    try:
        async with running_hub(SynapseHub(journal=store, acl_policy=_policy())) as (_hub, uri):
            handles = [await connect_agent(name, uri) for name in names]
            worker, reviewer, approver, checker, recovery = handles
            try:
                epoch = await scene.claim(worker)
                intent = scene.statement(
                    pre_state=b, changed_files=[{"path": "README.md", "mode": "100644"}]
                )
                declared = await scene.record(
                    worker,
                    record="declare",
                    task_id=scene.TASK,
                    operation_id=scene.OPERATION,
                    epoch=epoch,
                    effect="git_publication",
                    effect_target={"repository": str(work), "branch": "main", "revision": x},
                    intent=intent,
                )
                digest = str(declared["intent_sha256"])
                await scene.review(reviewer, digest)
                await scene.approve(approver, digest)
                opened = await scene.attempt(worker, epoch, digest, "k1")
                attempt = str(opened["attempt_id"])
                marker = AttemptMarker(scene.OPERATION, attempt, digest)
                sealed = seal(Target(work, "main", b), marker, git=git)
                kept = await scene.record(
                    recovery,
                    record="seal",
                    task_id=scene.TASK,
                    operation_id=scene.OPERATION,
                    seal={
                        "attempt_id": attempt,
                        "marker_ref": marker.ref,
                        "object_format": "sha1",
                        "marker_object": sealed.marker,
                        "observation": sealed.answer,
                    },
                )
                reading = verify_git_publication(
                    work, str(work), "main", x, operation_id=scene.OPERATION,
                    attempt=AttemptCheck(attempt, digest, b),
                )  # fmt: skip
                published = await scene.record(
                    checker,
                    record="verification",
                    task_id=scene.TASK,
                    operation_id=scene.OPERATION,
                    verification=reading.to_json(),
                )
                after = await _state(recovery)
                await scene.approve(approver, digest, reason="after the seal")
                fresh = await scene.attempt(worker, epoch, digest, "k2")
            finally:
                await close_agents(*handles)
        rows = store.read_since(0, kinds=(EventKind.OPERATION_VERIFIED,))
    finally:
        store.close()
    assert (declared["status"], sealed.answer, kept["status"]) == ("accepted", LANDED, "accepted")
    assert (reading.effect, reading.result, reading.observation) == (
        "absent",
        "not_verified",
        "attempt_sealed_never_lands",
    )
    assert published["status"] == "accepted", published
    assert [item["state"] for item in after["attempts"]] == ["reconciled"]
    assert fresh["answer"] == "approved_fresh" and fresh["attempt_id"] != attempt
    assert len(rows) == 1
    kept_evidence = rows[0].payload["evidence"]
    assert kept_evidence["marker_object"] == marker.objects("sha1").sealed
    assert "marker_ref" not in kept_evidence
