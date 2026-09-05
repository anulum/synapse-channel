# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaged terminal monitor CLI tests
"""Exercise command routing and real explicit-PID JSON output."""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest

from _platform_caps import PROC_AVAILABLE, requires_proc
from synapse_channel import cli
from synapse_channel.cli_pid_monitor import format_runtime, render_host_observation
from synapse_channel.dashboard import start_dashboard_server


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00:00"), (59.9, "00:00:59"), (3661, "01:01:01"), (90061.5, "1d 01:01:01")],
)
def test_runtime_format_is_whole_second_clock(seconds: float, expected: str) -> None:
    assert format_runtime(seconds) == expected


@pytest.mark.parametrize("seconds", [-1, float("nan"), float("inf"), True, "3"])
def test_runtime_format_refuses_invalid_durations(seconds: object) -> None:
    with pytest.raises(ValueError, match="runtime"):
        format_runtime(cast(float, seconds))


def test_render_reports_runtime_only_from_consistent_numbers() -> None:
    row: dict[str, object] = {"pid": 7, "started_at": 100.0, "started_at_status": "observed"}
    rendered = render_host_observation({"observed_at": 3700.5, "rows": [row]})
    assert "  runtime: 01:00:00 (observed)" in rendered
    unavailable = {**row, "started_at": None, "started_at_status": "unavailable"}
    broken_documents: list[dict[str, object]] = [
        {"observed_at": 3700.5, "rows": [unavailable]},
        {"observed_at": 3700.5, "rows": [{**row, "started_at": 3701.0}]},
        {"observed_at": 3700.5, "rows": [{**row, "started_at": True}]},
        {"observed_at": float("inf"), "rows": [row]},
        {"rows": [row]},
    ]
    for broken in broken_documents:
        assert "  runtime: unknown (" in render_host_observation(broken)


@requires_proc
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap is not installed")
@pytest.mark.parametrize("hidden", ["/proc", "/proc/sys/kernel/random", "/proc/stat"])
def test_packaged_monitor_reports_missing_kernel_metadata(hidden: str, tmp_path: Path) -> None:
    mask = ["--ro-bind", "/dev/null", hidden] if hidden == "/proc/stat" else ["--tmpfs", hidden]
    argv = [
        "bwrap",
        "--ro-bind",
        "/",
        "/",
        *mask,
        "--",
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "pid-monitor",
        "--pid",
        str(os.getpid()),
        "--tmux-socket",
        str(tmp_path / "absent"),
        "--json",
    ]
    references = []
    for _ in range(2):
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        assert document["tmux_status"] == "unavailable"
        assert document["coordination_status"] == "unavailable"
        references.append(document["host_ref"])
        if hidden == "/proc":
            assert document["process_status"] == "unavailable"
            assert document["rows"] == []
            continue
        assert len(document["rows"]) == 1
        row = document["rows"][0]
        assert row["pid"] == os.getpid()
        assert row["cwd"] is None and row["context_id"] is None
        if hidden == "/proc/stat":
            assert document["process_status"] == "complete"
            assert row["started_at"] is None and row["started_at_status"] == "unavailable"
        else:
            assert document["process_status"] == "unavailable"
            assert row["started_at_status"] == "observed"
    if hidden == "/proc/stat":
        assert references[0] == references[1]
    else:
        assert references[0] != references[1]


