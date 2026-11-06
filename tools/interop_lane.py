# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — run the official-client interop matrix and write its evidence
"""Run every client, transport and mode of the interop lane, then write the record.

Each row runs three times: as a real journey, which must pass, and as its two
negative controls, which must fail in their required place. The lane exits
non-zero when any of those outcomes is not the required one. The record holds
every run, controls included, so a reader can see that the controls failed.

Usage::

    python -m tools.interop_lane --out interop-results.json \
        [--mcp-ts] [--a2a-python CLIENT_VENV/bin/python] [--work DIR]

The commit and the run URL come from ``GITHUB_SHA`` and the GitHub Actions run
variables when present, otherwise from ``git rev-parse HEAD`` and an empty URL.
"""

from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
import tempfile
from collections.abc import Callable
from pathlib import Path

from tools import interop_a2a, interop_mcp_python, interop_mcp_ts
from tools.interop_journey import CONTROLS, expectation_met
from tools.interop_record import JourneyRecord, build_record, write_record

Runner = Callable[[str, Path], tuple[JourneyRecord, list[str]]]


def _mcp_runner(module: str, transport: str) -> Runner:
    """Return the runner of one MCP client module on one transport."""

    def run(control: str, work: Path) -> tuple[JourneyRecord, list[str]]:
        if module == "python":
            return interop_mcp_python.run_journey(transport, control, work)
        return interop_mcp_ts.run_journey(transport, control, work)

    return run


def _a2a_runner(client_python: Path) -> Runner:
    """Return the A2A row's runner for the client environment's interpreter."""

    def run(control: str, work: Path) -> tuple[JourneyRecord, list[str]]:
        return interop_a2a.run_journey(control, work, client_python=client_python)

    return run


def _rows(a2a_python: Path | None, *, mcp_ts: bool) -> list[tuple[str, str, Runner]]:
    """Return ``(label, first journal step, runner)`` for every row of the matrix."""
    modules = [("python", "mcp-python-sdk")] + ([("ts", "mcp-typescript-sdk")] if mcp_ts else [])
    rows: list[tuple[str, str, Runner]] = [
        (
            f"{client}/{transport}",
            interop_mcp_python.FIRST_JOURNAL_STEP,
            _mcp_runner(module, transport),
        )
        for module, client in modules
        for transport in ("stdio", "streamable-http")
    ]
    if a2a_python is not None:
        rows.append(("a2a-sdk/http-json", interop_a2a.FIRST_JOURNAL_STEP, _a2a_runner(a2a_python)))
    return rows


def run_lane(
    work: Path, *, a2a_python: Path | None, mcp_ts: bool
) -> tuple[list[JourneyRecord], list[str], bool]:
    """Run the matrix; return every record, every secret used, and whether all met."""
    records: list[JourneyRecord] = []
    secrets: list[str] = []
    all_met = True
    for label, journal_step, runner in _rows(a2a_python, mcp_ts=mcp_ts):
        for control in CONTROLS:
            directory = work / label.replace("/", "-") / control
            directory.mkdir(parents=True)
            record, used = runner(control, directory)
            met = expectation_met(record, control, journal_step)
            all_met = all_met and met
            records.append(record)
            secrets.extend(used)
            print(f"{label} {control}: failed_step={record.failed_step or '-'} required={met}")
    return records, secrets, all_met


def _commit() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha
    # A fixed git query.
    return subprocess.run(  # noqa: S603, S607  # nosec B603 B607
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _run_url() -> str:
    server, repository, run = (
        os.environ.get("GITHUB_SERVER_URL", ""),
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("GITHUB_RUN_ID", ""),
    )
    return f"{server}/{repository}/actions/runs/{run}" if server and repository and run else ""


def main(argv: list[str] | None = None) -> int:
    """Run the lane and write its record; exit 1 when any outcome is not the required one."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="New record file to write.")
    parser.add_argument(
        "--a2a-python", type=Path, default=None, help="Interpreter of the a2a-sdk environment."
    )
    parser.add_argument(
        "--mcp-ts", action="store_true", help="Also run the built MCP TypeScript SDK client."
    )
    parser.add_argument("--work", type=Path, default=None, help="Working directory for the run.")
    args = parser.parse_args(argv)
    work = args.work or Path(tempfile.mkdtemp(prefix="synapse-interop-"))
    records, secrets, all_met = run_lane(work, a2a_python=args.a2a_python, mcp_ts=args.mcp_ts)
    text = build_record(records, commit=_commit(), run_url=_run_url(), secrets=secrets)
    write_record(args.out, text)
    return 0 if all_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
