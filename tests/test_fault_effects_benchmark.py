# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the fault/effect benchmark is seeded, observed independently and repeatable
"""The baseline's duplicates and unauthorised effects come from its faults; Core avoids them."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "fault_effects_benchmark.py"
_SPEC = importlib.util.spec_from_file_location("fault_effects_benchmark", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
bench = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = bench
_SPEC.loader.exec_module(bench)


class Down:
    """An effect target that never answers; its observer never sees the effect."""

    def __init__(self, ledger: object, repository: object) -> None:
        self.ledger = ledger
        self.repository = repository  # the verifier reads the task's file there

    def apply(self, task: object, worker: str) -> None:
        raise bench.LostConnection("down")

    def inquire(self, task: object) -> int:
        return 0


def test_the_schedule_is_seeded_and_covers_every_fault() -> None:
    first, again, other = bench.schedule(7), bench.schedule(7), bench.schedule(8)
    assert first == again and first != other
    faults = [task.fault for task in first]
    assert {fault: faults.count(fault) for fault in bench.FAULTS} == dict.fromkeys(bench.FAULTS, 3)
    assert len(first) == 24 and len({task.task_id for task in first}) == 24
    assert first[0].operation_id == f"op-{first[0].task_id}"
    assert first[0].path == f"tasks/{first[0].task_id}.txt"


def test_the_baseline_duplicates_exactly_where_it_cannot_tell(tmp_path: Path) -> None:
    records = bench.run_baseline(bench.schedule(7), tmp_path / "a")
    by_fault = {fault: [r for r in records if r.fault == fault] for fault in bench.FAULTS}
    assert all(r.verified and r.recovery is None for r in by_fault["none"])
    assert all(r.verified and r.attempts == 2 for r in by_fault["lost_before"])
    assert all(r.effects == 2 and not r.verified for r in by_fault["lost_after"])
    assert all(r.effects == 2 and r.file_ok for r in by_fault["restart"])
    assert all(r.recovery is not None for r in by_fault["restart"])
    assert all(r.effects == 2 and r.attempts == 2 for r in by_fault["duplicate_request"])
    assert all(r.effects == 2 and not r.verified for r in by_fault["unknown_outcome"])
    taken_back = by_fault["lease_expiry"] + by_fault["withdrawn_authority"]
    assert all((r.effects, r.unauthorised, r.file_ok) == (1, 1, True) for r in taken_back)
    assert not any(r.verified for r in taken_back)


def test_an_unreachable_target_gives_up_and_loses_the_effect(tmp_path: Path) -> None:
    task = bench.Task("t", "agent-0", "none")
    (record,) = bench.run_baseline((task,), tmp_path / "b", target_factory=Down)
    assert (record.gave_up, record.effects, record.attempts) == (True, 0, bench.MAX_ATTEMPTS)
    assert record.recovery is None and not record.verified
    measures = bench.ModeResult("baseline", (record,)).measures()
    assert measures["lost_effects"] == measures["gave_up"] == 1
    assert measures["cpu_seconds_per_completed_task"] is None
    assert measures["mean_completed_latency_seconds"] is None
    assert measures["mean_attempt_latency_seconds"] == record.latency


def test_time_measures_count_completed_tasks_and_cost_counts_all_work() -> None:
    done = bench.TaskRecord("a", "none", latency=2.0, effects=1, file_ok=True, cpu_seconds=1.0)
    duplicate = bench.TaskRecord("b", "lost_after", latency=10.0, effects=2, file_ok=True)
    duplicate.cpu_seconds, duplicate.recovery = 3.0, 4.0
    measures = bench.ModeResult("baseline", (done, duplicate)).measures()
    assert measures["mean_completed_latency_seconds"] == 2.0
    assert measures["mean_attempt_latency_seconds"] == 6.0
    assert measures["cpu_seconds_per_completed_task"] == 4.0  # all work / one completed task
    assert measures["mean_recovery_seconds"] == 4.0
    by_fault = measures["by_fault"]
    assert by_fault["none"]["mean_completed_latency_seconds"] == 2.0
    assert by_fault["lost_after"]["mean_completed_latency_seconds"] is None
    assert by_fault["lost_after"]["cpu_seconds_per_completed_task"] is None
    unreported = bench.TaskRecord("c", "none", latency=1.0, effects=1, file_ok=True)
    assert bench.ModeResult("x", (unreported,)).measures()["cpu_seconds_per_completed_task"] is None
    assert bench.ModeResult("baseline").measures()["mean_attempt_latency_seconds"] is None


def test_a_run_is_repeatable_and_the_command_writes_its_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = bench.run(7, workdir=tmp_path / "one")
    second = bench.run(7)
    keys = (
        "tasks",
        "verified_success",
        "unauthorised_effects",
        "duplicate_effects",
        "lost_effects",
        "gave_up",
        "human_interventions",
        "verified_on_hub",
        "hub_disagrees_with_observers",
    )

    def counts(mode: dict[str, Any]) -> list[object]:
        per_fault = [[mode["by_fault"][f][k] for k in keys] for f in bench.FAULTS]
        return [*(mode[k] for k in keys), per_fault]

    assert [counts(m) for m in first["modes"]] == [counts(m) for m in second["modes"]]
    summary = (
        "verified_success",
        "unauthorised_effects",
        "duplicate_effects",
        "human_interventions",
    )
    assert [first["modes"][0][k] for k in summary] == [6, 6, 12, 0]
    assert [first["modes"][1][k] for k in summary] == [18, 0, 0, 3]
    hub = ("verified_on_hub", "hub_disagrees_with_observers")
    assert [first["modes"][0][k] for k in hub] == [0, 0]  # the baseline has no hub
    assert [first["modes"][1][k] for k in hub] == [18, 0]
    assert first["modes"][0]["cpu_seconds_per_completed_task"] > 0  # Git children counted
    versions = first["versions"]
    head = subprocess.run(
        ["git", "-C", str(_PATH.parent), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert (versions["core_commit"], versions["core_version"]) == (
        head,
        bench.synapse_channel.__version__,
    )
    assert versions["benchmark_sha256"] == hashlib.sha256(_PATH.read_bytes()).hexdigest()
    assert versions["child_cpu_reported"] is True and isinstance(
        versions["core_uncommitted_changes"], bool
    )
    out = tmp_path / "result.json"
    assert bench.main(["--seed", "7", "--results", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["seed"] == 7
    assert bench.main(["--seed", "3"]) == 0
    assert json.loads(capsys.readouterr().out)["seed"] == 3
    repository = bench.Repository(tmp_path / "repo")
    assert repository.digest("main", "missing") is None


def test_the_script_runs_from_its_real_entry_point(tmp_path: Path) -> None:
    out = tmp_path / "entry.json"
    result = subprocess.run(
        [sys.executable, str(_PATH), "--seed", "1", "--results", str(out)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    modes = json.loads(out.read_text(encoding="utf-8"))["modes"]
    assert [(m["mode"], m["tasks"]) for m in modes] == [("baseline", 24), ("core", 24)]


def test_core_inquires_instead_of_retrying_and_leaves_no_duplicate(tmp_path: Path) -> None:
    records = bench.run_core(bench.schedule(7), tmp_path / "core")
    by_fault = {fault: [r for r in records if r.fault == fault] for fault in bench.FAULTS}
    taken_back = ("lease_expiry", "withdrawn_authority")
    assert all(r.verified for r in records if r.fault not in taken_back)
    assert sum(r.unauthorised for r in records) == 0
    assert all(r.attempts == 1 and r.recovery is None for r in by_fault["none"])
    assert all(r.attempts == 2 and r.recovery is not None for r in by_fault["lost_before"])
    assert all(r.attempts == 1 and r.recovery is not None for r in by_fault["lost_after"])
    assert all(r.attempts == 1 and r.recovery is not None for r in by_fault["restart"])
    assert all(
        r.attempts == 2 and r.reason == "second delivery refused: duplicate_operation"
        for r in by_fault["duplicate_request"]
    )
    assert all(
        (r.human, r.attempts, r.recovery is not None) == (True, 1, True)
        and r.reason == "outcome unknown, recorded accepted; routed to an operator"
        for r in by_fault["unknown_outcome"]
    )
    # Taken away after preparation and before the effect: neither the commit nor the record.
    assert all(
        (r.gave_up, r.effects, r.file_ok, r.attempts, r.recovery is not None)
        == (True, 0, False, 0, True)
        and r.reason == "the claim check was refused; no effect made"
        for r in by_fault["lease_expiry"] + by_fault["withdrawn_authority"]
    )
    assert all(r.cpu_seconds is not None and r.cpu_seconds >= 0 for r in records)
    # Every declared operation has the worker's outcome and an independent verification
    # on the hub, and the hub's journal agrees with the observers task by task.
    assert all(r.outcome == "accepted" for r in records)
    assert all(
        r.verification == ("not_verified" if r.fault in taken_back else "verified") for r in records
    )
    assert all(r.verified_on_hub == r.verified for r in records)


def test_core_gives_up_on_an_unreachable_target_and_on_a_real_refused_declaration(
    tmp_path: Path,
) -> None:
    task = bench.Task("t", "agent-0", "none")
    (down,) = bench.run_core((task,), tmp_path / "down", target_factory=Down)
    assert (down.gave_up, down.effects, down.attempts, down.recovery) == (
        True,
        0,
        bench.MAX_ATTEMPTS,
        None,
    )
    assert down.reason == "the effect stayed absent after every attempt"
    assert (down.outcome, down.verification, down.verified_on_hub) == (
        "accepted",
        "not_verified",
        False,
    )
    # The same task delivered twice to its own agent: the hub refuses the second
    # declaration of the operation, and the agent makes no second effect.
    first, again = bench.run_core((task, task), tmp_path / "again")
    assert first.verified and (first.effects, first.attempts) == (1, 1)
    assert (again.gave_up, again.attempts) == (True, 0)
    assert again.reason == "declaration refused: duplicate_operation"
    assert (again.outcome, again.verification) == (
        None,
        None,
    )  # nothing declared, nothing to verify


def test_core_shows_the_duplicate_a_hub_would_let_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def always(self: object, task: object, epoch: int) -> tuple[str, str]:
        return "accepted", ""

    monkeypatch.setattr(bench._Session, "declare", always)
    task = bench.Task("t", "agent-0", "duplicate_request")
    (record,) = bench.run_core((task,), tmp_path / "twice")
    assert (record.effects, record.unauthorised, record.verified, record.reason) == (
        2,
        0,
        False,
        "",
    )
    # The hub never saw that declaration, so it refuses the outcome and the verification.
    assert record.outcome == "refused" and str(record.verification).startswith("refused: ")
    assert not record.verified_on_hub


async def test_the_session_and_hub_helpers_refuse_what_never_answers(tmp_path: Path) -> None:
    """Environment guards: a hub that never listens, a session that cannot connect.

    They are reachable from the public run only when the in-process hub cannot start,
    so they are exercised here directly against real sockets.
    """
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    never = bench._Session("BENCH/x", f"ws://127.0.0.1:{port}")
    with pytest.raises(RuntimeError, match="could not connect"):
        await never.open(timeout=0.2)
    await never.close()
    await bench._Session("BENCH/y", "ws://127.0.0.1:1").close()
    with pytest.raises(RuntimeError, match="nothing listens"):
        await bench._listening(port, rounds=2)
    async with bench._hub(tmp_path) as uri:
        session = await bench._Session("BENCH/z", uri).open()
        with pytest.raises(TimeoutError, match="no never frame"):
            await session.wait("never", rounds=1)
        task = bench.Task("t", "agent-z", "none")
        await session.claim(task)
        other = await bench._Session("BENCH/w", uri).open()
        with pytest.raises(RuntimeError, match="BENCH/w was refused t"):
            await other.claim(task)
        await other.close()
        await session.close()
