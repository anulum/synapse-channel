# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real external-surfaces benchmark CLI and measured evidence
"""The benchmark runs its real servers and, where tmux exists, a real pane."""

from __future__ import annotations

import json
import os
import runpy
import shutil
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

BENCHMARK = Path(__file__).resolve().parents[1] / "benchmarks/external_surfaces_benchmark.py"
_HTTP_KINDS = {
    "a2a": ("agent_card", "message_send", "host_refusal"),
    "dashboard": ("snapshot_json", "classic_page"),
}


def _cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(BENCHMARK), *arguments],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_http_surfaces(measured: dict[str, Any], requests: int) -> None:
    for surface, kinds in _HTTP_KINDS.items():
        for kind in kinds:
            summary = measured[surface][kind]
            assert summary["requests"] == summary["complete"] == requests, (surface, kind)
            assert summary["reset"] == 0, (surface, kind)
            assert 0 < summary["min_us"] <= summary["p50_us"] <= summary["p95_us"]
            assert summary["p95_us"] <= summary["max_us"]
    # Only the accepted messages reach the worker; the refused ones never do.
    assert measured["a2a"]["messages_received"] == requests


def test_cli_measures_both_http_edges_and_says_why_no_pane_was_measured(tmp_path: Path) -> None:
    output = tmp_path / "measured.json"
    absent = tmp_path / "no-such-tmux"

    process = _cli("--requests", "3", "--tmux", str(absent), "--results", str(output))

    assert process.returncode == 0, process.stderr
    measured = json.loads(output.read_text(encoding="utf-8"))
    assert measured == json.loads(process.stdout)
    assert measured["requests"] == 3
    _assert_http_surfaces(measured, 3)
    assert measured["pane"] == {"measured": False, "reason": f"{absent} is not installed"}
    assert measured["host"]["python"] and "shared host" in measured["host"]["isolation"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_programmatic_entry_point_wakes_a_real_pane_and_leaves_no_session(
    tmp_path: Path,
) -> None:
    namespace = runpy.run_path(str(BENCHMARK), run_name="benchmark_api")
    main: Callable[[list[str] | None], int] = namespace["main"]
    output = tmp_path / "entry-point.json"

    assert main(["--requests", "1", "--wakes", "2", "--results", str(output)]) == 0

    measured = json.loads(output.read_text(encoding="utf-8"))
    assert measured["host"]["pid"] == os.getpid()
    _assert_http_surfaces(measured, 1)
    pane = measured["pane"]
    assert pane["measured"] is True and pane["wakes"] == pane["injected"] == 2
    assert 0 < pane["min_us"] <= pane["p50_us"] <= pane["max_us"]
    sessions = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert "synapse-bench-" not in sessions.stdout


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_a_pane_that_never_becomes_idle_is_reported_as_not_injected(tmp_path: Path) -> None:
    """With an executable that does nothing in place of tmux, no wake is injected."""
    output = tmp_path / "idle-never.json"
    does_nothing = shutil.which("true")
    assert does_nothing is not None

    process = _cli(
        "--requests", "1", "--wakes", "1", "--tmux", does_nothing, "--results", str(output)
    )

    assert process.returncode == 0, process.stderr
    measured = json.loads(output.read_text(encoding="utf-8"))
    assert measured["pane"] == {"measured": True, "wakes": 1, "injected": 0}


def test_cli_refuses_zero_work_before_opening_the_result_path(tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist.json"

    process = _cli("--requests", "0", "--results", str(output))

    assert process.returncode == 2 and "count must be positive" in process.stderr
    assert not output.exists()


def test_a_reset_connection_is_counted_and_not_timed() -> None:
    """A peer that resets instead of answering yields a counted reset and no distribution."""
    namespace = runpy.run_path(str(BENCHMARK), run_name="benchmark_api")
    measured: Callable[[int, list[bytes], int], dict[str, Any]] = namespace["measure_requests"]
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)

    def reset_each() -> None:
        for _ in range(2):
            accepted, _address = listener.accept()
            accepted.recv(64)
            accepted.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            accepted.close()

    thread = threading.Thread(target=reset_each, daemon=True)
    thread.start()
    try:
        summary = measured(listener.getsockname()[1], [b"GET / HTTP/1.1\r\n\r\n"] * 2, 200)
    finally:
        thread.join(timeout=5.0)
        listener.close()

    assert summary == {"requests": 2, "complete": 0, "reset": 2}
