# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — prerequisite framing and ACL contracts
"""Use owned processes, socketpairs and socket ACLs without host qualification."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import platform
import select
import signal
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from _platform_caps import requires_packaged_python312
from fixtures.protected_runtime_image import samsung_backing_or_refusal

if TYPE_CHECKING:
    from tools.protected_runtime_probe.controller import _Campaign

_ECHO = "import sys;sys.stdout.buffer.write(sys.stdin.buffer.readline())"
_EMIT = "import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))"


def _native_contract_profile() -> bool:
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

    observed = (
        sys.platform,
        platform.machine(),
        sys.version_info[:2],
        sys.maxsize.bit_length() + 1,
    )
    if observed != ("linux", "x86_64", (3, 12), 64):
        with pytest.raises(OSError, match="native Linux"):
            require_native_runtime_abi(*observed)
        return False
    return True


@asynccontextmanager
async def _owned_child(
    script: str,
    arguments: tuple[str, ...] = (),
    *,
    stdin: int = asyncio.subprocess.PIPE,
    stdout: int = asyncio.subprocess.PIPE,
) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-S",
        "-c",
        script,
        *arguments,
        stdin=stdin,
        stdout=stdout,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 3)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.stderr is not None
        assert process.returncode == 0, (await process.stderr.read()).decode()


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "boundary",
        "empty",
        "oversized",
        "non-object",
        "wrong-event",
        "invalid-json",
        "no-observation",
        "deadline",
    ],
)
async def test_actual_child_event_framing(case: str) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import read_probe_event

    raw = b'{"event":"authority-listening"}\n'
    if case == "boundary":
        prefix = b'{"event":"authority-listening"}'
        raw = prefix + b" " * (8192 - len(prefix) - 1) + b"\n"
        assert len(raw) == 8192
    elif case == "empty":
        raw = b""
    elif case == "oversized":
        raw = b"x" * 8192 + b"\n"
    elif case == "non-object":
        raw = b"[]\n"
    elif case == "wrong-event":
        raw = b'{"event":"other"}\n'
    elif case == "invalid-json":
        raw = b"{\n"
    script = _ECHO if case == "deadline" else _EMIT
    stdout = asyncio.subprocess.DEVNULL if case == "no-observation" else asyncio.subprocess.PIPE
    async with _owned_child(script, (raw.hex(),), stdout=stdout) as process:
        if case in {"normal", "boundary"}:
            assert await read_probe_event(process, "authority-listening") == json.loads(raw)
        elif case == "deadline":
            with pytest.raises(TimeoutError):
                await read_probe_event(process, "authority-listening")
        elif case == "invalid-json":
            with pytest.raises(json.JSONDecodeError):
                await read_probe_event(process, "authority-listening")
        else:
            message = (
                "no observation pipe"
                if case == "no-observation"
                else "missing or unbounded"
                if case in {"empty", "oversized"}
                else "unexpected event"
            )
            with pytest.raises(ValueError, match=message):
                await read_probe_event(process, "authority-listening")


@pytest.mark.parametrize("channel", ["socket", "pipe"])
async def test_actual_configuration_roundtrip(channel: str) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _send, read_probe_event

    config = {"event": "authority-listening"}
    if channel == "pipe":
        async with _owned_child(_ECHO) as process:
            await _send(process, config)
            assert await read_probe_event(process, "authority-listening") == config
        return
    parent, child = socket.socketpair()
    parent.setblocking(False)
    try:
        async with _owned_child(_ECHO, stdin=child.fileno()) as process:
            child.close()
            try:
                await _send(parent, config)
                assert await read_probe_event(process, "authority-listening") == config
            finally:
                parent.close()
    finally:
        parent.close()
        child.close()


async def test_oversized_configuration_sends_no_bytes() -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _send

    sender, receiver = socket.socketpair()
    try:
        sender.setblocking(False)
        receiver.setblocking(False)
        with pytest.raises(ValueError, match="configuration exceeds byte bound"):
            await _send(sender, {"padding": "x" * 8192})
        with pytest.raises(BlockingIOError):
            receiver.recv(1)
    finally:
        sender.close()
        receiver.close()


async def test_configuration_requires_an_actual_input_pipe() -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _send

    async with _owned_child(_ECHO, stdin=asyncio.subprocess.DEVNULL) as process:
        with pytest.raises(ValueError, match="no configuration pipe"):
            await _send(process, {"event": "authority-listening"})


async def test_actual_socket_backpressure_has_a_finite_deadline() -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _send

    sender, receiver = socket.socketpair()
    try:
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512)
        sender.setblocking(False)
        for _ in range(128):
            try:
                sender.send(b"x" * 4096)
            except BlockingIOError:
                break
        else:
            raise AssertionError("owned socket queue did not fill within its bound")
        with pytest.raises(TimeoutError):
            await _send(sender, {"event": "authority-listening"})
        assert sender.fileno() >= 0 and receiver.fileno() >= 0
    finally:
        sender.close()
        receiver.close()


@pytest.mark.parametrize("case", ["roundtrip", "missing", "mask-mismatch"])
async def test_actual_disposable_socket_acl_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _acl

    assert os.geteuid() != 0
    monkeypatch.chdir(tmp_path)
    path = Path("request.sock")
    client_uid = os.geteuid() + 1
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        if case != "missing":
            listener.bind(str(path))
            path.chmod(0o600 if case == "mask-mismatch" else 0o660)
        if case == "missing":
            with pytest.raises(OSError, match="ACL installation failed"):
                await _acl(path, client_uid)
            assert not path.exists()
        elif case == "mask-mismatch":
            with pytest.raises(ValueError, match="differs from exact client enrollment"):
                await _acl(path, client_uid)
        else:
            observed = await _acl(path, client_uid)
            assert set(observed.split()) == {
                "user::rw-",
                f"user:{client_uid}:rw-",
                "group::rw-",
                "mask::rw-",
                "other::---",
            }
            assert os.getxattr(path, "system.posix_acl_access")


@pytest.mark.parametrize("exit_code", [0, 7])
async def test_command_captures_actual_output_and_status(exit_code: int) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import run_probe_command

    script = "import sys;print('result');sys.stderr.write('diagnostic');sys.exit(int(sys.argv[1]))"
    assert await run_probe_command(
        (sys.executable, "-I", "-B", "-S", "-c", script, str(exit_code))
    ) == (exit_code, b"result\n", b"diagnostic")


@pytest.mark.parametrize("command", [(), ("relative-program",)])
async def test_command_requires_an_absolute_program(command: tuple[str, ...]) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import run_probe_command

    with pytest.raises(ValueError, match="command path"):
        await run_probe_command(command)


@pytest.mark.parametrize("deadline", [0.0, -1.0, 31.0, float("nan"), True])
async def test_command_refuses_invalid_deadlines_before_spawn(deadline: float) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import run_probe_command

    with pytest.raises(ValueError, match="command deadline"):
        await run_probe_command((), timeout_seconds=deadline)


async def test_command_propagates_actual_missing_program(tmp_path: Path) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import run_probe_command

    missing = tmp_path / "missing-program"
    with pytest.raises(FileNotFoundError):
        await run_probe_command((str(missing),))
    assert not missing.exists()


@pytest.mark.parametrize("interruption", ["deadline", "cancel"])
async def test_command_reaps_the_exact_owned_child(tmp_path: Path, interruption: str) -> None:
    """PIDFD observation covers one controlled child, without grading subtree quiescence."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import run_probe_command

    pidfile = tmp_path / "owned-child.json"
    script = (
        "import json,os,signal,sys\n"
        "with open(sys.argv[1],'x',encoding='ascii')as stream:\n"
        " stream.write(json.dumps({'pid':os.getpid(),'parent':os.getppid()}))\n"
        "signal.pause()\n"
    )
    task = asyncio.create_task(
        run_probe_command((sys.executable, "-I", "-B", "-S", "-c", script, str(pidfile)))
    )
    pidfd: int | None = None

    async def started() -> int:
        while True:
            try:
                record: object = json.loads(pidfile.read_text(encoding="ascii"))
            except (FileNotFoundError, json.JSONDecodeError):
                await asyncio.sleep(0.01)
                continue
            assert isinstance(record, dict) and record["parent"] == os.getpid()
            pid: object = record["pid"]
            assert isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
            return pid

    try:
        pid = await asyncio.wait_for(started(), 3)
        pidfd = os.pidfd_open(pid)
        if interruption == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(TimeoutError):
                await task
        monitor = select.poll()
        monitor.register(pidfd, select.POLLIN)
        events = monitor.poll(0)
        assert events and events[0][1] & select.POLLIN
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(task, 3)
        if pidfd is not None:
            monitor = select.poll()
            monitor.register(pidfd, select.POLLIN)
            if not monitor.poll(0):
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            os.close(pidfd)


