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
from typing import Any

import pytest

from synapse_channel.execution_outcome import OutcomeState
from synapse_channel.worker_outcome import WorkerExit, WorkerObservation, read_worker_outcome
from synapse_channel.worker_session import run_worker_session

_REFUSAL = "^worker outcome record changed; inquire before continuing$"
# A hang guard, not a measurement: a real CLI launch imports the package in a
# fresh interpreter, which a loaded host can stretch past several seconds. A
# launch or readiness wait that truly hangs still fails, after this long.
_HANG_GUARD_SECONDS = 60
_SETUP_GUARD_SECONDS = 30
"""Bound on a child's wait for the launcher's started record, below the hang guard."""


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
        timeout=_HANG_GUARD_SECONDS,
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
        timeout=_HANG_GUARD_SECONDS,
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
        timeout=_HANG_GUARD_SECONDS,
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
        timeout=_HANG_GUARD_SECONDS,
    )
    assert run.returncode == exitcode, run.stderr + run.stdout
    result = inquire(report, env)
    assert result["state"] == ("confirmed_done" if exitcode == 0 else "confirmed_failed")
    assert result["returncode"] == exitcode
    assert result["external_effect"] == "unknown"
    assert result["external_recovery"] == "inquire_or_operator"
    assert not result["stop_requested"] and not result["stop_confirmed"]
    assert effect.read_text() == "effect"


