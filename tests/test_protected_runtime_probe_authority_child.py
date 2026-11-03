# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — actual prerequisite listener framing contracts
"""Exercise one actual UID's bounded framing without enrolling runtime roles."""

from __future__ import annotations

import asyncio
import os
import selectors
import signal
import socket
import subprocess
import sys
from contextlib import suppress
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.authority_child import (
    probe_role_access,
    serve_prerequisite_listeners,
)
from websockets.asyncio.client import ClientConnection, unix_connect
from websockets.exceptions import ConnectionClosedError
from websockets.sync.client import unix_connect as sync_unix_connect

_FRAME = b"synapse-prerequisite-peer-check-v1"
_SERVER = """import sys,os,socket,asyncio
sys.path[:0]=[sys.argv[1],sys.argv[1]+'/src',sys.argv[2]]
from tools.protected_runtime_probe.authority_child import (
    probe_role_access,
    serve_prerequisite_listeners,
)
request=socket.socket(fileno=int(sys.argv[3]));operator=socket.socket(fileno=int(sys.argv[4]))
config={name:os.geteuid() for name in ('writer_uid','client_uid','operator_uid')}
try:
 asyncio.run(serve_prerequisite_listeners(request,operator,config,graceful_stop=sys.argv[5]=='graceful'))
 print('finite-listeners-closed',flush=True)
finally: request.close();operator.close()
"""


async def _connect_listener(path: str, server: asyncio.Task[None]) -> ClientConnection:
    """Wait only for this owned listener, propagating its startup failure."""
    for _attempt in range(100):
        if server.done():
            await server
            raise RuntimeError("owned authority listener exited before connection")
        try:
            return await unix_connect(
                path, max_size=128, open_timeout=1, close_timeout=1, ping_interval=None
            )
        except ConnectionRefusedError:
            await asyncio.sleep(0.01)
    raise TimeoutError("owned authority listener did not start")


@pytest.mark.parametrize("endpoint", ["request", "operator"])
@pytest.mark.parametrize("case", ["accepted", "principal", "frame"])
async def test_actual_listener_peer_and_frame_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, endpoint: str, case: str
) -> None:
    """Check actual kernel peer UID against explicit component admission policy."""
    if sys.platform != "linux":
        with socket.socket() as request, socket.socket() as operator:
            with pytest.raises(OSError, match="requires native Linux"):
                await serve_prerequisite_listeners(request, operator, {})
        return
    monkeypatch.chdir(tmp_path)
    actual_uid = os.getuid()
    assert actual_uid != 0
    allowed_uid = actual_uid + 1 if case == "principal" else actual_uid
    config = {
        "writer_uid": allowed_uid,
        "client_uid": allowed_uid,
        "operator_uid": allowed_uid,
    }
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as request,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as operator,
    ):
        request.bind("request.sock")
        operator.bind("operator.sock")
        server = asyncio.create_task(serve_prerequisite_listeners(request, operator, config))
        try:
            client = await asyncio.wait_for(
                _connect_listener(endpoint + ".sock", server), timeout=3
            )
            async with client:
                if case == "principal":
                    with pytest.raises(ConnectionClosedError) as closed:
                        await asyncio.wait_for(client.recv(), timeout=2)
                    assert closed.value.rcvd is not None
                    assert closed.value.rcvd.code == 1008
                    assert closed.value.rcvd.reason == "unexpected prerequisite principal"
                else:
                    await client.send(b"different-frame" if case == "frame" else _FRAME)
                    if case == "frame":
                        with pytest.raises(ConnectionClosedError) as closed:
                            await asyncio.wait_for(client.recv(), timeout=2)
                        assert closed.value.rcvd is not None
                        assert closed.value.rcvd.code == 1008
                        assert closed.value.rcvd.reason == "unexpected prerequisite frame"
                    else:
                        assert await asyncio.wait_for(client.recv(), timeout=2) == _FRAME
        finally:
            server.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.wait_for(server, timeout=3)
    # Python 3.13 may already remove the listener path when its server closes.
    for name in ("request.sock", "operator.sock"):
        (tmp_path / name).unlink(missing_ok=True)
    assert not tuple(tmp_path.iterdir())