@contextmanager
def _observation_campaign(tmp_path: Path) -> Iterator[_Campaign]:
    """Supply a model campaign and actual owned backing FD, without root enrollment."""
    from tools.protected_runtime_probe.capsule import CapsuleManifest, CapturedCapsule, FilePin
    from tools.protected_runtime_probe.controller import _Campaign

    source = Path(__file__).resolve().parents[1]
    head = subprocess.check_output(
        ["/usr/bin/git", "-C", str(source), "rev-parse", "HEAD"], text=True, timeout=10
    ).strip()
    raw = b"pass\n"
    manifest = CapsuleManifest(
        head, str(tmp_path), (FilePin("app/fixture.py", hashlib.sha256(raw).hexdigest(), len(raw)),)
    )
    capsule = CapturedCapsule(
        manifest, hashlib.sha256(manifest.canonical_bytes()).hexdigest(), {"app/fixture.py": raw}
    )
    backing = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        state = _Campaign(
            capsule,
            backing,
            tmp_path / "not-enrolled-view",
            "run-0123456789abcdef",
            lambda: {"removed": False},
            False,
        )
        state.report = {"quiescent": False, "component_checks_passed": True}
        yield state
    finally:
        os.close(backing)


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("native-set", marks=requires_packaged_python312),
        "user-file",
        "device",
        "missing",
        "empty",
    ],
)
def test_read_only_native_object_preflight_contract(tmp_path: Path, case: str) -> None:
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _preflight

    assert os.geteuid() != 0
    if case == "native-set":
        _preflight()
    elif case == "empty":
        with pytest.raises(ValueError, match="object set is empty"):
            _preflight(())
    elif case == "missing":
        with pytest.raises(FileNotFoundError):
            _preflight((str(tmp_path / "missing"),))
    elif case == "device":
        with pytest.raises(ValueError, match="not root controlled"):
            _preflight(("/dev/null",))
    else:
        path = tmp_path / "ordinary-file"
        path.write_bytes(b"unchanged")
        assert path.stat().st_uid == os.getuid()
        with pytest.raises(ValueError, match="trusted root ancestry"):
            _preflight((str(path),))
        assert path.read_bytes() == b"unchanged"


