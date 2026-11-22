# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the structure budget judges every pushed commit against its own parent
"""Run the real checker's ``--walk-from`` mode on real git repositories with planted pushes.

A fast-forward push can carry several commits. The old CI step compared only the
pushed tip with its parent, so a ceiling raised by an earlier commit and kept by
the tip went unseen. These tests start ``python -m tools.check_structure_budget``
as a process, the way CI starts it, and plant each case of the walk: a raise in a
non-tip commit, the four per-commit cases, and every refused anchor.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tools.structure_budget_walk import DEFAULT_MAX_COMMITS, walk_range

REPO = Path(__file__).resolve().parents[1]
ZERO = "0" * 40

LEDGER_HEAD = """schema = 1
roots = ["pkg"]

[thresholds]
constructor_parameters = 12
init_attributes = 20
init_constructions = 5
function_body_lines = 60
function_branches = 15
module_internal_imports = 25
private_access = 0

[allowed_private_access]
names = ["argparse._SubParsersAction"]
"""


def _function(name: str, lines: int) -> str:
    body = "\n".join(f"    v{i} = {i}" for i in range(lines - 1))
    return f"def {name}():\n{body}\n    return 0\n"


class Repo:
    """A temporary repository whose history is built commit by commit."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pkg").mkdir(parents=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *arguments: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
            + list(arguments),
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def commit(self, message: str = "state") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def budget(self, *arguments: str, env: dict[str, str] | None = None) -> tuple[int, str]:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.check_structure_budget",
                *arguments,
                "--root",
                str(self.root),
                "--ledger",
                "budget.toml",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return done.returncode, done.stdout + done.stderr

    def walk(
        self, before: str, to: str = "HEAD", *extra: str, env: dict[str, str] | None = None
    ) -> tuple[int, str]:
        return self.budget("--check", "--walk-from", before, "--to", to, *extra, env=env)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Return a repository with a pre-budget commit, then the ledger adopted for a 70-line unit."""
    made = Repo(tmp_path / "work")
    made.write("pkg/long.py", _function("listed", 70))
    made.commit("before the budget")
    made.write("budget.toml", LEDGER_HEAD)
    code, out = made.budget("--update", "--baseline", "HEAD")
    assert code == 0, out
    made.commit("adopt the budget")
    return made


def test_a_raise_in_a_non_tip_commit_fails_the_walk_where_the_tip_check_passes(
    repo: Repo,
) -> None:
    before = repo.git("rev-parse", "HEAD")
    repo.write("pkg/long.py", _function("listed", 75))
    ledger = (repo.root / "budget.toml").read_text(encoding="utf-8")
    repo.write("budget.toml", ledger.replace('listed" = 70', 'listed" = 75'))
    raised = repo.commit("raise the ceiling")
    repo.write("pkg/other.py", "VALUE = 1\n")
    repo.commit("an unrelated tip")
    # The old CI step: the tip against its parent sees no rise.
    assert repo.budget("--check", "--baseline", "HEAD^")[0] == 0
    code, out = repo.walk(before)
    assert code == 1, out
    assert f"{raised}: raised_ceiling: function_body_lines pkg/long.py::listed: 70 -> 75" in out
    assert "structure budget: 1 violation(s) across 2 commit(s)" in out


def test_a_first_parent_walk_judges_the_merge_and_not_its_side_branch(repo: Repo) -> None:
    before = repo.git("rev-parse", "HEAD")
    ledger = (repo.root / "budget.toml").read_text(encoding="utf-8")
    repo.git("checkout", "-q", "-b", "side")
    repo.write("pkg/long.py", _function("listed", 75))
    repo.write("budget.toml", ledger.replace('listed" = 70', 'listed" = 75'))
    raised = repo.commit("raise a listed ceiling on the side branch")
    repo.write("pkg/long.py", _function("listed", 70))
    repo.write("budget.toml", ledger)
    repo.commit("lower it again on the side branch")
    repo.git("checkout", "-q", "main")
    repo.write("pkg/other.py", "VALUE = 1\n")
    mainline = repo.commit("a mainline commit")
    repo.git("merge", "--no-ff", "-q", "-m", "merge the side branch", "side")
    merge = repo.git("rev-parse", "HEAD")
    # Judged on its own, the side commit raises the ceiling.
    code, out = repo.walk(before, raised)
    assert code == 1, out
    assert f"{raised}: raised_ceiling: function_body_lines pkg/long.py::listed: 70 -> 75" in out
    # First-parent, the push is the mainline commit and the merge, net equal to its first parent.
    listed = walk_range(
        repo.root, before, merge, default_branch=None, max_commits=DEFAULT_MAX_COMMITS
    )
    assert listed == (mainline, merge)
    assert repo.walk(before, merge) == (0, "structure budget holds on every commit: 2 judged\n")


def test_an_empty_range_judges_the_tip_against_its_parent(repo: Repo) -> None:
    tip = repo.git("rev-parse", "HEAD")
    listed = walk_range(repo.root, tip, tip, default_branch=None, max_commits=DEFAULT_MAX_COMMITS)
    assert listed == (tip,)
    assert repo.walk(tip, tip) == (0, "structure budget holds on every commit: 1 judged\n")
    # A tip that raises a ceiling is refused even when the walk starts at it.
    ledger = (repo.root / "budget.toml").read_text(encoding="utf-8")
    repo.write("pkg/long.py", _function("listed", 75))
    repo.write("budget.toml", ledger.replace('listed" = 70', 'listed" = 75'))
    raised = repo.commit("raise a listed ceiling at the tip")
    code, out = repo.walk(raised, raised)
    assert code == 1, out
    assert f"{raised}: raised_ceiling: function_body_lines pkg/long.py::listed: 70 -> 75" in out
    assert "structure budget: 1 violation(s) across 1 commit(s)" in out


def test_a_clean_range_holds_on_every_commit(repo: Repo) -> None:
    before = repo.git("rev-parse", "HEAD")
    repo.write("pkg/a.py", "A = 1\n")
    repo.commit()
    repo.write("pkg/b.py", "B = 1\n")
    repo.commit()
    assert repo.walk(before) == (0, "structure budget holds on every commit: 2 judged\n")


def test_the_four_cases_skip_adopt_refuse_and_judge(repo: Repo) -> None:
    root_commit = repo.git("rev-list", "--max-parents=0", "HEAD")
    first = repo.git("rev-parse", "HEAD~1")
    # Skipped: neither the pre-budget commit nor its parent holds a ledger.
    second = repo.git("rev-parse", "HEAD")
    assert first == root_commit
    # Adoption: walking from the pre-budget commit judges the adopting commit on its own ledger.
    assert repo.walk(first) == (0, "structure budget holds on every commit: 1 judged\n")
    # Refused: a commit that removes the ledger its parent holds, at that commit.
    (repo.root / "budget.toml").unlink()
    removed = repo.commit("drop the ledger")
    code, out = repo.walk(second)
    assert code == 2
    assert f"ledger_removed: {removed} drops the budget.toml its parent holds" in out
    # Refused: an emptied ledger cannot be parsed.
    repo.write("budget.toml", "")
    repo.commit("empty ledger")
    code, out = repo.walk(removed)
    assert code == 2
    assert "ledger_malformed" in out


def test_a_commit_before_the_budget_is_skipped_by_name(tmp_path: Path) -> None:
    made = Repo(tmp_path / "work")
    made.write("pkg/a.py", "A = 1\n")
    base = made.commit()
    made.write("pkg/b.py", "B = 1\n")
    later = made.commit()
    code, out = made.walk(base)
    assert code == 0
    assert f"{later}: predates the structure budget; skipped" in out


def test_an_all_zero_before_walks_from_the_merge_base_with_the_default_branch(
    repo: Repo,
) -> None:
    repo.git("branch", "trunk")
    repo.write("pkg/a.py", "A = 1\n")
    repo.commit()
    repo.write("pkg/b.py", "B = 1\n")
    repo.commit()
    code, out = repo.walk(ZERO, "HEAD", "--default-branch", "trunk")
    assert (code, out) == (0, "structure budget holds on every commit: 2 judged\n")
    # When the merge base is the tip itself, the tip alone is judged against its parent.
    code, out = repo.walk(ZERO, "HEAD", "--default-branch", "HEAD")
    assert (code, out) == (0, "structure budget holds on every commit: 1 judged\n")
    code, out = repo.walk(ZERO)
    assert code == 2
    assert "an all-zero --walk-from needs --default-branch" in out


def test_an_all_zero_before_with_no_shared_history_is_refused(repo: Repo) -> None:
    tip = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "--orphan", "unrelated")
    repo.commit("an unrelated root")
    code, out = repo.walk(ZERO, tip, "--default-branch", "unrelated")
    assert code == 2, out
    assert f"walk_refused: git merge-base {tip} unrelated failed with status 1" in out


def test_a_range_git_cannot_list_is_refused_not_judged_as_its_tip(
    repo: Repo, tmp_path: Path
) -> None:
    before = repo.git("rev-parse", "HEAD")
    repo.write("pkg/long.py", _function("listed", 75))
    ledger = (repo.root / "budget.toml").read_text(encoding="utf-8")
    repo.write("budget.toml", ledger.replace('listed" = 70', 'listed" = 75'))
    repo.commit("raise the ceiling")
    repo.write("pkg/other.py", "VALUE = 1\n")
    tip = repo.commit("an unrelated tip")
    # Real git for every call except the listing, which fails as a broken repository would.
    real = shutil.which("git")
    assert real is not None
    planted = tmp_path / "listing-fails"
    planted.mkdir()
    script = planted / "git"
    script.write_text(
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        '  if [ "$argument" = rev-list ]; then echo "fatal: planted" >&2; exit 128; fi\n'
        "done\n"
        f'exec "{real}" "$@"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    path = f"{planted}{os.pathsep}{os.environ['PATH']}"
    code, out = repo.walk(before, env={**os.environ, "PATH": path})
    # Judged as its tip alone the range would pass; the raise is in the commit before it.
    assert code == 2, out
    assert f"walk_refused: git rev-list {before}..{tip} failed with status 128" in out


def test_a_before_that_is_not_an_ancestor_is_refused(repo: Repo) -> None:
    tip = repo.git("rev-parse", "HEAD")
    repo.git("checkout", "-q", "-b", "side", "HEAD~1")
    repo.write("pkg/side.py", "S = 1\n")
    side = repo.commit()
    code, out = repo.walk(side, tip)
    assert code == 2
    assert f"walk_refused: {side} is not an ancestor of {tip}" in out
    code, out = repo.walk("no-such-revision", tip)
    assert code == 2
    assert "walk_refused: unknown revision 'no-such-revision'" in out


def test_a_range_above_the_bound_is_refused_with_the_manual_command(repo: Repo) -> None:
    before = repo.git("rev-parse", "HEAD")
    for index in range(3):
        repo.write(f"pkg/m{index}.py", f"M = {index}\n")
        repo.commit()
    tip = repo.git("rev-parse", "HEAD")
    code, out = repo.walk(before, tip, "--max-commits", "2")
    assert code == 2
    assert "walk_refused: 3 commits exceed the bound of 2" in out
    assert f"--walk-from {before} --to {tip} --max-commits 3` by hand" in out
    assert repo.walk(before, tip, "--max-commits", "3")[0] == 0


def test_the_walk_runs_only_with_check(repo: Repo) -> None:
    code, out = repo.budget("--update", "--walk-from", "HEAD~1")
    assert code == 2
    assert "--walk-from judges only, so it runs only with --check" in out


def test_an_unparsable_source_in_a_walked_commit_cannot_be_judged(repo: Repo) -> None:
    before = repo.git("rev-parse", "HEAD")
    repo.write("pkg/broken.py", "def broken(:\n")
    repo.commit()
    code, out = repo.walk(before)
    assert code == 2
    assert "structure budget cannot be judged" in out


def test_a_walk_without_a_runnable_git_cannot_be_judged(repo: Repo, tmp_path: Path) -> None:
    empty = tmp_path / "no-git-here"
    empty.mkdir()
    code, out = repo.walk("HEAD~1", env={**os.environ, "PATH": str(empty)})
    assert code == 2
    assert "cannot run git" in out


def test_a_walked_commit_with_a_missing_object_cannot_be_judged(repo: Repo) -> None:
    before = repo.git("rev-parse", "HEAD")
    repo.write("pkg/lost.py", "LOST = 1\n")
    repo.commit()
    blob = repo.git("rev-parse", "HEAD:pkg/lost.py")
    (repo.root / ".git" / "objects" / blob[:2] / blob[2:]).unlink()
    code, out = repo.walk(before)
    assert code == 2
    assert "structure budget cannot be judged: baseline_unavailable" in out
