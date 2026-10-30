# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified completion through worker-session and verify-result
"""Measure the gap between a worker outcome report and ``verify-result file``.

Each task is a real ``synapse worker-session`` launch. The worker decides the
fault. The benchmark reads ``confirmed_done`` and ``unknown`` from the outcome
file and ``verified`` / ``not_verified`` from ``verify-result``. It does not
write the effect or assign the boundary state.

The wake sidecar is off (``--no-arm``). Arming needs a hub and is not part of
this measurement. This is not the reproducible public fault-and-effect
benchmark (baseline, Core and Core with Fleet): no network fault, restart,
duplicate, or withdrawn authority, and no publication claim.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results" / "verified_completion_benchmark.json"
DEFAULT_TASKS = 4
REQUESTED = b"requested completion\n"
MODES = ("baseline", "wrong_content", "absent", "crash_after_effect")
_WORKER = """\
import os
import signal
import sys
import time
from pathlib import Path

mode, target = sys.argv[1], Path(sys.argv[2])
requested = b"requested completion\\n"
if mode == "baseline":
    target.write_bytes(requested)
elif mode == "wrong_content":
    target.write_bytes(b"wrong content\\n")
elif mode == "absent":
    pass
elif mode == "crash_after_effect":
    target.write_bytes(requested)
    os.kill(os.getppid(), signal.SIGKILL)
    time.sleep(30)
else:
    raise SystemExit(2)
"""


def _label(value: str, fallback: str) -> str:
    """Return ``value`` when the platform reported one, otherwise ``fallback``.

    Parameters
    ----------
    value : str
        Text from the platform module. An empty string is not a CPU name.
    fallback : str
        Label stored when the platform reports nothing.
    """
    if value:
        return value
    return fallback


def host_profile() -> dict[str, str]:
    """Return host metadata stored beside the counts."""
    return {
        "cpu": _label(platform.processor(), "unknown"),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _launch_env() -> dict[str, str]:
    """Return an environment that imports this worktree's package."""
    env = dict(os.environ)
    src = str(REPO_ROOT / "src")
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not previous else src + os.pathsep + previous
    env["SYNAPSE_NO_UPDATE_CHECK"] = "1"
    return env