@pytest.mark.parametrize(
    "case", ["not-quiescent", "component-failed", "match", "length", "digest", "extra-file"]
)
def test_retained_marker_check_against_real_owned_files(tmp_path: Path, case: str) -> None:
    """Flags are model inputs; no test here proves actual writer quiescence."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _verify_effect

    raw = b"actual ordinary marker"
    report = {
        "quiescent": case != "not-quiescent",
        "component_checks_passed": case != "component-failed",
        "writer": {"marker_bytes": len(raw), "marker_sha256": hashlib.sha256(raw).hexdigest()},
    }
    if case not in {"not-quiescent", "component-failed"}:
        (tmp_path / "marker").write_bytes(raw + b"x" if case == "length" else raw)
    if case == "digest":
        (tmp_path / "marker").write_bytes(b"x" * len(raw))
    if case == "extra-file":
        (tmp_path / "extra").write_bytes(b"unexpected")
    directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if case in {"length", "digest", "extra-file"}:
            with pytest.raises(ValueError, match="post-quiescence prerequisite effect differs"):
                _verify_effect(directory, report)
            assert report["component_checks_passed"] is False
            assert "independent_effect_sha256" not in report
        else:
            _verify_effect(directory, report)
            if case == "match":
                assert report["independent_effect_sha256"] == hashlib.sha256(raw).hexdigest()
            else:
                assert "independent_effect_sha256" not in report and not list(tmp_path.iterdir())
    finally:
        os.close(directory)


@pytest.mark.parametrize("unexpected_file", [False, True])
def test_retirement_closes_actual_descriptors_and_endpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unexpected_file: bool
) -> None:
    if not samsung_backing_or_refusal(tmp_path):
        return
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _retire_files

    monkeypatch.chdir(tmp_path)
    endpoints = Path("endpoints")
    endpoints.mkdir()
    if unexpected_file:
        (endpoints / "extra").write_bytes(b"retained")
    (tmp_path / "data").mkdir()
    with _observation_campaign(tmp_path) as state:
        state.endpoints = endpoints
        state.data = os.open(tmp_path / "data", os.O_RDONLY | os.O_DIRECTORY)
        state.host_fd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
        descriptors = (state.data, state.host_fd)
        for name in ("request.sock", "operator.sock"):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(endpoints / name))
            state.sockets.append(listener)
        if unexpected_file:
            with pytest.raises(OSError) as error:
                _retire_files(state)
            assert error.value.errno == errno.ENOTEMPTY
            assert sorted(path.name for path in endpoints.iterdir()) == ["extra"]
            assert (endpoints / "extra").read_bytes() == b"retained"
            (endpoints / "extra").unlink()
            endpoints.rmdir()
        else:
            _retire_files(state)
        assert not endpoints.exists() and not list((tmp_path / "data").iterdir())
        assert all(listener.fileno() == -1 for listener in state.sockets)
        for descriptor in descriptors:
            with pytest.raises(OSError) as error:
                os.fstat(descriptor)
            assert error.value.errno == errno.EBADF


@pytest.mark.parametrize("case", ["exit", "eof", "deadline", "no-observation", "no-input"])
async def test_cleanup_reaps_real_ordinary_children(tmp_path: Path, case: str) -> None:
    """No cgroup exists; exact-child reaping never grades whole-subtree quiescence."""
    if not samsung_backing_or_refusal(tmp_path):
        return
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import _cleanup

    script = {
        "exit": "import sys;sys.stderr.write('observed')",
        "eof": "import sys;sys.stdin.buffer.read();sys.stderr.write('observed')",
        "deadline": "import signal;signal.pause()",
        "no-observation": "pass",
        "no-input": "import sys;sys.stderr.write('observed')",
    }[case]
    stderr = asyncio.subprocess.DEVNULL if case == "no-observation" else asyncio.subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-S",
        "-c",
        script,
        stdin=asyncio.subprocess.DEVNULL if case == "no-input" else asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
    )
    try:
        with _observation_campaign(tmp_path) as state:
            assert state.scope is None
            state.children.append(process)
            await _cleanup(state)
            assert process.returncode == (-signal.SIGKILL if case == "deadline" else 0)
            assert state.report["quiescent"] is False
            children = state.report["children"]
            assert len(children) == 1 and children[0]["returncode"] == process.returncode
            if case == "no-observation":
                assert state.report["component_checks_passed"] is False
                assert children[0]["stderr_unavailable"] is True
            else:
                assert children[0]["stderr"] == ("" if case == "deadline" else "observed")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.parametrize(
    ("expected", "role", "event"),
    [
        ("authority-listening", None, {"event": "authority-listening"}),
        ("namespace-launching", None, {"event": "namespace-launching"}),
        ("writer-ready", None, {"event": "writer-ready"}),
        ("writer-restricted", None, {"event": "writer-restricted"}),
        (
            "peer-probe-complete",
            "synapse-client",
            {"event": "peer-probe-complete", "role": "synapse-client"},
        ),
        (
            "peer-probe-complete",
            "synapse-operator",
            {"event": "peer-probe-complete", "role": "synapse-operator"},
        ),
        (
            "writer-probe-complete",
            None,
            {
                "event": "writer-probe-complete",
                "marker_sha256": "a" * 64,
                "marker_bytes": 36,
                "base_write_errno": errno.EROFS,
                "scm_rights_errno": errno.EPERM,
            },
        ),
    ],
)
async def test_actual_pipe_accepts_exact_phase_observations(
    expected: str, role: str | None, event: dict[str, object]
) -> None:
    """Use the controller's public observation API over an actual child pipe."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import read_probe_event

    raw = json.dumps(event).encode("ascii") + b"\n"
    async with _owned_child(_EMIT, (raw.hex(),)) as process:
        assert await read_probe_event(process, expected, expected_role=role) == event