def test_the_provider_is_told_the_operation_its_report_carries(tmp_path: Path) -> None:
    """The provider reads SYN_OPERATION_ID and it names the operation of its own report."""
    env = private_environment(tmp_path)
    env.pop("SYN_OPERATION_ID", None)
    report, seen = tmp_path / "private" / "outcome.json", tmp_path / "seen"
    program = (
        "import os,pathlib,sys; "
        "pathlib.Path(sys.argv[1]).write_text(os.environ['SYN_OPERATION_ID'])"
    )
    run = subprocess.run(
        launch_arguments(report, program, str(seen)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_HANG_GUARD_SECONDS,
    )
    assert run.returncode == 0, run.stderr + run.stdout
    operation_id = inquire(report, env)["operation_id"]
    assert isinstance(operation_id, str) and operation_id.startswith("worker-")
    assert seen.read_text() == operation_id


@pytest.mark.parametrize("raw", [b"{UNTRUSTED_REPORT_CONTENT", b"[]"])
def test_public_reader_refuses_truncated_or_nonobject_json(tmp_path: Path, raw: bytes) -> None:
    """Actual owner-local files cannot bypass the report document contract."""
    report = tmp_path / "outcome.json"
    with os.fdopen(os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(raw)
    with pytest.raises(ValueError, match="invalid worker outcome document"):
        read_worker_outcome(report)
    assert report.read_bytes() == raw


def test_a_finished_tmux_attachment_stays_an_unknown_outcome(tmp_path: Path) -> None:
    """A tmux launch that returns is still not confirmation that the provider finished."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    result = observation.tmux_result(lambda: 0)
    assert result == 0
    document = read_worker_outcome(report)
    assert document["state"] == document["external_effect"] == "unknown"
    assert document["observation"] == "tmux_attachment_ended"


def test_a_replace_the_kernel_refuses_leaves_no_temporary(tmp_path: Path) -> None:
    """A rename the kernel rejects closes the new descriptor and removes its temporary."""
    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    armed = True

    def refuse_replace(event: str, args: tuple[object, ...]) -> None:
        nonlocal armed
        if not armed or event != "os.rename" or Path(str(args[1])) != report.absolute():
            return
        armed = False
        report.unlink()
        report.mkdir()

    sys.addaudithook(refuse_replace)
    try:
        with pytest.raises(OSError):
            observation.observe(OutcomeState.STARTED, "provider_started")
    finally:
        observation.close()
    assert report.is_dir()
    assert list(report.parent.glob(".worker-outcome-*")) == []


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
    """A provider changes its report after its start is recorded; the final publication refuses.

    The provider waits for the launch record of this test's private state
    directory. The launcher writes it only after its ``provider_started``
    publication returned, so the change is seen only by the publication at exit.
    The record is matched by directory, not by process id: on Windows a venv's
    ``python.exe`` starts the interpreter as a child with another id. A change
    the start's own publication sees is the next test's case.
    """
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    program = (
        "import os,pathlib,sys,time\n"
        "state = pathlib.Path(os.environ['XDG_STATE_HOME'])\n"
        "records = state / 'synapse' / 'launch-records'\n"
        "deadline = time.monotonic() + 20\n"
        "while not list(records.glob('*.json')):\n"
        "    if time.monotonic() > deadline: sys.exit(3)\n"
        "    time.sleep(0.01)\n"
        "pathlib.Path(sys.argv[1]).write_text('UNTRUSTED_REPLACEMENT')\n"
        "pathlib.Path(sys.argv[2]).write_text('one effect')\n"
    )
    launched = subprocess.run(
        launch_arguments(report, program, str(report), str(effect)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert launched.returncode == 2
    assert "record changed; inquire before continuing" in launched.stdout
    assert "UNTRUSTED_REPLACEMENT" not in launched.stdout + launched.stderr
    assert report.read_text() == "UNTRUSTED_REPLACEMENT"
    assert effect.read_text() == "one effect"


def test_a_report_changed_before_the_start_is_recorded_stops_the_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start's own publication refuses a changed report, and the provider is stopped.

    The report is changed right after the provider process exists and before the
    launcher records ``provider_started``, so that publication sees the change. The
    changed report is kept, the provider is stopped and reaped before its effect,
    and nothing is launched again.
    """
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    program = "import pathlib,sys,time; time.sleep(30); pathlib.Path(sys.argv[1]).write_text('x')"
    command = [sys.executable, "-c", program, str(effect)]
    real_popen = subprocess.Popen
    started: list[subprocess.Popen[bytes]] = []

    def popen_then_change_the_report(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        # Only the provider's own start changes the report; on Windows the launch
        # also runs helper processes before the report exists.
        process: subprocess.Popen[bytes] = real_popen(*args, **kwargs)
        if args and list(args[0]) == command:
            started.append(process)
            report.write_text("UNTRUSTED_REPLACEMENT")
        return process

    monkeypatch.setattr(subprocess, "Popen", popen_then_change_the_report)
    with pytest.raises(ValueError, match=_REFUSAL):
        run_worker_session(
            identity="TEST/worker",
            command=command,
            terminal_tmux="off",
            arm=False,
            environ=private_environment(tmp_path),
            cwd=tmp_path,
            outcome_file=report,
        )
    assert report.read_text() == "UNTRUSTED_REPLACEMENT"
    assert len(started) == 1 and started[0].poll() is not None
    assert not effect.exists()


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
        timeout=_HANG_GUARD_SECONDS,
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
    """Read an actual pending launcher without inventing unavailable birth evidence."""
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
            deadline = time.monotonic() + _HANG_GUARD_SECONDS
            while not ready.exists():
                assert child.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            inflight = inquire(report, env)
            declared = json.loads(report.read_bytes())
            assert declared["state"] == "started"
            assert inflight["launcher"] == declared["launcher"]
            if sys.platform.startswith("linux") or sys.platform == "win32":
                assert inflight["state"] == "started"
            else:
                assert declared["launcher"]["started"] == ""
                assert inflight["state"] == "unknown"
                assert inflight["observation"] == "launcher_unconfirmed"
            stdout, stderr = child.communicate(input=b"finish\n", timeout=_HANG_GUARD_SECONDS)
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
        timeout=_HANG_GUARD_SECONDS,
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
    run = subprocess.run(
        argv, env=env, capture_output=True, text=True, check=False, timeout=_HANG_GUARD_SECONDS
    )
    assert run.returncode == 2
    result = inquire(report, env)
    assert result["state"] == result["external_effect"] == "not_started"
    assert result["observation"] == "provider_launch_refused"


def test_real_publication_failure_recovery_is_unknown_without_relaunch(tmp_path: Path) -> None:
    """A provider replaces the report directory; its joined launcher cannot publish exit.

    The launch record is written only after the started publication has
    returned, so the launcher is waiting and will not touch the directory
    again until the child exits. The child writes and flushes its effect, then
    renames the directory. The effect is therefore durable before the exit
    publication sees the replacement. Renaming any earlier races that
    publication: on Windows a directory with an open file cannot be renamed,
    and on every host the failed publication stops the child between its
    writes. The wait is a hang guard on the launch record, not part of the claim.
    """
    env = private_environment(tmp_path)
    original, moved = tmp_path / "private", tmp_path / "moved"
    report, effect = original / "outcome.json", tmp_path / "effect"
    program = "\n".join(
        [
            "import os, pathlib, sys, time",
            "src = pathlib.Path(sys.argv[1])",
            "records = pathlib.Path(os.environ['XDG_STATE_HOME']) / 'synapse' / 'launch-records'",
            f"deadline = time.monotonic() + {_SETUP_GUARD_SECONDS}",
            "while not any(records.glob('*.json')):",
            "    if time.monotonic() >= deadline:",
            "        raise SystemExit('the launch record was never published')",
            "    time.sleep(0.01)",
            "effect = pathlib.Path(sys.argv[3])",
            "effect.write_text('effect completed')",
            "with effect.open('rb+') as handle:",
            "    os.fsync(handle.fileno())",
            "src.rename(pathlib.Path(sys.argv[2]))",
            "src.write_text('controlled replacement')",
        ]
    )
    run = subprocess.run(
        launch_arguments(report, program, str(original), str(moved), str(effect)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_HANG_GUARD_SECONDS,
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
    """An actual pending interrupt and native child wait preserve its earlier effect.

    The child stays in the wait long enough for the interrupt to be pending
    before it exits. It does not wait for the parent's provider-started
    record: an interrupt during that publish still owns the bytes it replaced.
    The interrupt is raised while the child is still alive: the child is
    stopped before its sleep ends, so it never writes its second file. A wait
    that only notices the interrupt after the child exits lets it write.
    """
    env = private_environment(tmp_path)
    report, effect = tmp_path / "private" / "outcome.json", tmp_path / "effect"
    finished = tmp_path / "finished"
    ready = threading.Event()

    def interrupt_after_effect() -> None:
        """Interrupt only this bounded API call once its real child has written."""
        deadline = time.monotonic() + _HANG_GUARD_SECONDS
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
                    "time.sleep(8); pathlib.Path(sys.argv[2]).write_text('finished')",
                    str(effect),
                    str(finished),
                ],
                environ=env,
                cwd=tmp_path,
                outcome_file=report,
            )
    finally:
        timer.join(timeout=5)
        assert not timer.is_alive()
    assert ready.is_set() and effect.read_text() == "effect"
    assert not finished.exists()
    result = read_worker_outcome(report)
    assert result["state"] == result["external_effect"] == "unknown"
    assert result["stop_requested"] and result["stop_confirmed"]
    assert not result["effects_undone"]


def test_a_provider_that_outlives_one_wait_still_reports_its_exit(tmp_path: Path) -> None:
    """A child that keeps running past one wait still reports its own exit code.

    The wait looks again until the child exits. One look expiring is not a
    failure and not an interrupt.
    """
    result = run_worker_session(
        identity="TEST/worker",
        command=[sys.executable, "-c", "import time; time.sleep(0.15); raise SystemExit(0)"],
        arm=False,
        terminal_tmux="off",
        environ=private_environment(tmp_path),
        cwd=tmp_path,
    )
    assert isinstance(result, WorkerExit) and result == 0
    assert result.outcome.state.value == "confirmed_done"
    assert result.outcome.external_effect.value == "unknown"
    assert not result.outcome.stop_requested


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


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="the NT DACL is the Windows owner-only report floor",
)
def test_windows_report_dacl_is_owner_only_until_everyone_can_read(tmp_path: Path) -> None:
    """A real launch keeps the report owner-only; Everyone read makes inquiry refuse."""
    from synapse_channel.core.secure_path import assert_owner_only_file_path

    env = private_environment(tmp_path)
    report = tmp_path / "private" / "outcome.json"
    launch = subprocess.run(
        launch_arguments(report, "pass"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_HANG_GUARD_SECONDS,
    )
    assert launch.returncode == 0, launch.stderr + launch.stdout
    assert_owner_only_file_path(report, purpose="worker outcome")
    granted = subprocess.run(
        ["icacls", str(report), "/grant", "*S-1-1-0:(R)"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert granted.returncode == 0, granted.stderr
    refused = subprocess.run(
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
        timeout=_HANG_GUARD_SECONDS,
    )
    assert refused.returncode == 2
    assert refused.stdout == "worker-session: outcome report unavailable (SecurePathError)\n"
    assert not refused.stderr


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows closes the report descriptor, then a sharing violation fails the replace",
)
def test_windows_replace_leaves_published_bytes_when_another_handle_holds_the_report(
    tmp_path: Path,
) -> None:
    """An open handle blocks the rename; the update raises and leaves no temporary."""
    from synapse_channel.execution_outcome import OutcomeState
    from synapse_channel.worker_outcome import WorkerObservation

    report = tmp_path / "private" / "outcome.json"
    observation = WorkerObservation(report)
    published = report.read_bytes()
    held = os.open(report, os.O_RDONLY)
    try:
        with pytest.raises(PermissionError) as caught:
            observation.observe(OutcomeState.STARTED, "provider_started")
        # This host's rename over an open handle is ERROR_ACCESS_DENIED (5).
        assert getattr(caught.value, "winerror", None) == 5
    finally:
        os.close(held)
    assert report.read_bytes() == published
    assert [path.name for path in report.parent.iterdir()] == ["outcome.json"]
