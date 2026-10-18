# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native worker process and inquiry outcomes
"""Exercise actual CLI/provider processes, filesystem failures and interrupted waits."""

from __future__ import annotations

import _thread
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from synapse_channel.worker_outcome import WorkerExit, read_worker_outcome
from synapse_channel.worker_session import run_worker_session


def private_environment(root: Path) -> dict[str, str]:
    """Keep every launcher record, identity and optional runtime in this fixture."""
    env = dict(os.environ)
    home = root / "home"
    home.mkdir(mode=0o700)
    for name, value in {
        "HOME": home,
        "USERPROFILE": home,
        "XDG_CONFIG_HOME": home / "config",
        "XDG_DATA_HOME": home / "data",
        "XDG_STATE_HOME": home / "state",
        "XDG_RUNTIME_DIR": home / "runtime",
    }.items():
        env[name] = str(value)
    env.pop("TMUX", None)
    return env


def launch_arguments(report: Path, program: str, *arguments: str) -> list[str]:
    """Invoke the actual registered launcher without a sidecar or vendor model."""
    return [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "worker-session",
        "--identity",
        "TEST/worker",
        "--no-arm",
        "--terminal-tmux",
        "off",
        "--outcome-file",
        str(report),
        "--",
        sys.executable,
        "-c",
        program,
        *arguments,
    ]