@pytest.mark.parametrize(
    "case",
    [
        "extra-key",
        "missing-key",
        "wrong-type",
        "boolean",
        "non-finite",
        "invalid-digest",
        "invalid-length",
        "invalid-errno",
        "permission-only-refusal",
        "duplicate-key",
        "missing-newline",
        "wrong-role",
        "unsupported-expectation",
        "missing-expected-role",
        "unexpected-expected-role",
    ],
)
async def test_actual_pipe_rejects_invalid_phase_observations(case: str) -> None:
    """Malformed child output never becomes a report's accepted observation."""
    if not _native_contract_profile():
        return
    from tools.protected_runtime_probe.controller import read_probe_event

    expected = "writer-probe-complete"
    role: str | None = None
    event: dict[str, object] = {
        "event": expected,
        "marker_sha256": "a" * 64,
        "marker_bytes": 36,
        "base_write_errno": errno.EROFS,
        "scm_rights_errno": errno.EPERM,
    }
    if case == "extra-key":
        event["extra"] = "unaccepted"
    elif case == "missing-key":
        del event["marker_bytes"]
    elif case == "wrong-type":
        event["marker_bytes"] = "36"
    elif case == "boolean":
        event["marker_bytes"] = True
    elif case == "non-finite":
        event["marker_bytes"] = float("nan")
    elif case == "invalid-digest":
        event["marker_sha256"] = "a" * 63 + "\n"
    elif case == "invalid-length":
        event["marker_bytes"] = 0
    elif case == "invalid-errno":
        event["base_write_errno"] = 0
    elif case == "permission-only-refusal":
        event["base_write_errno"] = errno.EACCES
    elif case in {"wrong-role", "missing-expected-role"}:
        expected = "peer-probe-complete"
        role = "synapse-client" if case == "wrong-role" else None
        event = {"event": expected, "role": "synapse-operator"}
    elif case == "unsupported-expectation":
        expected = "unknown"
    elif case == "unexpected-expected-role":
        role = "synapse-client"
    raw = json.dumps(event).encode("ascii") + b"\n"
    if case == "duplicate-key":
        raw = raw.replace(b'"marker_bytes": 36', b'"marker_bytes": 36,"marker_bytes": 36')
    elif case == "missing-newline":
        raw = raw[:-1]
    report: dict[str, object] = {"component_checks_passed": False}
    async with _owned_child(_EMIT, (raw.hex(),)) as process:
        with pytest.raises(ValueError):
            report["writer"] = await read_probe_event(process, expected, expected_role=role)
    assert report == {"component_checks_passed": False}
    assert json.loads(json.dumps(report, allow_nan=False)) == report
