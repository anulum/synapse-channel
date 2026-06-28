# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — five-agent parallel-edit coordination benchmark
"""Measure a deterministic five-agent coding fleet over the real state model.

The benchmark exercises the same in-memory claim, conflict, release, and journal
replay logic used by the hub. It is a local functional regression benchmark, not
a production throughput claim and not a comparison against remote coding-agent
services. The scenario intentionally includes both disjoint edits and overlapping
file-scope attempts so the result records conflict rate, claim latency, release
cleanup, and replay/recovery evidence.

Run with ``python benchmarks/coding_fleet_benchmark.py``. Results are written to
``benchmarks/results/coding_fleet_benchmark.json`` by default.
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind, replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.state import SynapseState
from synapse_channel.core.state_models import GitContext

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "coding_fleet_benchmark.json"
SCENARIO_NAME = "five_agent_parallel_edit"


@dataclass(frozen=True)
class WorkAttempt:
    """A single claim attempt in the deterministic coding-fleet scenario.

    Attributes
    ----------
    agent : str
        Agent attempting the claim.
    task_id : str
        Task id claimed by the agent.
    paths : tuple[str, ...]
        Declared file scope for the task.
    branch : str
        Branch context attached to the claim.
    expected_granted : bool
        Whether the scenario expects the real state model to grant the claim.
    """

    agent: str
    task_id: str
    paths: tuple[str, ...]
    branch: str
    expected_granted: bool


@dataclass(frozen=True)
class FleetScenario:
    """A deterministic edit plan for a multi-agent coding fleet."""

    name: str
    attempts: tuple[WorkAttempt, ...]

    @property
    def agent_count(self) -> int:
        """Return the number of distinct agents in the scenario."""
        return len({attempt.agent for attempt in self.attempts})


def host_profile() -> dict[str, str]:
    """Return host metadata so committed benchmark results are attributable."""
    cpu = platform.processor()
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {
        "cpu": cpu or "unknown",
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def default_scenario() -> FleetScenario:
    """Return the committed five-agent parallel-edit scenario."""
    return FleetScenario(
        name=SCENARIO_NAME,
        attempts=(
            WorkAttempt(
                agent="planner",
                task_id="PLAN",
                paths=("docs/plan.md",),
                branch="bench/planner",
                expected_granted=True,
            ),
            WorkAttempt(
                agent="api-dev",
                task_id="API",
                paths=("src/app/api.py",),
                branch="bench/api",
                expected_granted=True,
            ),
            WorkAttempt(
                agent="test-dev",
                task_id="TEST",
                paths=("tests/test_api.py",),
                branch="bench/tests",
                expected_granted=True,
            ),
            WorkAttempt(
                agent="docs-dev",
                task_id="DOCS",
                paths=("docs/usage.md",),
                branch="bench/docs",
                expected_granted=True,
            ),
            WorkAttempt(
                agent="reviewer",
                task_id="REVIEW",
                paths=("src/app/api.py",),
                branch="bench/review",
                expected_granted=False,
            ),
            WorkAttempt(
                agent="api-dev",
                task_id="API-CLIENT",
                paths=("src/app/client.py",),
                branch="bench/api",
                expected_granted=True,
            ),
            WorkAttempt(
                agent="planner",
                task_id="PLAN-DOCS",
                paths=("docs/usage.md",),
                branch="bench/planner",
                expected_granted=False,
            ),
        ),
    )


def _mean(values: list[float]) -> float:
    """Return the arithmetic mean for a non-empty sequence, or zero."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _recorded_claims(state: SynapseState, task_ids: list[str]) -> list[dict[str, Any]]:
    """Return claim snapshots in the order claims were granted."""
    snapshots: list[dict[str, Any]] = []
    for task_id in task_ids:
        claim = state.claims.get(task_id)
        if claim is None:
            raise RuntimeError(f"claim {task_id} disappeared before release")
        snapshots.append(claim.as_dict())
    return snapshots