def test_packaged_json_and_text(tmp_path: Path) -> None:
    argv = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "pid-monitor",
        "--pid",
        str(os.getpid()),
        "--tmux-socket",
        str(tmp_path / "absent"),
    ]
    result = subprocess.run(argv + ["--json"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    document = json.loads(result.stdout)
    if PROC_AVAILABLE:
        assert document["process_status"] == "complete"
        assert document["rows"][0]["pid"] == os.getpid()
        assert document["rows"][0]["cwd"] is None
    else:
        assert document["process_status"] == "unavailable"
        assert document["rows"] == []
    result = subprocess.run(argv + ["--paths"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "OS STATE" in result.stdout
    if PROC_AVAILABLE:
        assert os.getcwd() in result.stdout
        assert re.search(r"^  runtime: (\d+d )?\d\d:\d\d:\d\d \(observed\)$", result.stdout, re.M)
    else:
        assert "process scan: unavailable" in result.stdout
        assert os.getcwd() not in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--pid", "-1"],
        ["--samples", "0"],
        ["--samples", "3601"],
        ["--samples", "nan"],
        ["--dashboard-port", "8765"],
        ["--dashboard-port", "0"],
        ["--dashboard-port", "65536"],
        ["--dashboard-port", "nan"],
        ["--dashboard-port", "8765", "--paths"],
        ["--dashboard-port", "8765", "--pid", "1"],
        ["--dashboard-port", "8765", "--context-root", "/nonexistent"],
    ],
)
def test_invalid_cli_inputs_are_reported_without_traceback(args: list[str]) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "pid-monitor", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


@requires_proc
def test_custom_context_root_requires_local_consent(tmp_path: Path) -> None:
    context = "12345678-1234-1234-1234-123456789abc"
    rollout = tmp_path / f"rollout-cli-{context}.jsonl"
    rollout.write_text("PRIVATE-BODY-NOT-FOR-OUTPUT")
    program = (
        'import ctypes,sys; assert ctypes.CDLL(None).prctl(15,b"codex",0,0,0)==0; '
        'stream=open(sys.argv[1]); print("ready",flush=True); sys.stdin.read()'
    )
    with subprocess.Popen(
        [sys.executable, "-c", program, str(rollout)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            argv = [
                sys.executable,
                "-m",
                "synapse_channel.cli",
                "pid-monitor",
                "--pid",
                str(child.pid),
                "--context-root",
                str(tmp_path),
                "--tmux-socket",
                str(tmp_path / "absent"),
                "--json",
            ]
            for consent in ([], ["--context"]):
                result = subprocess.run(argv + consent, capture_output=True, text=True, timeout=5)
                assert result.returncode == 0, result.stderr
                row = json.loads(result.stdout)["rows"][0]
                assert row["context_id"] == (context if consent else None)
                assert row["context_status"] == ("observed" if consent else "not_requested")
                assert "PRIVATE-BODY-NOT-FOR-OUTPUT" not in result.stdout
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


@requires_proc
def test_real_process_metadata_cannot_emit_terminal_control_sequences(tmp_path: Path) -> None:
    directory = tmp_path / "line\n\x1b[31m"
    directory.mkdir()
    program = (
        "import ctypes, sys; "
        'assert ctypes.CDLL(None).prctl(15, b"name\\x1b[31m", 0, 0, 0) == 0; '
        'print("ready", flush=True); sys.stdin.read()'
    )
    with subprocess.Popen(
        [sys.executable, "-c", program],
        cwd=directory,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None and child.stdout.readline() == "ready\n"
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "synapse_channel.cli",
                    "pid-monitor",
                    "--pid",
                    str(child.pid),
                    "--paths",
                    "--tmux-socket",
                    str(tmp_path / "absent"),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == 0, result.stderr
            assert "\\u001b[31m" in result.stdout and "\\u000a" in result.stdout
            assert "\x1b" not in result.stdout
            assert "name\\u001b[31m" in result.stdout
        finally:
            assert child.stdin is not None
            child.stdin.close()
            child.wait(timeout=5)


def test_render_refuses_malformed_row_containers() -> None:
    with pytest.raises(ValueError, match="invalid host rows"):
        render_host_observation({"rows": {}})
    with pytest.raises(ValueError, match="invalid host rows"):
        render_host_observation({"rows": [{}] * 257})
    with pytest.raises(ValueError, match="invalid host row"):
        render_host_observation({"rows": [None]})


@pytest.mark.parametrize(
    "args",
    [
        ["--samples", "0"],
        ["--samples", "3601"],
        ["--dashboard-port", "0"],
        ["--dashboard-port", "65536"],
    ],
)
def test_in_process_argument_validators_reject_out_of_range(args: list[str]) -> None:
    with pytest.raises(SystemExit) as failure:
        cli.main(["pid-monitor", *args])
    assert failure.value.code == 2


@requires_proc
def test_in_process_local_monitor_prints_json_and_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["pid-monitor", "--pid", str(os.getpid()), "--tmux-socket", str(tmp_path / "absent")]
    assert cli.main([*argv, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["version"] == 1 and document["rows"][0]["pid"] == os.getpid()
    assert document["rows"][0]["started_at_status"] == "observed"
    assert cli.main([*argv, "--paths", "--samples", "2"]) == 0
    text = capsys.readouterr().out
    assert text.count("OS STATE") == 2 and os.getcwd() in text
    assert re.search(r"^  runtime: (\d+d )?\d\d:\d\d:\d\d \(observed\)$", text, re.M)
    assert cli.main(["pid-monitor", "--dashboard-port", "8765"]) == 2
    assert "requires --token-file" not in capsys.readouterr().out.replace("ValueError", "")


@requires_proc
def test_in_process_connected_monitor_reads_the_shared_loopback_feed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = "disposable-connected-monitor-token"
    token_file = tmp_path / "dashboard.token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    grants = tmp_path / "grants.json"
    grants.write_text(json.dumps({"version": 1, "observers": {}}))
    grants.chmod(0o600)
    server = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="connected-monitor-test",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=2,
        allow_non_loopback=False,
        dashboard_token=token,
        host_sessions_access_file=grants,
        host_session_pids=(os.getpid(),),
        host_session_tmux_socket=str(tmp_path / "absent"),
    )
    try:
        port = str(urlsplit(server.url("/")).port)
        connected = ["pid-monitor", "--dashboard-port", port, "--token-file", str(token_file)]
        assert cli.main(connected) == 2
        assert "observation unavailable" in capsys.readouterr().out
        grants.write_text(
            json.dumps(
                {"version": 1, "observers": {"compatibility": {"paths": True, "context": False}}}
            )
        )
        assert cli.main([*connected, "--json"]) == 0
        document = json.loads(capsys.readouterr().out)
        assert document["rows"][0]["pid"] == os.getpid()
        assert document["rows"][0]["cwd"] == os.getcwd()
        assert cli.main(connected) == 0
        text = capsys.readouterr().out
        assert "OS STATE" in text and os.getcwd() in text and "  runtime: " in text
        assert cli.main(["pid-monitor", "--dashboard-port", port]) == 2
        assert "observation unavailable" in capsys.readouterr().out
    finally:
        server.close()
    assert cli.main(connected) == 2
    assert "observation unavailable" in capsys.readouterr().out


def test_in_process_connected_mode_rejects_local_scope_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["pid-monitor", "--dashboard-port", "8765", "--paths"]) == 2
    assert "ValueError; observation unavailable" in capsys.readouterr().out


def test_in_process_connected_mode_refuses_an_incompatible_feed_version(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "dashboard.token"
    token_file.write_text("disposable-incompatible-feed-token")
    token_file.chmod(0o600)

    class IncompatibleFeed(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"version": 2, "rows": []}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), IncompatibleFeed)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = str(server.server_address[1])
        result = cli.main(
            ["pid-monitor", "--dashboard-port", port, "--token-file", str(token_file), "--json"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert result == 2
    assert "ValueError; observation unavailable" in capsys.readouterr().out


@requires_proc
def test_in_process_interrupt_between_samples_exits_130(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    interrupt = threading.Timer(0.3, os.kill, args=(os.getpid(), signal.SIGINT))
    interrupt.start()
    try:
        result = cli.main(
            [
                "pid-monitor",
                "--pid",
                str(os.getpid()),
                "--tmux-socket",
                str(tmp_path / "absent"),
                "--samples",
                "2",
                "--json",
            ]
        )
    finally:
        interrupt.cancel()
    assert result == 130
    printed = capsys.readouterr().out.strip().splitlines()
    assert len(printed) == 1 and json.loads(printed[0])["version"] == 1