@pytest.mark.parametrize("graceful", [False, True])
def test_actual_owned_listener_termination_closes_gracefully_only_in_finite_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, graceful: bool
) -> None:
    """Real peer exchanges precede SIGTERM; only the explicit finite listener returns normally."""
    if sys.platform != "linux":
        with socket.socket() as request, socket.socket() as operator:
            with pytest.raises(OSError, match="requires native Linux"):
                asyncio.run(
                    serve_prerequisite_listeners(request, operator, {}, graceful_stop=graceful)
                )
        return
    monkeypatch.chdir(tmp_path)
    assert os.geteuid() != 0
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as request,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as operator,
    ):
        request.bind("request.sock")
        operator.bind("operator.sock")
        process = subprocess.Popen(
            [
                "/usr/bin/python3.12",
                "-I",
                "-B",
                "-S",
                "-c",
                _SERVER,
                str(Path(__file__).resolve().parents[1]),
                str(Path(websockets.__file__).parent.parent),
                str(request.fileno()),
                str(operator.fileno()),
                "graceful" if graceful else "production",
            ],
            pass_fds=(request.fileno(), operator.fileno()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            cwd=tmp_path,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
        try:
            assert process.stdout is not None
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(5), "owned listener did not announce readiness"
            announced = process.stdout.readline().strip()
            if announced != '{"event":"authority-listening"}':
                _output, error = process.communicate(timeout=5)
                raise AssertionError("owned listener failed before readiness: " + error)
            for name in ("request.sock", "operator.sock"):
                with sync_unix_connect(
                    name, max_size=128, open_timeout=2, close_timeout=1, ping_interval=None
                ) as connection:
                    connection.send(_FRAME)
                    assert connection.recv(timeout=2) == _FRAME
            process.send_signal(signal.SIGTERM)
            output, error = process.communicate(timeout=5)
            assert process.returncode == (0 if graceful else -signal.SIGTERM), error
            assert output == ("finite-listeners-closed\n" if graceful else "")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
    # Python 3.13 may already remove the listener path when its server closes.
    for name in ("request.sock", "operator.sock"):
        (tmp_path / name).unlink(missing_ok=True)


@pytest.mark.parametrize("role", ["synapse-client", "synapse-operator"])
@pytest.mark.parametrize("data_mode", [0o000, 0o444, 0o555])
async def test_peer_probe_fails_when_actual_writer_data_is_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, data_mode: int
) -> None:
    """Real server/peer/DAC checks detect readable data without controller procfs traversal.

    All participants use this test's actual ordinary UID. This isolates the
    directory-access negative control and does not qualify dedicated-role launch.
    """
    if sys.platform != "linux":
        with pytest.raises(OSError, match="native Linux"):
            probe_role_access(role, tmp_path, 0, 0)
        return
    assert os.geteuid() != 0
    monkeypatch.chdir(tmp_path)
    data = Path("writer-data")
    data.mkdir(mode=0o700)
    reference = os.open(data, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    (tmp_path / "endpoints").mkdir(mode=0o700)
    anchor = os.open(tmp_path, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    endpoints = Path(f"/proc/self/fd/{anchor}/endpoints")
    paths = {name: endpoints / (name + ".sock") for name in ("request", "operator")}
    with (
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as request,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as operator,
    ):
        request.bind(str(paths["request"]))
        operator.bind(str(paths["operator"]))
        uid = os.geteuid()
        config = {name: uid for name in ("writer_uid", "client_uid", "operator_uid")}
        target = "request" if role == "synapse-client" else "operator"
        other = "operator" if target == "request" else "request"
        paths[target].chmod(0o600)
        paths[other].chmod(0o000)
        server = asyncio.create_task(serve_prerequisite_listeners(request, operator, config))
        try:
            ready = await _connect_listener(str(paths[target]), server)
            try:
                await ready.send(_FRAME)
                assert await ready.recv() == _FRAME
            finally:
                await ready.close()
            data.chmod(data_mode)
            call = asyncio.to_thread(probe_role_access, role, endpoints, reference, uid)
            if data_mode == 0o000:
                await call
            else:
                with pytest.raises(ValueError, match="non-writer role reached private data"):
                    await call
        finally:
            data.chmod(0o700)
            paths[other].chmod(0o600)
            os.close(reference)
            os.close(anchor)
            server.cancel()
            with suppress(asyncio.CancelledError):
                await server


@pytest.mark.parametrize(
    "role,message",
    [
        ("unknown", "unexpected prerequisite peer role"),
        ("synapse-client", "O_PATH only"),
    ],
)
def test_public_peer_rejects_role_and_actual_readonly_descriptor(
    tmp_path: Path, role: str, message: str
) -> None:
    """An actual read-only directory descriptor cannot stand in for an O_PATH anchor."""
    if sys.platform != "linux":
        return
    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(ValueError, match=message):
            probe_role_access(role, tmp_path, descriptor, os.geteuid())
        assert os.fstat(descriptor).st_ino == tmp_path.stat().st_ino
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("case", ["nonboolean-mode", "owned-signal-handler"])
async def test_public_listener_refuses_invalid_mode_or_existing_signal_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Real socket inputs refuse without replacing an existing SIGTERM handler."""
    from typing import cast

    if sys.platform != "linux":
        return
    monkeypatch.chdir(tmp_path)
    previous = signal.getsignal(signal.SIGTERM)

    def keep(signum: int, frame: object) -> None:
        """Retain the caller's actual signal-handler ownership."""

    if case == "owned-signal-handler":
        signal.signal(signal.SIGTERM, keep)
    try:
        with (
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as request,
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as operator,
        ):
            request.bind("request.sock")
            operator.bind("operator.sock")
            config = {name: os.geteuid() for name in ("writer_uid", "client_uid", "operator_uid")}
            expected = (
                "invalid finite listener termination mode"
                if case == "nonboolean-mode"
                else "cannot replace an existing termination handler"
            )
            with pytest.raises(ValueError, match=expected):
                await serve_prerequisite_listeners(
                    request,
                    operator,
                    config,
                    graceful_stop=cast(bool, "invalid") if case == "nonboolean-mode" else True,
                )
            if case == "owned-signal-handler":
                assert signal.getsignal(signal.SIGTERM) is keep
    finally:
        signal.signal(signal.SIGTERM, previous)