def inquire(report: Path, env: dict[str, str]) -> dict[str, object]:
    """Run the read-only registered CLI mode without an identity or provider command."""
    query = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "worker-session",
            "--read-outcome",
            str(report),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert query.returncode == 0, query.stderr + query.stdout
    result = json.loads(query.stdout)
    assert isinstance(result, dict)
    assert result["schema"] == "synapse.execution-outcome.v1"
    assert result["boundary"] == "worker_process"
    assert not result["automatic_retry"] and not result["effects_undone"]
    return result


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "unsupported"},
        {"boundary": "mcp_exchange"},
        {"evidence_class": "independent_attestation"},
        {"operation_id": "UNTRUSTED_REPORT_CONTENT\n"},
        {"observation": 7},
        {"state": "UNTRUSTED_REPORT_CONTENT"},
        {"external_effect": 7},
        {"stop_requested": 1},
        {"stop_confirmed": True},
        {"returncode": True},
        {"returncode": 2**64},
        {"returncode": 1},
        {"state": "confirmed_failed", "returncode": 0},
        {"recorded_at": float("nan")},
        {"launcher": {"pid": True, "boot": "", "started": ""}},
        {"launcher": []},
        {"launcher": {"pid": 1, "boot": 7, "started": ""}},
        {"launcher": {"pid": 1, "boot": "invalid", "started": "1"}},
        {"launcher": {"pid": 1, "boot": "", "started": "invalid"}},
    ],
)
def test_real_cli_refuses_malformed_report_without_echoing_contents(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    """Queries validate owner-local files without granting authority to edited fields."""
    env = private_environment(tmp_path)
    report = tmp_path / "private" / "outcome.json"
    launch = subprocess.run(
        launch_arguments(report, "pass"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert launch.returncode == 0, launch.stderr + launch.stdout
    document = json.loads(report.read_bytes())
    document.update(changes)
    report.write_text(json.dumps(document))
    before = report.read_bytes()
    query = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.cli",
            "worker-session",
            "--read-outcome",
            str(report),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert query.returncode == 2
    assert query.stdout == "worker-session: outcome report unavailable (ValueError)\n"
    assert not query.stderr
    assert report.read_bytes() == before


@pytest.mark.parametrize("exitcode", [0, 7, 255])
def test_real_cli_process_exit_does_not_verify_external_effects(
    tmp_path: Path, exitcode: int
) -> None:
    """The native program writes an effect even when its process subsequently fails."""
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    program = (
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('effect'); "
        "sys.exit(int(sys.argv[2]))"
    )
    run = subprocess.run(
        launch_arguments(report, program, str(effect), str(exitcode)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert run.returncode == exitcode, run.stderr + run.stdout
    result = inquire(report, env)
    assert result["state"] == ("confirmed_done" if exitcode == 0 else "confirmed_failed")
    assert result["returncode"] == exitcode
    assert result["external_effect"] == "unknown"
    assert result["external_recovery"] == "inquire_or_operator"
    assert not result["stop_requested"] and not result["stop_confirmed"]
    assert effect.read_text() == "effect"


@pytest.mark.parametrize("raw", [b"{UNTRUSTED_REPORT_CONTENT", b"[]"])
def test_public_reader_refuses_truncated_or_nonobject_json(tmp_path: Path, raw: bytes) -> None:
    """Actual owner-local files cannot bypass the report document contract."""
    report = tmp_path / "outcome.json"
    with os.fdopen(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)
    with pytest.raises(ValueError, match="invalid worker outcome document"):
        read_worker_outcome(report)
    assert report.read_bytes() == raw


def test_actual_tmux_executable_refusal_preserves_unknown_report(tmp_path: Path) -> None:
    """An OS launch error in the tmux boundary never confirms provider completion."""
    report = tmp_path / "private" / "outcome.json"
    with pytest.raises(FileNotFoundError):
        run_worker_session(
            identity="TEST/worker",
            command=["codex", "--version"],
            terminal_tmux="on",
            tmux_bin=str(tmp_path / "missing-tmux"),
            arm=False,
            environ={},
            cwd=tmp_path,
            outcome_file=report,
        )
    result = read_worker_outcome(report)
    assert result["state"] == result["external_effect"] == "unknown"
    assert result["observation"] == "tmux_exchange_unconfirmed"
    assert not result["stop_requested"] and not result["stop_confirmed"]


def test_provider_changed_report_is_preserved_and_never_relaunched(tmp_path: Path) -> None:
    """A real provider changes its report before exit; final publication refuses overwrite."""
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    program = (
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('UNTRUSTED_REPLACEMENT'); "
        "pathlib.Path(sys.argv[2]).write_text('one effect')"
    )
    launched = subprocess.run(
        launch_arguments(report, program, str(report), str(effect)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert launched.returncode == 2
    assert "record changed; inquire before continuing" in launched.stdout
    assert "UNTRUSTED_REPLACEMENT" not in launched.stdout + launched.stderr
    assert report.read_text() == "UNTRUSTED_REPLACEMENT"
    assert effect.read_text() == "one effect"


@pytest.mark.parametrize("mode", ["query_launch", "query_output", "empty_identity"])
def test_actual_cli_refuses_ambiguous_launch_and_query_modes(tmp_path: Path, mode: str) -> None:
    """A query or empty identity cannot accidentally execute the supplied provider."""
    env = private_environment(tmp_path)
    report, effect = tmp_path / "report.json", tmp_path / "unexpected-effect"
    command = [
        "--",
        sys.executable,
        "-c",
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('must not run')",
        str(effect),
    ]
    options = {
        "query_launch": ["--read-outcome", str(report), *command],
        "query_output": ["--read-outcome", str(report), "--outcome-file", str(report)],
        "empty_identity": ["--identity", "", "--no-arm", "--terminal-tmux", "off", *command],
    }[mode]
    result = subprocess.run(
        [sys.executable, "-m", "synapse_channel.cli", "worker-session", *options],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert not effect.exists() and not report.exists()
    expected = (
        "worker-session requires --identity for a provider launch\n"
        if mode == "empty_identity"
        else "worker-session: report inquiry cannot include a launch or output file\n"
    )
    assert result.stdout == expected and not result.stderr


def test_read_only_inquiry_observes_real_inflight_launcher_then_exit(tmp_path: Path) -> None:
    """A separate actual CLI reader checks the native launcher's birth identity."""
    env = private_environment(tmp_path)
    report, ready = tmp_path / "private" / "outcome.json", tmp_path / "ready"
    program = (
        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ready'); sys.stdin.readline()"
    )
    with subprocess.Popen(
        launch_arguments(report, program, str(ready)),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        try:
            deadline = time.monotonic() + 4
            while not ready.exists():
                assert child.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            inflight = inquire(report, env)
            assert inflight["state"] == "started"
            stdout, stderr = child.communicate(input=b"finish\n", timeout=5)
            assert child.returncode == 0, stdout + stderr
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
    ended = inquire(report, env)
    assert ended["state"] == "confirmed_done"
    assert ended["operation_id"] == inflight["operation_id"]


def test_existing_report_refuses_before_actual_provider_launch(tmp_path: Path) -> None:
    """An operator's existing file survives and the requested process never runs."""
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    report.parent.mkdir(mode=0o700)
    report.write_bytes(b"owner original bytes")
    run = subprocess.run(
        launch_arguments(
            report, "import pathlib,sys; pathlib.Path(sys.argv[1]).touch()", str(effect)
        ),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert run.returncode == 2
    assert report.read_bytes() == b"owner original bytes"
    assert not effect.exists()


def test_actual_launch_refusal_remains_not_started_after_launcher_exits(tmp_path: Path) -> None:
    """A native missing-executable error cannot be reported as a started provider."""
    env = private_environment(tmp_path)
    report = tmp_path / "private" / "outcome.json"
    argv = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "worker-session",
        "--identity",
        "TEST/worker",
        "--no-arm",
        "--terminal-tmux",
        "off",
        "--outcome-file",
        str(report),
        "--",
        str(tmp_path / "no-such-program"),
    ]
    run = subprocess.run(argv, env=env, capture_output=True, text=True, check=False, timeout=5)
    assert run.returncode == 2
    result = inquire(report, env)
    assert result["state"] == result["external_effect"] == "not_started"
    assert result["observation"] == "provider_launch_refused"


def test_real_publication_failure_recovery_is_unknown_without_relaunch(tmp_path: Path) -> None:
    """A provider replaces the report directory; its joined launcher cannot publish exit."""
    env = private_environment(tmp_path)
    original, moved = tmp_path / "private", tmp_path / "moved"
    report, effect = original / "outcome.json", tmp_path / "effect"
    program = (
        "import pathlib,sys; src=pathlib.Path(sys.argv[1]); "
        "src.rename(pathlib.Path(sys.argv[2])); src.write_text('controlled replacement'); "
        "pathlib.Path(sys.argv[3]).write_text('effect completed')"
    )
    run = subprocess.run(
        launch_arguments(report, program, str(original), str(moved), str(effect)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert run.returncode == 2
    recovered = moved / "outcome.json"
    before = recovered.read_bytes()
    assert json.loads(before)["state"] == "started"
    result = inquire(recovered, env)
    assert result["state"] == "unknown"
    assert result["observation"] == "launcher_unconfirmed"
    assert result["recovery"] == "inquire_or_operator"
    assert result["external_effect"] == "unknown"
    assert recovered.read_bytes() == before
    assert original.read_text() == "controlled replacement"
    assert effect.read_text() == "effect completed"


def test_real_interrupted_wait_confirms_only_the_direct_child_stop(tmp_path: Path) -> None:
    """An actual pending interrupt and native child wait preserve its earlier effect."""
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    ready = threading.Event()

    def interrupt_after_effect() -> None:
        """Interrupt only this bounded API call once its real child has written."""
        deadline = time.monotonic() + 4
        while not effect.exists():
            if time.monotonic() >= deadline:
                ready.set()
                return
            time.sleep(0.01)
        ready.set()
        _thread.interrupt_main()

    timer = threading.Thread(target=interrupt_after_effect, daemon=True)
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            run_worker_session(
                identity="TEST/worker",
                arm=False,
                terminal_tmux="off",
                command=[
                    sys.executable,
                    "-c",
                    "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('effect'); "
                    "time.sleep(0.5)",
                    str(effect),
                ],
                environ=env,
                cwd=tmp_path,
                outcome_file=report,
            )
    finally:
        timer.join(timeout=5)
        assert not timer.is_alive()
    assert ready.is_set() and effect.read_text() == "effect"
    result = read_worker_outcome(report)
    assert result["state"] == result["external_effect"] == "unknown"
    assert result["stop_requested"] and result["stop_confirmed"]
    assert not result["effects_undone"]


def test_public_api_keeps_integer_result_with_scoped_observation(tmp_path: Path) -> None:
    """Existing integer callers keep their exit code and typed clients get the outcome."""
    result = run_worker_session(
        identity="TEST/worker",
        command=[sys.executable, "-c", "raise SystemExit(7)"],
        arm=False,
        terminal_tmux="off",
        environ=private_environment(tmp_path),
        cwd=tmp_path,
    )
    assert isinstance(result, WorkerExit) and result == 7
    assert result.outcome.state.value == "confirmed_failed"
    assert result.outcome.external_effect.value == "unknown"