def _replay_evidence(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay claim and release events to prove restart recovery boundaries."""
    with tempfile.TemporaryDirectory() as directory:
        store = EventStore(Path(directory) / "events.db")
        for index, claim in enumerate(claims, start=1):
            store.append(EventKind.CLAIM, claim, ts=float(index), durable=False)
        pre_release_state = replay(store, now=100.0).state
        for index, claim in enumerate(claims, start=len(claims) + 1):
            store.append(
                EventKind.RELEASE,
                {"task_id": str(claim["task_id"])},
                ts=float(index),
                durable=False,
            )
        post_release_state = replay(store, now=100.0).state
        store.close()
    return {
        "events": len(claims) * 2,
        "replayed_claims": len(pre_release_state.claims),
        "replayed_conflicting_claims": _conflicting_claim_pairs(pre_release_state),
        "post_release_claims": len(post_release_state.claims),
    }


def _conflicting_claim_pairs(state: SynapseState) -> int:
    """Count conflicting pairs among replayed claims.

    The count is expected to remain zero because refused claims are not recorded.
    """
    claims = list(state.claims.values())
    conflicts = 0
    for left_index, left in enumerate(claims):
        for right in claims[left_index + 1 :]:
            if left.worktree != right.worktree:
                continue
            if not left.paths or not right.paths or set(left.paths) & set(right.paths):
                conflicts += 1
    return conflicts


def profile(scenario: FleetScenario | None = None) -> dict[str, Any]:
    """Run the coding-fleet scenario and return benchmark evidence.

    Parameters
    ----------
    scenario : FleetScenario or None, optional
        Scenario to run. ``None`` uses :func:`default_scenario`.

    Returns
    -------
    dict[str, Any]
        JSON-serialisable benchmark summary with conflict, latency, release, and
        replay/recovery evidence.
    """
    active_scenario = default_scenario() if scenario is None else scenario
    state = SynapseState(default_ttl_seconds=300.0)
    latencies: list[float] = []
    granted: list[str] = []
    refused_reasons: list[str] = []
    attempt_rows: list[dict[str, Any]] = []

    for index, attempt in enumerate(active_scenario.attempts, start=1):
        start = time.perf_counter()
        ok, message = state.claim(
            attempt.agent,
            attempt.task_id,
            note="coding fleet benchmark",
            now=float(index),
            paths=attempt.paths,
            git=GitContext(branch=attempt.branch, base="main", auto_release_on="merge"),
        )
        elapsed = time.perf_counter() - start
        latencies.append(elapsed)
        if ok:
            granted.append(attempt.task_id)
        else:
            refused_reasons.append(message)
        if ok != attempt.expected_granted:
            raise RuntimeError(f"scenario expectation failed for {attempt.task_id}: {message}")
        attempt_rows.append(
            {
                "agent": attempt.agent,
                "task_id": attempt.task_id,
                "paths": list(attempt.paths),
                "branch": attempt.branch,
                "granted": ok,
                "message": message,
                "latency_microseconds": round(elapsed * 1e6, 3),
            }
        )

    claim_snapshots = _recorded_claims(state, granted)
    released = 0
    for index, task_id in enumerate(granted, start=len(active_scenario.attempts) + 1):
        owner = str(
            next(claim["owner"] for claim in claim_snapshots if claim["task_id"] == task_id)
        )
        ok, message = state.release(owner, task_id, now=float(index))
        if not ok:
            raise RuntimeError(message)
        released += 1

    refused = len(refused_reasons)
    attempts = len(active_scenario.attempts)
    return {
        "scenario": active_scenario.name,
        "host": host_profile(),
        "agents": active_scenario.agent_count,
        "attempts": attempts,
        "granted": len(granted),
        "refused": refused,
        "conflict_rate": refused / attempts if attempts else 0.0,
        "claim_latency": {
            "mean_microseconds": round(_mean(latencies) * 1e6, 3),
            "max_microseconds": round(max(latencies, default=0.0) * 1e6, 3),
        },
        "release_recovery": {
            "released": released,
            "remaining_claims": len(state.claims),
        },
        "replay_recovery": _replay_evidence(claim_snapshots),
        "attempt_details": attempt_rows,
        "refusal_reasons": refused_reasons,
        "evidence_class": "local_functional_benchmark",
        "limitations": [
            "single-process in-memory state benchmark",
            "does not measure model latency or editor integration latency",
            "does not compare against external coding-agent services",
        ],
    }


def run(results_path: Path | None = DEFAULT_RESULTS) -> dict[str, Any]:
    """Run the benchmark and optionally write the JSON result."""
    summary = profile()
    if results_path is not None:
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", "utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the benchmark, and print a short summary."""
    parser = argparse.ArgumentParser(description="Run the five-agent coding fleet benchmark.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    args = parser.parse_args(argv)

    summary = run(args.results)
    print("five-agent coding fleet benchmark")
    print(f"agents: {summary['agents']}")
    print(f"attempts: {summary['attempts']}")
    print(f"granted: {summary['granted']}")
    print(f"refused: {summary['refused']}")
    print(f"conflict rate: {summary['conflict_rate']:.2%}")
    print(f"mean claim latency: {summary['claim_latency']['mean_microseconds']} us")
    print(f"results written to {args.results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
