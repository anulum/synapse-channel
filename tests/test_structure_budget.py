# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the structure budget rejects growth and admits only what it should
"""Run the real structure-budget checker on real git repositories with planted violations.

Every test starts ``python -m tools.check_structure_budget`` as a process, the
way the pre-commit hook and the CI lint job start it, against a small
repository built in a temporary directory. A violation must end with exit
status 1 and its reason code; an input that cannot be judged with status 2.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LONG = 70
"""Body lines of the listed function in the fixture; the threshold is 60."""

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
    """A temporary git repository with one measured package and a ledger."""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "pkg").mkdir(parents=True)
        self.git("init", "-q")
        self.write("pkg/long.py", _function("listed", LONG))

    def git(self, *arguments: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@example.invalid"]
            + list(arguments),
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def ledger(self) -> str:
        return (self.root / "budget.toml").read_text(encoding="utf-8")

    def append_ledger(self, text: str) -> None:
        self.write("budget.toml", self.ledger() + text)

    def commit(self) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "state")

    def run(self, mode: str, *baseline: str) -> tuple[int, str]:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.check_structure_budget",
                mode,
                *(baseline or ("--baseline", "HEAD")),
                "--root",
                str(self.root),
                "--ledger",
                "budget.toml",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    """Return a repository whose ledger lists one 70-line function, committed."""
    made = Repo(tmp_path / "work")
    made.commit()
    made.write("budget.toml", LEDGER_HEAD)
    code, out = made.run("--update")
    assert code == 0, out
    assert "bootstrap: HEAD holds no ledger" in out
    assert '"pkg/long.py::listed" = 70' in made.ledger()
    assert made.ledger().startswith("# SPDX-License-Identifier: AGPL-3.0-or-later\n")
    made.commit()
    return made


def test_clean_tree_holds_and_reports_nothing_changed(repo: Repo) -> None:
    assert repo.run("--check") == (0, "structure budget holds: listed figures 1, exceptions 0\n")
    assert repo.run("--report") == (0, "no listed figure changed\n")


def test_new_unit_over_a_threshold_is_refused_and_cannot_be_added_by_update(repo: Repo) -> None:
    parameters = ", ".join(f"p{i}" for i in range(13))
    repo.write("pkg/wide.py", f"class Wide:\n    def __init__(self, {parameters}):\n        pass\n")
    before = repo.ledger()
    code, out = repo.run("--check")
    assert code == 1
    assert "over_threshold_unlisted: constructor_parameters pkg/wide.py::Wide" in out
    code, out = repo.run("--update")
    assert code == 1
    assert "new_debt: constructor_parameters pkg/wide.py::Wide" in out
    assert repo.ledger() == before


def test_twelve_parameters_are_within_the_threshold(repo: Repo) -> None:
    parameters = ", ".join(f"p{i}" for i in range(12))
    repo.write("pkg/wide.py", f"class Wide:\n    def __init__(self, {parameters}):\n        pass\n")
    assert repo.run("--check")[0] == 0


def test_listed_unit_may_not_grow_even_with_an_edited_ledger(repo: Repo) -> None:
    repo.write("pkg/long.py", _function("listed", LONG + 5))
    code, out = repo.run("--check")
    assert code == 1
    assert (
        "above_ceiling: function_body_lines pkg/long.py::listed: 75 is above its ceiling 70" in out
    )
    before = repo.ledger()
    code, out = repo.run("--update")
    assert code == 1
    assert "raised_ceiling: function_body_lines pkg/long.py::listed: 70 -> 75" in out
    assert repo.ledger() == before
    repo.write("budget.toml", before.replace('listed" = 70', 'listed" = 75'))
    code, out = repo.run("--check")
    assert code == 1
    assert "raised_ceiling" in out


def test_shortened_unit_must_lower_the_ledger_and_update_does_it(repo: Repo) -> None:
    repo.write("pkg/long.py", _function("listed", LONG - 5))
    code, out = repo.run("--check")
    assert code == 1
    assert (
        "stale_ceiling: function_body_lines pkg/long.py::listed: 65 is below its ceiling 70" in out
    )
    assert repo.run("--update")[0] == 0
    assert '"pkg/long.py::listed" = 65' in repo.ledger()
    assert repo.run("--check")[0] == 0
    assert repo.run("--report") == (0, "function_body_lines pkg/long.py::listed: 70 -> 65\n")


def test_unit_brought_under_its_threshold_leaves_the_ledger(repo: Repo) -> None:
    repo.write("pkg/long.py", _function("listed", 60))
    code, out = repo.run("--check")
    assert code == 1
    assert "stale_entry: function_body_lines pkg/long.py::listed" in out
    assert repo.run("--update")[0] == 0
    assert "pkg/long.py::listed" not in repo.ledger()
    assert repo.run("--report") == (
        0,
        "function_body_lines pkg/long.py::listed: 70 -> within threshold\n",
    )


def test_private_access_to_another_object_is_new_debt_but_the_allowed_name_is_not(
    repo: Repo,
) -> None:
    repo.write("pkg/parser.py", "import argparse\n\nKIND = argparse._SubParsersAction\n")
    assert repo.run("--check")[0] == 0
    repo.write("pkg/reach.py", "def reach(hub):\n    return hub._system\n")
    code, out = repo.run("--check")
    assert code == 1
    assert "over_threshold_unlisted: private_access pkg/reach.py: 1 is above the threshold 0" in out


def test_recorded_move_carries_the_ceiling_and_must_not_grow(repo: Repo) -> None:
    (repo.root / "pkg/long.py").unlink()
    repo.write("pkg/moved.py", _function("listed", LONG))
    move = '\n[[moves]]\nfrom = "pkg/long.py::listed"\nto = "pkg/moved.py::listed"\n'
    repo.append_ledger(move + 'measure = "function_body_lines"\n')
    assert repo.run("--update")[0] == 0
    assert '"pkg/moved.py::listed" = 70' in repo.ledger()
    assert repo.run("--check")[0] == 0

    repo.write("pkg/moved.py", _function("listed", LONG + 1))
    code, out = repo.run("--update")
    assert code == 1
    assert "raised_ceiling: function_body_lines pkg/moved.py::listed: 70 -> 71" in out

    repo.write("pkg/moved.py", _function("listed", LONG))
    repo.commit()
    code, out = repo.run("--check")
    assert code == 1
    assert "stale_move: function_body_lines pkg/moved.py::listed" in out


def test_move_from_a_unit_the_baseline_never_listed_cannot_admit_new_debt(repo: Repo) -> None:
    repo.write("pkg/fresh.py", _function("fresh", LONG))
    move = '\n[[moves]]\nfrom = "pkg/never.py::listed"\nto = "pkg/fresh.py::fresh"\n'
    repo.append_ledger(move + 'measure = "function_body_lines"\n')
    code, out = repo.run("--update")
    assert code == 1
    assert "new_debt: function_body_lines pkg/fresh.py::fresh" in out
    assert "stale_move: function_body_lines pkg/fresh.py::fresh" in out
    assert "Traceback" not in out


def test_move_carries_a_ceiling_for_its_own_measure_only(tmp_path: Path) -> None:
    branches = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(16))
    padding = "".join(f"    v{i} = {i}\n" for i in range(40))
    source = f"def tangled(a):\n{branches}{padding}    return -1\n"
    made = Repo(tmp_path / "work")
    made.write("pkg/long.py", source)
    made.commit()
    made.write("budget.toml", LEDGER_HEAD)
    assert made.run("--update")[0] == 0
    assert '"pkg/long.py::tangled" = 16' in made.ledger()
    made.commit()

    (made.root / "pkg/long.py").unlink()
    made.write("pkg/moved.py", source)
    move = '\n[[moves]]\nfrom = "pkg/long.py::tangled"\nto = "pkg/moved.py::tangled"\n'
    made.append_ledger(move + 'measure = "function_body_lines"\n')
    code, out = made.run("--update")
    assert code == 1
    assert "new_debt: function_branches pkg/moved.py::tangled" in out
    assert "new_debt: function_body_lines" not in out

    made.append_ledger(move + 'measure = "function_branches"\n')
    assert made.run("--update")[0] == 0
    assert made.run("--check")[0] == 0


def test_unrecorded_move_is_new_debt(repo: Repo) -> None:
    (repo.root / "pkg/long.py").unlink()
    repo.write("pkg/moved.py", _function("listed", LONG))
    code, out = repo.run("--update")
    assert code == 1
    assert "new_debt: function_body_lines pkg/moved.py::listed" in out


def _exception(
    unit: str, allowed: int, review_by: str, measure: str = "function_body_lines"
) -> str:
    return (
        f'\n[[exceptions]]\nunit = "{unit}"\nmeasure = "{measure}"\nallowed = {allowed}\n'
        f'reason = "generated table"\nowner = "core"\nreview_by = "{review_by}"\n'
    )


def test_exception_admits_a_new_unit_until_its_review_date(repo: Repo) -> None:
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    past = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    repo.write("pkg/table.py", _function("table", 90))
    head = repo.ledger()
    repo.write("budget.toml", head + _exception("pkg/table.py::table", 90, future))
    assert repo.run("--check")[0] == 0
    assert repo.run("--update")[0] == 0
    assert 'pkg/table.py::table" = ' not in repo.ledger().split("[[exceptions]]")[0]

    repo.write("pkg/table.py", _function("table", 91))
    code, out = repo.run("--check")
    assert code == 1
    assert (
        "above_ceiling: function_body_lines pkg/table.py::table: 91 is above the excepted 90" in out
    )

    repo.write("pkg/table.py", _function("table", 90))
    repo.write("budget.toml", head + _exception("pkg/table.py::table", 90, past))
    code, out = repo.run("--check")
    assert code == 1
    assert f"exception_expired: function_body_lines pkg/table.py::table: review_by {past}" in out

    repo.write("pkg/table.py", _function("table", 10))
    repo.write("budget.toml", head + _exception("pkg/table.py::table", 90, future))
    code, out = repo.run("--check")
    assert code == 1
    assert "exception_unused: function_body_lines pkg/table.py::table" in out


def test_exception_for_an_unknown_measure_cannot_be_judged(repo: Repo) -> None:
    repo.append_ledger(_exception("pkg/x.py::f", 90, "2099-01-01", measure="elegance"))
    code, out = repo.run("--check")
    assert code == 2
    assert "ledger_malformed: exception of pkg/x.py::f names an unknown measure" in out


def test_exception_is_still_valid_on_its_review_date(repo: Repo) -> None:
    today = datetime.date.today().isoformat()
    repo.write("pkg/table.py", _function("table", 90))
    repo.append_ledger(_exception("pkg/table.py::table", 90, today))
    assert repo.run("--check")[0] == 0


def test_incomplete_exception_cannot_be_judged(repo: Repo) -> None:
    repo.append_ledger('\n[[exceptions]]\nunit = "pkg/x.py::f"\nmeasure = "function_body_lines"\n')
    code, out = repo.run("--check")
    assert code == 2
    assert "exception_incomplete" in out
    repo.git("checkout", "-q", "--", "budget.toml")
    repo.append_ledger(_exception("pkg/x.py::f", 90, "next spring"))
    code, out = repo.run("--check")
    assert code == 2
    assert "is not an ISO date" in out


def test_raising_a_threshold_or_allowing_a_private_name_needs_an_exception(repo: Repo) -> None:
    future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
    original = repo.ledger()
    raised = original.replace("function_body_lines = 60", "function_body_lines = 80")
    repo.write("budget.toml", raised)
    code, out = repo.run("--check")
    assert code == 1
    assert "threshold_raised: configuration function_body_lines: 60 -> 80" in out
    excepted = raised + _exception("function_body_lines", 80, future, measure="configuration")
    repo.write("budget.toml", excepted)
    code, out = repo.run("--check")
    assert "threshold_raised" not in out
    assert "stale_entry: function_body_lines pkg/long.py::listed" in out
    lowered = original.replace("function_branches = 15", "function_branches = 14")
    repo.write("budget.toml", lowered)
    assert repo.run("--check")[0] == 0

    grown = original.replace(
        '["argparse._SubParsersAction"]', '["argparse._SubParsersAction", "a._b"]'
    )
    repo.write("budget.toml", grown)
    code, out = repo.run("--check")
    assert code == 1
    assert "allowed_list_grown: configuration a._b" in out
    repo.write("budget.toml", grown + _exception("a._b", 0, future, measure="configuration"))
    assert repo.run("--check")[0] == 0


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        ("schema = 1", "schema = 2"),
        ("private_access = 0", "private_access = -1"),
        ("private_access = 0", "private_access = 0\nunknown_measure = 3"),
        ('roots = ["pkg"]', "roots = []"),
        ("[thresholds]", "[thresholds"),
        ("[thresholds]\n", "thresholds = 3\n[other]\n"),
        ("schema = 1", "schema = 1\nexceptions = 3"),
        ("schema = 1", "schema = 1\nmoves = [{from = 'a'}]"),
        ("schema = 1", "schema = 1\nmoves = 3"),
    ],
)
def test_malformed_ledger_cannot_be_judged(repo: Repo, damage: str, reason: str) -> None:
    repo.write("budget.toml", repo.ledger().replace(damage, reason))
    code, out = repo.run("--check")
    assert code == 2
    assert "ledger_malformed" in out


def test_inputs_that_cannot_be_read_fail_closed(repo: Repo, tmp_path: Path) -> None:
    code, out = repo.run("--check", "--baseline", "no-such-revision")
    assert (code, "baseline_unavailable: unknown revision" in out) == (2, True)

    repo.write("pkg/broken.py", "def broken(:\n")
    code, out = repo.run("--check")
    assert (code, "pkg/broken.py" in out) == (2, True)
    (repo.root / "pkg/broken.py").unlink()

    repo.write("budget.toml", repo.ledger().replace('roots = ["pkg"]', 'roots = ["absent"]'))
    code, out = repo.run("--check")
    assert (code, "measured root is not a directory: absent" in out) == (2, True)

    (repo.root / "budget.toml").unlink()
    code, out = repo.run("--check")
    assert (code, "ledger_missing" in out) == (2, True)

    plain = tmp_path / "plain"
    (plain / "pkg").mkdir(parents=True)
    (plain / "budget.toml").write_text(LEDGER_HEAD, encoding="utf-8")
    outside = Repo.__new__(Repo)
    outside.root = plain
    code, out = outside.run("--check")
    assert (code, "baseline_unavailable" in out) == (2, True)
    assert outside.run("--check", "--no-baseline")[0] == 0


def test_git_that_cannot_answer_fails_closed(repo: Repo, tmp_path: Path) -> None:
    arguments = [sys.executable, "-m", "tools.check_structure_budget", "--check"]
    target = ["--root", str(repo.root)]
    outside = subprocess.run(
        [*arguments, "--baseline", "HEAD", *target, "--ledger", str(tmp_path / "elsewhere.toml")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert outside.returncode == 2
    assert "ledger_missing" in outside.stdout
    (tmp_path / "elsewhere.toml").write_text(LEDGER_HEAD, encoding="utf-8")
    outside = subprocess.run(
        [*arguments, "--baseline", "HEAD", *target, "--ledger", str(tmp_path / "elsewhere.toml")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert outside.returncode == 2
    assert "baseline_unavailable: git ls-tree failed" in outside.stdout
    no_git = subprocess.run(
        [*arguments, "--baseline", "HEAD", *target, "--ledger", "budget.toml"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": str(tmp_path / "empty")},
    )
    assert no_git.returncode == 2
    assert "baseline_unavailable: cannot run git" in no_git.stdout


def test_report_without_a_baseline_says_there_is_nothing_to_compare(repo: Repo) -> None:
    assert repo.run("--report", "--no-baseline") == (0, "no baseline ledger: nothing to compare\n")


def test_a_mode_and_a_baseline_choice_are_both_required(repo: Repo) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "tools.check_structure_budget", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 2
    assert "one of the arguments --baseline --no-baseline is required" in done.stderr


def test_this_repository_meets_its_own_structure_budget() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "tools.check_structure_budget", "--check", "--no-baseline"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
    assert "structure budget holds" in done.stdout


def test_hook_and_ci_run_the_check_against_the_parent_commit() -> None:
    hooks = (REPO / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook = hooks.split("- id: structure-budget")[1].split("- id:")[0]
    assert "entry: python -m tools.check_structure_budget --check --baseline HEAD\n" in hook
    assert "always_run: true" in hook
    assert "pass_filenames: false" in hook
    workflow = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    lint = workflow.split("  lint:\n")[1].split("\n  typecheck:\n")[0]
    assert "fetch-depth: 2" in lint
    assert "run: python -m tools.check_structure_budget --check --baseline HEAD^\n" in lint
    assert "continue-on-error" not in lint
