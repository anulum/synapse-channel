# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worker-session verified completion benchmark
"""The benchmark reads worker outcomes and verify-result, and it is not product code."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PATH = _ROOT / "benchmarks" / "verified_completion_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("verified_completion_benchmark", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench
_SPEC.loader.exec_module(bench)


def test_the_module_is_not_shipped_in_the_package() -> None:
    """Benchmark code stays beside the harness."""
    assert not (_ROOT / "src" / "synapse_channel" / "verified_completion.py").exists()


def test_docs_name_the_limits() -> None:
    """The public text names the benchmark this run does not replace."""
    phrase = (
        "the reproducible public fault-and-effect benchmark (baseline, Core and Core with Fleet)"
    )
    for relative in (
        "docs/benchmarks.md",
        "benchmarks/README.md",
        "benchmarks/results/verified_completion_benchmark.json",
    ):
        text = (_ROOT / relative).read_text(encoding="utf-8")
        assert "worker-session" in text or relative.endswith(".json")
        assert "verify-result" in text or relative.endswith(".json")
        assert phrase in text
        assert "network fault" in text or "network faults" in text


def test_one_task_of_each_fault_shows_the_gap(tmp_path: Path) -> None:
    """Reported completion and verified content come from the shipped commands."""
    baseline = bench.cohort(tmp_path / "baseline", "baseline", 1)
    wrong = bench.cohort(tmp_path / "wrong", "wrong_content", 1)
    absent = bench.cohort(tmp_path / "absent", "absent", 1)
    crash = bench.cohort(tmp_path / "crash", "crash_after_effect", 1)
    assert baseline == {
        "tasks": 1,
        "confirmed_done": 1,
        "unknown": 0,
        "verified": 1,
        "not_verified": 0,
    }
    assert wrong["confirmed_done"] == 1
    assert wrong["verified"] == 0
    assert wrong["not_verified"] == 1
    assert absent["confirmed_done"] == 1
    assert absent["not_verified"] == 1
    assert crash["confirmed_done"] == 0
    assert crash["unknown"] == 1
    assert crash["verified"] == 1


def test_run_rejects_an_empty_cohort() -> None:
    """A zero-task run is not a measurement."""
    with pytest.raises(ValueError, match="at least one"):
        bench.run(tasks=0)


def test_cohort_rejects_an_empty_or_unknown_fault(tmp_path: Path) -> None:
    """The counter refuses a cohort that is not one of the four worker faults."""
    with pytest.raises(ValueError, match="at least one"):
        bench.cohort(tmp_path, "baseline", 0)
    with pytest.raises(ValueError, match="four worker modes"):
        bench.cohort(tmp_path, "not-a-mode", 1)


def test_launch_env_prepends_this_worktree(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker imports the worktree package, keeping any existing path."""
    monkeypatch.setenv("PYTHONPATH", "already")
    inherited = bench._launch_env()
    src = str(_ROOT / "src")
    assert inherited["PYTHONPATH"] == src + os.pathsep + "already"
    assert inherited["SYNAPSE_NO_UPDATE_CHECK"] == "1"
    monkeypatch.delenv("PYTHONPATH")
    bare = bench._launch_env()
    assert bare["PYTHONPATH"] == src


def test_label_keeps_a_reported_cpu_and_replaces_an_empty_one() -> None:
    """An empty platform CPU string is stored as unknown."""
    assert bench._label("cpu", "unknown") == "cpu"
    assert bench._label("", "unknown") == "unknown"


def test_a_timed_out_session_is_still_reaped() -> None:
    """A wait that times out still stops the session group."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        bench._wait_and_reap(process, 0.2)
    process.wait(timeout=5)
    assert process.returncode is not None


def test_reap_stops_a_live_group_and_ignores_one_already_gone() -> None:
    """The crash cohort can reap a sleeper, and a finished group is not an error."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    bench._reap_session(process.pid)
    process.wait(timeout=5)
    bench._reap_session(process.pid)
    assert process.returncode is not None


def test_a_second_task_reuses_the_worker_script(tmp_path: Path) -> None:
    """One cohort directory keeps a single worker program."""
    root = tmp_path / "baseline"
    first = bench.cohort(root, "baseline", 1)
    second = bench.cohort(root, "baseline", 1)
    assert first["verified"] == 1
    assert second["verified"] == 1
    assert (root / "worker.py").is_file()


def test_an_unreadable_outcome_is_not_a_count(tmp_path: Path) -> None:
    """A report the shipped reader cannot open is a failed measurement."""
    root = tmp_path / "broken"
    root.mkdir()
    (root / "baseline-0.json").mkdir()
    with pytest.raises(RuntimeError, match="not readable"):
        bench.run_task(root, "baseline", 0)


def test_a_directory_effect_is_not_a_decision(tmp_path: Path) -> None:
    """verify-result ``not_checked`` is neither verified nor not verified."""
    root = tmp_path / "absent"
    root.mkdir()
    (root / "absent-0.txt").mkdir()
    with pytest.raises(RuntimeError, match="did not decide"):
        bench.cohort(root, "absent", 1)


def test_main_writes_one_task_of_each_cohort(tmp_path: Path) -> None:
    """The command records the four cohorts and returns the same document."""
    destination = tmp_path / "nested" / "verified_completion_benchmark.json"
    assert bench.main(["--tasks", "1", "--results", str(destination)]) == 0
    record = bench.run(tasks=1)
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert written["schema"] == "synapse.verified-completion-benchmark.v2"
    assert (
        "the reproducible public fault-and-effect benchmark (baseline, Core and Core with Fleet)"
        in written["not_measured"]
    )
    assert written["sidecar"] == "off"
    assert written["tasks_per_cohort"] == 1
    assert written["baseline"]["verified"] == 1
    assert written["wrong_content"]["not_verified"] == 1
    assert written["absent"]["confirmed_done"] == 1
    assert written["crash_after_effect"]["unknown"] == 1
    assert written["crash_after_effect"]["verified"] == 1
    assert record["schema"] == written["schema"]
    assert isinstance(record["host"], dict)
    assert record["baseline"]["verified"] == 1


def test_module_entry_prints_help() -> None:
    """Running the benchmark file as the program reaches its console guard."""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(_ROOT / "src"), env.get("PYTHONPATH", "")]).rstrip(
        os.pathsep
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and module path
        [sys.executable, str(_PATH), "--help"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "usage:" in completed.stdout.lower()
