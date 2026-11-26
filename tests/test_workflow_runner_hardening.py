# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — every job starts under the runner egress monitor
"""Keep Harden-Runner, in egress audit mode, as the first step of every job.

The audit records each job's outbound connections, so a later allowlist can be
built from what the jobs really reach. It runs first so that it sees every later
step. A job whose runner is not a literal ``ubuntu-*`` label guards the step to
Linux, which keeps matrix, Windows and macOS jobs running unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
HARDEN_RUNNER = "step-security/harden-runner@1d88384fd173af6ecb012dfd0264a306b711241a"
LINUX_ONLY = "runner.os == 'Linux'"
# The only jobs whose runner is not a fixed ubuntu label. A new one must be named here,
# so the monitor is never skipped by a guard nobody decided on.
LINUX_GUARDED_JOBS = {
    ("ci.yml", "test"),
    ("ci.yml", "test-windows"),
    ("cross-os.yml", "macos-shard"),
    ("cross-os.yml", "cross-os-filesystem"),
    ("opencode-compatibility.yml", "cross-platform-acp"),
}


def _jobs() -> list[tuple[str, str, dict[str, Any]]]:
    """Return ``(workflow, job, definition)`` for every job that runs steps."""
    workflows = sorted([*WORKFLOW_DIR.glob("*.yml"), *WORKFLOW_DIR.glob("*.yaml")])
    assert workflows, "no workflow was found"
    found: list[tuple[str, str, dict[str, Any]]] = []
    for path in workflows:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        found.extend(
            (path.name, name, job) for name, job in document["jobs"].items() if "steps" in job
        )
    assert found, "no job with steps was found"
    return found


def _literal_ubuntu(job: dict[str, Any]) -> bool:
    """Return whether the job's runner is a fixed ``ubuntu-*`` label."""
    runner = job.get("runs-on")
    return isinstance(runner, str) and runner.startswith("ubuntu-") and "${{" not in runner


def test_every_job_starts_with_the_pinned_egress_audit() -> None:
    """The first step of every job is the pinned Harden-Runner, auditing egress."""
    wrong = [
        (workflow, name)
        for workflow, name, job in _jobs()
        if job["steps"][0].get("uses", "").split(" ")[0] != HARDEN_RUNNER
        or job["steps"][0].get("with") != {"egress-policy": "audit"}
    ]
    assert wrong == []


def test_only_jobs_off_a_fixed_ubuntu_runner_guard_the_monitor() -> None:
    """A fixed ubuntu job always runs the monitor; any other runner limits it to Linux."""
    mismatched = [
        (workflow, name)
        for workflow, name, job in _jobs()
        if job["steps"][0].get("if") != (None if _literal_ubuntu(job) else LINUX_ONLY)
    ]
    assert mismatched == []


def test_the_monitor_appears_once_per_job_at_one_pin() -> None:
    """No job repeats the monitor, and no workflow pins another revision of it."""
    for workflow, name, job in _jobs():
        uses = [str(step.get("uses", "")) for step in job["steps"]]
        monitors = [use for use in uses if use.startswith("step-security/harden-runner@")]
        assert monitors == [HARDEN_RUNNER], (workflow, name, monitors)


def test_the_linux_guard_is_on_exactly_the_named_jobs() -> None:
    """The guarded jobs are exactly the five named ones, each off a fixed ubuntu runner."""
    guarded = {
        (workflow, name)
        for workflow, name, job in _jobs()
        if job["steps"][0].get("if") == LINUX_ONLY
    }
    off_ubuntu = {(workflow, name) for workflow, name, job in _jobs() if not _literal_ubuntu(job)}
    assert guarded == LINUX_GUARDED_JOBS
    assert off_ubuntu == LINUX_GUARDED_JOBS
