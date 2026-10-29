# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — timed complete-discovery scheduling and real pytest journeys
"""Exercise shared snapshots and balanced scheduling through the actual pytest plugin."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from tools.ci_shard_artifacts import verify
from tools.pytest_shard_schedule import (
    load_shard_timings,
    partition_test_modules,
    validate_module_durations,
)

from test_ci_shard_artifacts import (
    PYTHON,
    SOURCE,
    TOOL,
    _artifacts,
    _prepare,
    _pytest,
    _run,
)
from test_ci_shard_artifacts import (
    actual_artifacts as actual_artifacts,
)


@pytest.mark.parametrize("weights", [{}, {"test_a.py": 9, "test_b.py": 8, "test_c.py": 1}])
def test_complete_current_modules_are_balanced_once(weights: dict[str, float]) -> None:
    """New cases enter, removed modules stay absent and each module retains its fixtures."""
    inventory = [
        "test_a.py::test_one",
        "test_a.py::test_two",
        "test_b.py::test_one",
        "test_c.py::test_new",
    ]
    parts = partition_test_modules(inventory, 2, {**weights, "test_removed.py": 900})
    assert sorted(node for part in parts for node in part) == sorted(inventory)
    assert any(set(inventory[:2]) <= set(part) for part in parts)
    assert parts == partition_test_modules(list(reversed(inventory)), 2, weights)
    if weights:
        assert parts == (tuple(inventory[:2]), tuple(inventory[2:]))


@pytest.mark.parametrize("weight", [-1, True, math.inf, -math.inf, math.nan, "1", None, 86401])
def test_malformed_durations_cannot_guide_execution(weight: object) -> None:
    """Invalid numeric history fails before any scheduler can choose tests."""
    with pytest.raises(ValueError, match="invalid module timing"):
        validate_module_durations({"test_a.py": weight})


@pytest.mark.parametrize(
    "name", ["../test_a.py", "/test_a.py", "test_a.txt", "a\\test_a.py", "a\0.py", 1]
)
def test_unsafe_or_nonmodule_timing_names_are_refused(name: object) -> None:
    """Historical metadata cannot supply absolute, traversal or malformed module keys."""
    with pytest.raises(ValueError, match="invalid module timing"):
        validate_module_durations({name: 1})


@pytest.mark.parametrize("value", [[], None, "timings"])
def test_duration_object_is_required(value: object) -> None:
    """Wrong JSON containers are rejected instead of interpreted as cold history."""
    with pytest.raises(ValueError, match="must be an object"):
        validate_module_durations(value)


@pytest.mark.parametrize("count", [0, 129, -1, True])
def test_invalid_worker_count_is_not_an_execution_profile(count: int) -> None:
    """A malformed count never produces an apparently complete partition."""
    with pytest.raises(ValueError, match="shard count"):
        partition_test_modules(["test_a.py::test_one"], count, {})


@pytest.mark.parametrize("inventory", [[""], ["test_a.py::a", "test_a.py::a"]])
def test_invalid_native_inventory_is_refused(inventory: list[str]) -> None:
    """Empty identifiers and duplicates cannot hide omissions behind balancing."""
    with pytest.raises(ValueError, match="test inventory"):
        partition_test_modules(inventory, 2, {})


@pytest.mark.parametrize(
    "kind", ["missing", "array", "schema", "profile", "duplicate", "symlink", "oversized"]
)
def test_shared_snapshot_refuses_corrupt_inputs(tmp_path: Path, kind: str) -> None:
    """Every worker must reject the same malformed snapshot rather than silently diverge."""
    path = tmp_path / "snapshot.json"
    if kind == "missing":
        pass
    elif kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text('{"schema":1,"profiles":{}}')
        path.symlink_to(target)
    elif kind == "oversized":
        with path.open("wb") as output:
            output.truncate(16 * 1024 * 1024 + 1)
    else:
        path.write_text(
            {
                "array": "[]",
                "schema": '{"schema":true,"profiles":{}}',
                "profile": '{"schema":1,"profiles":{"invalid":{}}}',
                "duplicate": '{"schema":1,"schema":1,"profiles":{}}',
            }[kind]
        )
    with pytest.raises(ValueError):
        load_shard_timings(path, PYTHON)


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("count", [2, 4])
def test_real_timed_workers_execute_complete_discovery(
    tmp_path: Path, history: bool, count: int
) -> None:
    """Actual pytest runners share one snapshot and execute newly added modules exactly once."""
    root = tmp_path / "project"
    _prepare(root)
    (root / "test_b.py").write_text("def test_b():\n    assert 2 + 2 == 4\n")
    (root / "test_c.py").write_text("def test_new():\n    assert 3 + 3 == 6\n")
    (root / "test_d.py").write_text("def test_another_new():\n    assert 4 + 4 == 8\n")
    weights = {"test_project.py": 9, "test_b.py": 8, "test_removed.py": 999} if history else {}
    snapshot = root / "snapshot.json"
    snapshot.write_text(json.dumps({"schema": 1, "profiles": {PYTHON: weights}}))
    digest, retained = load_shard_timings(snapshot, PYTHON)
    assert digest == hashlib.sha256(snapshot.read_bytes()).hexdigest() and retained == weights
    assert load_shard_timings(snapshot, "9.99")[1] == {}
    records = []
    for index in range(count):
        result = _run(
            root,
            [
                "-m",
                "pytest",
                "test_project.py",
                "test_b.py",
                "test_c.py",
                "test_d.py",
                "-p",
                "tools.pytest_shard",
                f"--shard-index={index}",
                f"--shard-count={count}",
                f"--shard-output=part-{index}.json",
                "--shard-timings=snapshot.json",
            ],
        )
        assert result.returncode == 0, result.stdout + result.stderr
        records.append(json.loads((root / f"part-{index}.json").read_text()))
    inventory = records[0]["inventory"]
    assert all(record["inventory"] == inventory for record in records)
    assert sorted(node for record in records for node in record["selected"]) == inventory
    assert len(inventory) == 5
    assert all(record["schedule"]["timings_sha256"] == digest for record in records)
    assert all(record["completed"] == record["selected"] for record in records)
    assert all(value >= 0 for record in records for value in record["module_durations"].values())
    assert sorted((root / "executed").read_text().splitlines()) == ["a", "b"]
    assert not any("removed" in node for node in inventory)


def test_real_runner_refuses_malformed_shared_history_before_tests(tmp_path: Path) -> None:
    """Corrupt timing input cannot execute tests under a different accidental schedule."""
    root = tmp_path / "project"
    _prepare(root)
    (root / "snapshot.json").write_text('{"schema":1,"profiles":{"3.12":{"test_project.py":NaN}}}')
    result = _run(
        root,
        [
            "-m",
            "pytest",
            "test_project.py",
            "-p",
            "tools.pytest_shard",
            "--shard-index=0",
            "--shard-count=2",
            "--shard-output=part.json",
            "--shard-timings=snapshot.json",
        ],
    )
    assert result.returncode != 0 and "invalid shared shard timings" in result.stderr
    assert not (root / "executed").exists() and not (root / "part.json").exists()


@pytest.fixture(scope="module")
def actual_timed_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build genuine timed workers with disjoint branch coverage and native follow-up."""
    root = tmp_path_factory.mktemp("actual-timed-shards") / "project"
    _prepare(root)
    (root / "test_project.py").write_text(
        "from app import value\ndef test_a():\n    assert value(False) == 0\n"
    )
    (root / "test_b.py").write_text(
        "from app import value\ndef test_b():\n    assert value(True) == 1\n"
    )
    snapshot = {"schema": 1, "profiles": {PYTHON: {"test_project.py": 9, "test_b.py": 2}}}
    (root / "snapshot.json").write_text(json.dumps(snapshot))
    for index in range(2):
        result = _run(
            root,
            [
                "-m",
                "pytest",
                "test_project.py",
                "test_b.py",
                "-p",
                "tools.pytest_shard",
                "-p",
                "pytest_cov.plugin",
                f"--shard-index={index}",
                "--shard-count=2",
                f"--shard-output=main-{index}.json",
                "--shard-timings=snapshot.json",
                "--cov=app",
                "--cov-config=pyproject.toml",
                "--cov-report=",
                "--cov-fail-under=0",
            ],
            coverage_file=root / f"data-{index}",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        if index == 0:
            result = _pytest(root, 0, native=True)
            assert result.returncode == 0, result.stdout + result.stderr
        result = _run(
            root,
            [
                str(TOOL),
                "pack",
                "--manifest",
                f"main-{index}.json",
                "--coverage-file",
                f"data-{index}",
                "--output",
                f"artifacts/{index}",
                *(["--native-manifest", "native.json"] if index == 0 else []),
            ],
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return root


def _verify_timed(root: Path) -> subprocess.CompletedProcess[str]:
    """Use the public verify CLI with the exact common immutable input."""
    return _run(
        root,
        [
            str(TOOL),
            "verify",
            "--root",
            "artifacts",
            "--source",
            SOURCE,
            "--run-id",
            "actual-runner-fixture",
            "--attempt",
            "1",
            "--python",
            PYTHON,
            "--count",
            "2",
            "--coverage-python",
            PYTHON,
            "--coverage-config",
            "pyproject.toml",
            "--fail-under",
            "98",
            "--timings",
            "snapshot.json",
            "--output",
            "reports",
        ],
    )


def test_public_timed_union_requires_snapshot_and_emits_observed_history(
    actual_timed_artifacts: Path, tmp_path: Path
) -> None:
    """The exact successful timed union measures both branches and seeds the next run."""
    root = tmp_path / "project"
    shutil.copytree(actual_timed_artifacts, root)
    result = _verify_timed(root)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((root / "reports/coverage.json").read_text())
    assert report["totals"]["percent_covered"] == 100
    _digest, durations = load_shard_timings(root / "reports/timings.json", PYTHON)
    assert set(durations) == {"test_project.py", "test_b.py"}
    assert all(seconds > 0 for seconds in durations.values())


def test_public_verify_api_combines_real_data_and_writes_next_history(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """The Python API performs the same genuine complete-union gate as the command."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    verify(
        root / "artifacts",
        SOURCE,
        "actual-runner-fixture",
        "1",
        [PYTHON],
        2,
        PYTHON,
        root / "pyproject.toml",
        root / "reports",
        100,
    )
    _digest, durations = load_shard_timings(root / "reports/timings.json", PYTHON)
    assert set(durations) == {"test_project.py"}
    assert durations["test_project.py"] > 0


@pytest.mark.parametrize("threshold", [98, 100])
def test_public_verify_api_refuses_real_incomplete_branch_observations(
    tmp_path: Path, threshold: float
) -> None:
    """Passing tests still cannot publish timings when genuine production coverage is low."""
    root = tmp_path / "project"
    _artifacts(root, both_branches=False)
    with pytest.raises(ValueError, match="is below"):
        verify(
            root / "artifacts",
            SOURCE,
            "actual-runner-fixture",
            "1",
            [PYTHON],
            2,
            PYTHON,
            root / "pyproject.toml",
            root / "reports",
            threshold,
        )
    assert not (root / "reports/timings.json").exists()


@pytest.mark.parametrize("damage", ["object", "keys", "algorithm", "digest-type", "digest-size"])
def test_public_pack_refuses_malformed_timing_schedule(
    actual_timed_artifacts: Path, tmp_path: Path, damage: str
) -> None:
    """Altered scheduling metadata cannot become a successful indexed publication."""
    root = tmp_path / "project"
    shutil.copytree(actual_timed_artifacts, root)
    path = root / "main-1.json"
    record = json.loads(path.read_text())
    if damage == "object":
        record["schedule"] = []
    elif damage == "keys":
        record["schedule"]["foreign"] = True
    elif damage == "algorithm":
        record["schedule"]["algorithm"] = "invented"
    elif damage == "digest-type":
        record["schedule"]["timings_sha256"] = 1
    else:
        record["schedule"]["timings_sha256"] = "short"
    path.write_text(json.dumps(record))
    result = _run(root, [str(TOOL), "pack", "--manifest", "main-1.json", "--output", "refused"])
    assert result.returncode == 1 and "invalid indexed timing schedule" in result.stderr
    assert not (root / "refused").exists()


@pytest.mark.parametrize(
    ("field", "value"), [("index", -1), ("index", True), ("count", 0), ("count", None)]
)
def test_public_pack_refuses_invalid_coordinates_before_indexing(
    actual_timed_artifacts: Path, tmp_path: Path, field: str, value: object
) -> None:
    """A corrupted coordinate cannot index or slice a valid collection."""
    root = tmp_path / "project"
    shutil.copytree(actual_timed_artifacts, root)
    path = root / "main-1.json"
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    result = _run(root, [str(TOOL), "pack", "--manifest", "main-1.json", "--output", "refused"])
    assert result.returncode == 1 and "incomplete or misidentified" in result.stderr
    assert not (root / "refused").exists()


def test_real_workers_cannot_mix_independently_selected_timing_snapshots(
    actual_timed_artifacts: Path, tmp_path: Path
) -> None:
    """A genuine worker using another history cannot pass the common-input aggregate."""
    root = tmp_path / "project"
    shutil.copytree(actual_timed_artifacts, root)
    history = json.loads((root / "snapshot.json").read_text())
    history["profiles"][PYTHON]["test_b.py"] = 100
    (root / "other.json").write_text(json.dumps(history))
    result = _run(
        root,
        [
            "-m",
            "pytest",
            "test_project.py",
            "test_b.py",
            "-p",
            "tools.pytest_shard",
            "--shard-index=1",
            "--shard-count=2",
            "--shard-output=main-1.json",
            "--shard-timings=other.json",
            "-p",
            "pytest_cov.plugin",
            "--cov=app",
            "--cov-config=pyproject.toml",
            "--cov-report=",
            "--cov-fail-under=0",
        ],
        coverage_file=root / "data-1",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    shutil.rmtree(root / "artifacts/1")
    result = _run(
        root,
        [
            str(TOOL),
            "pack",
            "--manifest",
            "main-1.json",
            "--coverage-file",
            "data-1",
            "--output",
            "artifacts/1",
        ],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = _verify_timed(root)
    assert result.returncode == 1 and "different shared timing snapshot" in result.stderr
    assert not (root / "reports").exists()
