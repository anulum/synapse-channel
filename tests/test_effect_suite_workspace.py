# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the suite's system of record, its hub, and what its observer reads
"""The prepared history, the hub's lifetime, and the observer's own readings of Git and journal."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from synapse_channel.core.approvals import APPROVAL_NOTE_KIND
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.effect_suite.observer import (
    OBSERVER,
    GitReading,
    GitState,
    JournalReading,
    Observer,
    journal_reading,
)
from synapse_channel.effect_suite.session import HubSession
from synapse_channel.effect_suite.workspace import (
    REF,
    Repository,
    git_out,
    prepare_repository,
    suite_hub,
)
from synapse_channel.git.git_runtime import GitError, git_executable, run_git


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    return prepare_repository(tmp_path, git=git_executable())


def _git(repository: Repository, *args: str) -> str:
    return git_out(repository.path, list(args), git=repository.git)


def _child(repository: Repository, parent: str, files: list[tuple[str, str, str]]) -> str:
    """Return a commit of ``parent``'s tree plus ``files`` as (path, mode, text)."""
    _git(repository, "read-tree", parent)
    for name, mode, text in files:
        (repository.path / name).write_text(text, encoding="utf-8")
        blob = _git(repository, "hash-object", "-w", name)
        (repository.path / name).unlink()
        _git(repository, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{name}")
    tree = _git(repository, "write-tree")
    return _git(repository, "commit-tree", tree, "-p", parent, "-m", "test")


def test_the_history_is_a_then_b_on_main_with_three_unreferenced_children_of_b(
    repository: Repository,
) -> None:
    commits = repository.commits
    assert _git(repository, "rev-parse", REF) == commits.b
    assert _git(repository, "rev-list", "--parents", "-n", "1", commits.a) == commits.a
    assert _git(repository, "rev-parse", f"{commits.b}^") == commits.a
    for child in (commits.x, commits.x_other, commits.o):
        assert _git(repository, "rev-parse", f"{child}^") == commits.b
    assert _git(repository, "ls-tree", "--name-only", commits.x).split() == [
        "base.txt",
        "report.txt",
        "seed.txt",
    ]
    assert _git(repository, "show", f"{commits.x}:report.txt") == "published by the suite"
    assert _git(repository, "show", f"{commits.x_other}:report.txt") == "another candidate"
    assert _git(repository, "for-each-ref", "--format=%(refname)") == REF
    assert _git(repository, "config", "core.logAllRefUpdates") == "always"
    assert sorted(path.name for path in repository.path.iterdir()) == [".git"]


def test_a_failed_git_command_is_refused_with_its_name_and_what_git_said(
    repository: Repository,
) -> None:
    with pytest.raises(GitError) as quiet:
        _git(repository, "rev-parse", "--verify", "--quiet", "refs/heads/absent")
    assert str(quiet.value) == "git rev-parse failed: exit code 1"
    stale = ["update-ref", REF, repository.commits.x, repository.commits.a]
    said = run_git(repository.git, repository.path, stale).stderr.strip()
    assert said
    with pytest.raises(GitError) as loud:
        _git(repository, *stale)
    assert str(loud.value) == f"git update-ref failed: {said[:200]}"


async def test_the_hub_stops_serving_and_its_journal_can_be_read_after_it_ends(
    tmp_path: Path,
) -> None:
    async with suite_hub(tmp_path) as hub:
        port = int(hub.uri.rsplit(":", 1)[1])
        async with HubSession(OBSERVER, hub.uri) as session:
            granted = await session.after(
                lambda: session.agent.claim("suite-lifetime", note="test"),
                lambda frame: frame.get("type") == "claim_granted",
                what="a claim grant",
            )
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
    store = EventStore(hub.journal)
    try:
        claims = [
            row.payload.get("task_id") for row in store.read_since(0, kinds=(EventKind.CLAIM,))
        ]
    finally:
        store.close()
    assert (granted["task_id"], claims) == ("suite-lifetime", ["suite-lifetime"])


def _observer(repository: Repository) -> Observer:
    return Observer(repository, HubSession(OBSERVER, "ws://127.0.0.1:1"), "suite-test")


def test_only_the_candidate_with_parent_b_and_exactly_its_one_new_file_is_published(
    repository: Repository,
) -> None:
    commits, observer = repository.commits, _observer(repository)
    middle = _child(repository, commits.b, [])
    refused = {
        "another path": commits.o,
        "an executable mode": _child(repository, commits.b, [("report.txt", "100755", "r\n")]),
        # The second file sorts after report.txt, so only the count can refuse it.
        "a second file": _child(
            repository,
            commits.b,
            [("report.txt", "100644", "r\n"), ("zz-extra.txt", "100644", "e\n")],
        ),
        "a parent other than b": _child(repository, middle, [("report.txt", "100644", "r\n")]),
    }
    assert not observer.published(commits.x)
    for name, candidate in refused.items():
        _git(repository, "update-ref", REF, candidate)
        assert not observer.published(candidate), name
    _git(repository, "update-ref", REF, commits.x)
    assert observer.published(commits.x)


def test_effects_are_counted_from_the_reflog_so_a_reverted_write_still_counts(
    repository: Repository,
) -> None:
    commits, observer = repository.commits, _observer(repository)
    assert observer.git_reading(commits.x) == GitReading("b", 0, False)
    _git(repository, "update-ref", REF, commits.x, commits.b)
    _git(repository, "update-ref", REF, commits.b, commits.x)
    assert observer.git_reading(commits.x) == GitReading("b", 1, False)
    _git(repository, "update-ref", REF, commits.x_other, commits.b)
    assert observer.git_reading(commits.x) == GitReading("x_other", 2, False)
    assert [observer.role(commit) for commit in (commits.a, commits.o, None, "f" * 40)] == [
        "a",
        "o",
        "other",
        "other",
    ]


def test_the_state_is_the_commit_and_tree_of_the_branch_and_empty_without_one(
    repository: Repository,
) -> None:
    commits, observer = repository.commits, _observer(repository)
    tree_b = _git(repository, "rev-parse", f"{commits.b}^{{tree}}")
    tree_x = _git(repository, "rev-parse", f"{commits.x}^{{tree}}")
    assert tree_b != tree_x
    assert observer.state() == GitState(commits.b, tree_b)
    _git(repository, "update-ref", REF, commits.x, commits.b)
    assert observer.state() == GitState(commits.x, tree_x)
    _git(repository, "update-ref", "-d", REF)
    assert observer.state() == GitState("", "")


def test_the_journal_reading_counts_this_tasks_opened_attempts_and_refused_approvals(
    tmp_path: Path,
) -> None:
    journal = tmp_path / "hub.db"
    store = EventStore(journal)
    try:
        answered = EventKind.OPERATION_ATTEMPT_ANSWERED
        refused = EventKind.VERDICT_RECORD_REFUSED
        for kind, payload in (
            (answered, {"task_id": "t", "answer": "approved_fresh"}),
            (answered, {"task_id": "t", "answer": "attempt_exists"}),
            (answered, {"task_id": "other", "answer": "approved_fresh"}),
            (refused, {"task_id": "t", "kind": APPROVAL_NOTE_KIND}),
            (refused, {"task_id": "t", "kind": "verdict"}),
            (refused, {"task_id": "other", "kind": APPROVAL_NOTE_KIND}),
        ):
            store.append(kind, payload)
    finally:
        store.close()
    assert journal_reading(journal, "t") == JournalReading(
        attempts=1, answers=("approved_fresh", "attempt_exists"), gate_refusals=1, last_seq=6
    )
