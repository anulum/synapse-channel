# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the effect and the seal on real repositories, local and pushed, both formats
"""The first of an effect and a seal wins at the system of record; the other is read back refused.

Every test runs in a throwaway repository of each object format, in the local
form (a ref transaction in the repository) and the remote form (an atomic push
to a local bare repository). No test touches a hosted service.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.attempt_markers import (
    BASE_GONE,
    LANDED,
    LANDED_EARLIER,
    LANDED_PRESENT,
    LANDED_THEN_MOVED,
    MAY_STILL_LAND,
    NOT_REACHED,
    OTHER_PARTY_FIRST,
    PUBLISHED_OUTSIDE_PROTOCOL,
    SEALED_NEVER_LANDS,
    UNKNOWN,
    AttemptMarker,
)
from synapse_channel.git import attempt_marker_changes as changes
from synapse_channel.git.attempt_marker_changes import (
    Target,
    apply_effect,
    object_format,
    observe,
    seal,
    write_marker_objects,
)
from synapse_channel.git.git_runtime import GitError, git_executable, run_git


@dataclass(frozen=True)
class World:
    """A work repository with A, B on main, X and Y after B, O beside it; maybe a remote."""

    work: Path
    record: Path
    target: Target
    git: str
    a: str
    b: str
    x: str
    y: str
    o: str

    def out(self, repository: Path, *args: str) -> str:
        completed = run_git(self.git, repository, list(args))
        assert completed.returncode == 0, completed.stderr
        return completed.stdout.strip()

    def read(self, ref: str) -> str | None:
        completed = run_git(self.git, self.record, ["rev-parse", "--verify", "--quiet", ref])
        return completed.stdout.strip() or None

    def move_main(self, commit: str) -> None:
        self.out(self.record, "update-ref", "refs/heads/main", commit)


def _commit(world_git: str, repository: Path, message: str, *parents: str) -> str:
    tree = run_git(world_git, repository, ["write-tree"]).stdout.strip()
    args = ["commit-tree", tree, "-m", message]
    for parent in parents:
        args += ["-p", parent]
    return run_git(world_git, repository, args).stdout.strip()


@pytest.fixture(params=["sha1-local", "sha1-remote", "sha256-local", "sha256-remote"])
def world(request: pytest.FixtureRequest, tmp_path: Path) -> World:
    fmt, form = str(request.param).split("-")
    git = git_executable()
    work = tmp_path / "work"
    work.mkdir()
    for args in (
        ["init", "-q", f"--object-format={fmt}"],
        ["config", "user.name", "seal test"],
        ["config", "user.email", "seal-test@example.invalid"],
    ):
        assert run_git(git, work, args).returncode == 0
    a = _commit(git, work, "a")
    b = _commit(git, work, "b", a)
    x = _commit(git, work, "x", b)
    y = _commit(git, work, "y", x)
    o = _commit(git, work, "o", b)
    record, remote = work, None
    if form == "remote":
        record = tmp_path / "remote.git"
        assert (
            run_git(
                git, tmp_path, ["init", "-q", "--bare", f"--object-format={fmt}", str(record)]
            ).returncode
            == 0
        )
        for commit in (b, y, o):
            assert (
                run_git(
                    git, work, ["push", "-q", str(record), f"{commit}:refs/keep/{commit}"]
                ).returncode
                == 0
            )
        remote = str(record)
    run_git(git, record, ["update-ref", "refs/heads/main", b])
    return World(work, record, Target(work, "main", b, remote), git, a, b, x, y, o)


def _marker(number: int = 1) -> AttemptMarker:
    return AttemptMarker(f"op:{number}", f"{number:032x}", "e" * 64)


def test_the_effect_moves_the_branch_and_creates_the_applied_marker(world: World) -> None:
    marker = _marker()
    outcome = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    objects = marker.objects(object_format(world.target, git=world.git))
    assert (outcome.answer, outcome.succeeded, outcome.detail) == (LANDED, True, "")
    assert (outcome.marker, outcome.branch) == (objects.applied, world.x)
    assert (world.read(marker.ref), world.read("refs/heads/main")) == (objects.applied, world.x)


def test_a_seal_first_refuses_the_effect_and_the_branch_stays_at_the_base(world: World) -> None:
    marker = _marker()
    sealed = seal(world.target, marker, git=world.git)
    effect = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    objects = marker.objects(object_format(world.target, git=world.git))
    assert (sealed.answer, effect.answer, effect.succeeded) == (LANDED, OTHER_PARTY_FIRST, False)
    assert effect.detail != ""
    assert (world.read(marker.ref), world.read("refs/heads/main")) == (objects.sealed, world.b)


def test_a_seal_after_the_effect_is_refused_and_the_marker_stays_applied(world: World) -> None:
    marker = _marker()
    apply_effect(world.target, marker, candidate=world.x, git=world.git)
    late = seal(world.target, marker, git=world.git)
    objects = marker.objects(object_format(world.target, git=world.git))
    assert (late.answer, late.marker, late.branch) == (OTHER_PARTY_FIRST, objects.applied, world.x)


@pytest.mark.parametrize("moved_to", ["o", "a"])
def test_an_effect_on_a_moved_base_creates_nothing(world: World, moved_to: str) -> None:
    """A sibling, or an ancestor from which a plain fast-forward to the candidate would pass."""
    marker = _marker()
    commit = world.o if moved_to == "o" else world.a
    world.move_main(commit)
    outcome = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    assert (outcome.answer, outcome.marker, outcome.branch) == (BASE_GONE, None, commit)
    assert world.read(marker.ref) is None


def test_a_repeat_reads_as_landed_and_changes_nothing(world: World) -> None:
    marker = _marker()
    apply_effect(world.target, marker, candidate=world.x, git=world.git)
    seal_marker = _marker(2)
    seal(world.target, seal_marker, git=world.git)
    again = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    sealed_again = seal(world.target, seal_marker, git=world.git)
    # A push of what the remote already holds is "up to date"; a local transaction
    # that would create an existing ref is refused. Either way it reads as landed.
    expected = LANDED if world.target.remote is not None else LANDED_EARLIER
    assert (again.answer, sealed_again.answer) == (expected, expected)
    assert world.read("refs/heads/main") == world.x


def test_a_change_that_never_reached_the_repository_may_be_sent_again(world: World) -> None:
    marker = _marker()
    absent = "1" * len(world.b)
    outcome = apply_effect(world.target, marker, candidate=absent, git=world.git)
    assert (outcome.answer, outcome.marker, outcome.branch) == (NOT_REACHED, None, world.b)
    landed = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    assert landed.answer == LANDED


def test_a_marker_holding_a_foreign_object_reads_unknown(world: World) -> None:
    marker = _marker()
    tree = world.out(world.record, "rev-parse", "refs/heads/main^{tree}")
    world.out(world.record, "update-ref", marker.ref, tree)
    outcome = apply_effect(world.target, marker, candidate=world.x, git=world.git)
    assert (outcome.answer, outcome.marker) == (UNKNOWN, tree)
    assert world.read("refs/heads/main") == world.b


def _race(world: World, marker: AttemptMarker) -> dict[str, Any]:
    """Start an effect and a seal of one attempt at the same moment; return both outcomes."""
    results: dict[str, Any] = {}
    start = threading.Barrier(2)

    def effect() -> None:
        start.wait()
        results["effect"] = apply_effect(world.target, marker, candidate=world.x, git=world.git)

    def sealing() -> None:
        start.wait()
        results["seal"] = seal(world.target, marker, git=world.git)

    threads = [threading.Thread(target=effect), threading.Thread(target=sealing)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return results


def test_an_effect_and_a_seal_at_once_end_in_one_consistent_state(world: World) -> None:
    for number in range(1, 6):
        world.move_main(world.b)
        marker = _marker(number)
        results = _race(world, marker)
        answers = {results["effect"].answer, results["seal"].answer}
        assert answers == {LANDED, OTHER_PARTY_FIRST}, (number, answers)
        objects = marker.objects(object_format(world.target, git=world.git))
        head, kept = world.read("refs/heads/main"), world.read(marker.ref)
        assert (head, kept) in {(world.x, objects.applied), (world.b, objects.sealed)}


def test_the_stored_blobs_are_the_computed_bytes(world: World) -> None:
    marker = _marker()
    objects = write_marker_objects(world.target, marker, git=world.git)
    assert objects == marker.objects(object_format(world.target, git=world.git))
    assert (
        world.out(world.work, "cat-file", "-p", objects.applied) + "\n"
        == marker.blob("applied").decode()
    )
    assert world.out(world.work, "cat-file", "-t", objects.sealed) == "blob"


def test_malformed_branch_or_commit_names_are_refused_before_any_change(world: World) -> None:
    marker = _marker()
    for branch, message in (
        ("two words", "branch 'two words' is not a plain branch name"),
        ("a..b", "git check-ref-format failed: fatal: 'a..b' is not a valid branch name"),
    ):
        with pytest.raises(GitError) as refused:
            apply_effect(
                Target(world.work, branch, world.b, world.target.remote),
                marker,
                candidate=world.x,
                git=world.git,
            )
        assert str(refused.value) == message
    fmt = object_format(world.target, git=world.git)
    with pytest.raises(GitError) as short:
        apply_effect(world.target, marker, candidate=world.x[:12], git=world.git)
    assert str(short.value) == f"candidate {world.x[:12]!r} is not a full {fmt} commit name"
    with pytest.raises(GitError) as base:
        seal(Target(world.work, "main", "HEAD", world.target.remote), marker, git=world.git)
    assert str(base.value) == f"base 'HEAD' is not a full {fmt} commit name"
    assert world.read(marker.ref) is None


def test_an_observer_reads_every_state_of_an_attempt_in_the_repository(tmp_path: Path) -> None:
    git = git_executable()
    work = tmp_path / "work"
    work.mkdir()
    run_git(git, work, ["init", "-q"])
    b = _commit(git, work, "b")
    x = _commit(git, work, "x", b)
    y = _commit(git, work, "y", x)
    o = _commit(git, work, "o", b)
    run_git(git, work, ["update-ref", "refs/heads/main", b])
    target = Target(work, "main", b)

    def seen(marker: AttemptMarker, candidate: str = x) -> str:
        return observe(target, marker, candidate=candidate, git=git)

    fresh, landed, sealed, foreign = _marker(1), _marker(2), _marker(3), _marker(4)
    assert seen(fresh) == MAY_STILL_LAND
    seal(target, sealed, git=git)
    assert seen(sealed) == SEALED_NEVER_LANDS
    apply_effect(target, landed, candidate=x, git=git)
    assert seen(landed) == LANDED_PRESENT
    run_git(git, work, ["update-ref", "refs/heads/main", y])
    assert seen(landed) == LANDED_PRESENT
    run_git(git, work, ["update-ref", "refs/heads/main", o])
    assert seen(landed) == LANDED_THEN_MOVED
    assert seen(landed, candidate="1" * 40) == UNKNOWN
    run_git(git, work, ["update-ref", "refs/heads/main", x])
    assert seen(fresh) == PUBLISHED_OUTSIDE_PROTOCOL
    run_git(git, work, ["update-ref", foreign.ref, b])
    assert seen(foreign) == UNKNOWN
    with pytest.raises(GitError) as remote:
        observe(Target(work, "main", b, str(work)), fresh, candidate=x, git=git)
    assert str(remote.value) == "an observation reads the repository itself, not a remote"


def test_a_format_git_does_not_name_or_a_blob_stored_elsewhere_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = git_executable()
    run_git(git, tmp_path, ["init", "-q"])
    target = Target(tmp_path, "main", "b" * 40)
    real_run = run_git

    def answering(answer: str, command: str) -> Any:
        def fake(git: str, repository: Path, args: list[str], **kwargs: Any) -> Any:
            completed = real_run(git, repository, args, **kwargs)
            if args[0] == command or args[:2] == ["rev-parse", command]:
                completed.stdout = answer + "\n"
            return completed

        return fake

    monkeypatch.setattr(changes, "run_git", answering("md5", "--show-object-format"))
    with pytest.raises(GitError) as fmt:
        object_format(target, git=git)
    assert str(fmt.value) == "the repository's object format 'md5' is not sha1 or sha256"
    monkeypatch.setattr(changes, "run_git", answering("0" * 40, "hash-object"))
    with pytest.raises(GitError) as stored:
        write_marker_objects(target, _marker(), git=git)
    expected = _marker().objects("sha1").applied
    assert str(stored.value) == f"git stored the applied marker as {'0' * 40}, expected {expected}"