def _cli(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run ``synapse_channel.cli.main`` and capture its text."""
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from synapse_channel.cli import main\n"
            "raise SystemExit(main(sys.argv[1:]))\n",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _sha256() -> str:
    """Return the digest ``verify-result`` must see for a correct file."""
    import hashlib

    return hashlib.sha256(REQUESTED).hexdigest()


def _reap_session(pid: int) -> None:
    """Stop a worker that outlived its session leader.

    Parameters
    ----------
    pid : int
        Process-group id of the ``worker-session`` launch. A group that has
        already exited is a finished reap, not a failure.
    """
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _wait_and_reap(process: subprocess.Popen[bytes], timeout: float) -> None:
    """Wait for the session leader and always reap the group.

    Parameters
    ----------
    process : subprocess.Popen
        The ``worker-session`` process, started in its own session.
    timeout : float
        Seconds to wait for that process.

    Raises
    ------
    subprocess.TimeoutExpired
        If the leader is still running after ``timeout``. The group is reaped
        before the exception propagates.
    """
    try:
        process.wait(timeout=timeout)
    finally:
        _reap_session(process.pid)


def run_task(root: Path, mode: str, index: int) -> dict[str, str]:
    """Launch one worker and return its reported state and verification result.

    Parameters
    ----------
    root : pathlib.Path
        Private directory for the effect and the outcome file.
    mode : str
        Worker fault: baseline, wrong content, absent, or crash after write.
    index : int
        Task number inside the cohort.

    Returns
    -------
    dict
        ``state`` from the outcome report and ``result`` from verify-result.

    Raises
    ------
    RuntimeError
        If the shipped commands do not return a readable report.
    """
    env = _launch_env()
    target = root / f"{mode}-{index}.txt"
    outcome = root / f"{mode}-{index}.json"
    worker = root / "worker.py"
    if not worker.exists():
        worker.write_text(_WORKER, encoding="utf-8")
    command = [
        "worker-session",
        "--identity",
        "SYNAPSE-CHANNEL/verified-completion",
        "--no-arm",
        "--terminal-tmux",
        "off",
        "--outcome-file",
        str(outcome),
        "--",
        sys.executable,
        str(worker),
        mode,
        str(target),
    ]
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys\n"
            "from synapse_channel.cli import main\n"
            "raise SystemExit(main(sys.argv[1:]))\n",
            *command,
        ],
        env=env,
        start_new_session=True,
    )
    _wait_and_reap(process, 30)
    report = _cli(["worker-session", "--read-outcome", str(outcome)], env)
    verified = _cli(
        [
            "verify-result",
            "file",
            "--outcome",
            str(outcome),
            "--path",
            str(target),
            "--sha256",
            _sha256(),
        ],
        env,
    )
    if report.returncode != 0:
        raise RuntimeError(f"worker report was not readable: {report.stderr.strip()}")
    outcome_document = json.loads(report.stdout)
    verification = json.loads(verified.stdout)
    return {"state": str(outcome_document["state"]), "result": str(verification["result"])}


def cohort(root: Path, mode: str, tasks: int) -> dict[str, int]:
    """Count one fault across ``tasks`` real worker sessions.

    Parameters
    ----------
    root : pathlib.Path
        Directory for this cohort.
    mode : str
        Fault the worker performs.
    tasks : int
        Positive task count.

    Returns
    -------
    dict
        Reported ``confirmed_done`` and ``unknown``, plus verified counts.
    """
    if tasks < 1:
        raise ValueError("a cohort needs at least one task")
    if mode not in MODES:
        raise ValueError("the fault is not one of the four worker modes")
    root.mkdir(parents=True, exist_ok=True)
    counts = {"tasks": tasks, "confirmed_done": 0, "unknown": 0, "verified": 0, "not_verified": 0}
    for index in range(tasks):
        observed = run_task(root, mode, index)
        if observed["state"] == "confirmed_done":
            counts["confirmed_done"] += 1
        if observed["state"] == "unknown":
            counts["unknown"] += 1
        if observed["result"] == "verified":
            counts["verified"] += 1
        elif observed["result"] == "not_verified":
            counts["not_verified"] += 1
        else:
            raise RuntimeError(f"verify-result did not decide: {observed['result']}")
    return counts


def run(results: Path | None = None, *, tasks: int = DEFAULT_TASKS) -> dict[str, object]:
    """Measure four cohorts and optionally write the JSON record.

    Parameters
    ----------
    results : pathlib.Path or None
        Destination. ``None`` only returns the record.
    tasks : int
        Tasks in each cohort.

    Returns
    -------
    dict
        Per-cohort reported and verified counts.
    """
    if tasks < 1:
        raise ValueError("the benchmark needs at least one task")
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="synapse-verified-completion-") as temporary:
        base = Path(temporary)
        summary: dict[str, object] = {
            "schema": "synapse.verified-completion-benchmark.v2",
            "tasks_per_cohort": tasks,
            "host": host_profile(),
            "sidecar": "off",
            "not_measured": [
                "network faults",
                "restart",
                "duplicate delivery",
                "withdrawn authority",
                (
                    "the reproducible public fault-and-effect benchmark "
                    "(baseline, Core and Core with Fleet)"
                ),
            ],
            "baseline": cohort(base / "baseline", "baseline", tasks),
            "wrong_content": cohort(base / "wrong", "wrong_content", tasks),
            "absent": cohort(base / "absent", "absent", tasks),
            "crash_after_effect": cohort(base / "crash", "crash_after_effect", tasks),
        }
    summary["seconds"] = time.perf_counter() - started
    if results is not None:
        results.parent.mkdir(parents=True, exist_ok=True)
        results.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the benchmark from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--tasks", type=int, default=DEFAULT_TASKS)
    args = parser.parse_args(argv)
    record = run(args.results, tasks=args.tasks)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
