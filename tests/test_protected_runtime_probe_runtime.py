# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact captured-runtime preparation boundaries
"""Exercise actual filesystem capture, pinned manifests and fixed launch selection."""

from __future__ import annotations

import array
import asyncio
import errno
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from tools.protected_runtime_probe.capsule import (
    CapsuleManifest,
    CapturedCapsule,
    FilePin,
    decode_manifest,
)
from tools.protected_runtime_probe.prepare import prepare_packet
from tools.protected_runtime_probe.runtime import (
    capture_runtime_content,
    captured_runtime_selected,
    consume_probe_namespace_reference,
    probe_environment,
    probe_python_command,
    read_probe_child_error,
    receive_probe_namespace_reference,
    send_probe_namespace_reference,
    spawn_probe_child,
)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native POSIX runtime descriptor capture"
)

_FILES = (
    "config/UTC",
    "config/openssl.cnf",
    "native/lib/ld-linux-x86-64.so.2",
    "python/bin/python3.12",
    "python/lib/python3.12/dist-packages/synapse_channel/core/protected_write_kernel_boundary.py",
    "python/lib/python3.12/dist-packages/websockets/__init__.py",
    "python/lib/python3.12/encodings/__init__.py",
    "trust/ca-certificates.crt",
)


def _linux_namespace_profile_or_refusal() -> bool:
    """Exercise genuine ABI refusal before Linux-only namespace transport probes."""
    if sys.platform == "linux":
        return True
    from tools.protected_runtime_probe.native_closure import require_native_runtime_abi

    with pytest.raises(OSError, match="native Linux"):
        require_native_runtime_abi(
            sys.platform, platform.machine(), sys.version_info[:2], sys.maxsize.bit_length() + 1
        )
    return False


def _manifest(content: dict[str, bytes]) -> CapsuleManifest:
    """Declare an exact byte-capture fixture, not an executable or OS-qualified image."""
    return CapsuleManifest(
        "a" * 40,
        "/media/anulum/GOTM/prerequisite-test-only",
        tuple(
            FilePin(name, hashlib.sha256(raw).hexdigest(), len(raw))
            for name, raw in sorted(content.items())
        ),
    )


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, dict[str, bytes]]:
    """Create real files and an independently retained canonical manifest digest."""
    image = tmp_path / "image"
    image.mkdir(mode=0o700)
    content = {}
    for name in _FILES:
        raw = ("captured bytes for " + name).encode()
        target = image / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        content["runtime/" + name] = raw
    manifest = tmp_path / "runtime.json"
    encoded = _manifest(content).canonical_bytes()
    manifest.write_bytes(encoded)
    return image, manifest, hashlib.sha256(encoded).hexdigest(), content


def test_exact_capture_detaches_actual_source_bytes(tmp_path: Path) -> None:
    """Complete matching files are captured once and survive later source changes."""
    image, manifest, digest, expected = _fixture(tmp_path)
    captured = capture_runtime_content(image, manifest, digest)
    assert captured == expected
    (image / "config/UTC").write_bytes(b"changed after capture")
    assert captured == expected
    capsule = CapturedCapsule(decode_manifest(manifest.read_bytes()), digest, captured)
    assert captured_runtime_selected(capsule)


@pytest.mark.parametrize(
    "change", ["extra", "missing", "bytes", "shorter", "symlink", "hardlink", "empty-directory"]
)
def test_changed_actual_runtime_files_refuse_complete_capture(tmp_path: Path, change: str) -> None:
    """No partial runtime is returned after actual scope, content or topology changes."""
    image, manifest, digest, _expected = _fixture(tmp_path)
    target = image / "config/UTC"
    if change == "extra":
        (image / "ambient-module.py").write_bytes(b"extra")
    elif change == "missing":
        target.unlink()
    elif change == "bytes":
        target.write_bytes(b"x" * target.stat().st_size)
    elif change == "shorter":
        target.write_bytes(b"")
    elif change == "hardlink":
        os.link(target, tmp_path / "outside-runtime-UTC")
    elif change == "empty-directory":
        (image / "not-in-manifest").mkdir()
    else:
        target.rename(target.with_name("actual-UTC"))
        target.symlink_to("actual-UTC")
    with pytest.raises(ValueError):
        capture_runtime_content(image, manifest, digest)


@pytest.mark.parametrize("change", ["bad_digest", "changed_manifest", "foreign", "partial"])
def test_manifest_pin_and_explicit_runtime_profile_refuse_substitution(
    tmp_path: Path, change: str
) -> None:
    """Even canonical files cannot select unpinned or partial runtime semantics."""
    image, manifest, digest, expected = _fixture(tmp_path)
    if change == "bad_digest":
        digest = "invalid"
    elif change == "changed_manifest":
        manifest.write_bytes(manifest.read_bytes() + b"\n")
    else:
        if change == "foreign":
            expected["app/foreign.py"] = b"foreign scope"
        else:
            expected.pop("runtime/config/UTC")
            (image / "config/UTC").unlink()
        raw = _manifest(expected).canonical_bytes()
        manifest.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):
        capture_runtime_content(image, manifest, digest)


def test_source_only_capsule_is_explicitly_limited_host_profile() -> None:
    """An ordinary source-only prerequisite is not silently labelled captured-runtime."""
    content = {"app/example.py": b"source"}
    manifest = _manifest(content)
    capsule = CapturedCapsule(
        manifest, hashlib.sha256(manifest.canonical_bytes()).hexdigest(), content
    )
    assert not captured_runtime_selected(capsule)


@pytest.mark.parametrize("change", ["relative", "module", "boolean", "parent"])
def test_invalid_launch_selection_cannot_choose_a_different_executable(change: str) -> None:
    """Malformed view/module/profile inputs fail before any subprocess is created."""
    view = Path("relative") if change == "relative" else Path("/code")
    if change == "parent":
        view = Path("/code/../other")
    module = "arbitrary.module" if change == "module" else "authority_child"
    profile = cast(bool, 1) if change == "boolean" else True
    with pytest.raises(ValueError):
        probe_python_command(view, module, captured_runtime=profile)


def test_captured_environment_excludes_actual_ambient_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Public resource paths stay pinned even when the actual caller environment is hostile."""
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "hostile-import"))
    monkeypatch.setenv("LD_PRELOAD", str(tmp_path / "hostile-library"))
    monkeypatch.setenv("OPENSSL_CONF", str(tmp_path / "hostile-configuration"))
    environment = probe_environment(tmp_path, captured_runtime=True)
    assert "PYTHONPATH" not in environment and "LD_PRELOAD" not in environment
    assert environment["OPENSSL_CONF"] != os.environ["OPENSSL_CONF"]
    with pytest.raises(ValueError):
        probe_environment(Path("relative"), captured_runtime=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("socket_input", [False, True])
async def test_real_prepared_child_refuses_privilege_and_missing_capture_never_falls_back(
    tmp_path: Path,
    socket_input: bool,
) -> None:
    """Reach actual root refusal; an absent captured executable cannot run host code."""
    source = Path(__file__).resolve().parents[1]
    if sys.platform != "linux" or platform.machine() != "x86_64" or sys.version_info[:2] != (3, 12):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(source, tmp_path / "packet", tmp_path / "root-runs")
        return
    storage = Path("/media/anulum/GOTM")
    if not storage.is_mount() or not tmp_path.is_relative_to(storage):
        with pytest.raises(ValueError, match="Samsung"):
            await asyncio.to_thread(
                prepare_packet, source, tmp_path / "packet", storage / "prerequisite-test-only"
            )
        return
    packet = tmp_path / "packet"
    await asyncio.to_thread(prepare_packet, source, packet, tmp_path / "root-runs")
    control, child = socket.socketpair()
    with control, child:
        launches: list[bool] = []
        observing = True

        def observe_launch(event: str, arguments: tuple[object, ...]) -> None:
            if observing and event == "subprocess.Popen":
                assert arguments[3] == {
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "LC_ALL": "C",
                }
                command = arguments[1]
                assert isinstance(command, (list, tuple)) and "-S" in command
                launches.append(True)

        sys.addaudithook(observe_launch)
        try:
            process = await spawn_probe_child(
                packet,
                "namespace_child",
                captured_runtime=False,
                input_channel=child if socket_input else None,
            )
            assert os.getsid(process.pid) == process.pid
            assert os.getsid(process.pid) != os.getsid(0)
        finally:
            observing = False
        assert launches == [True]
    try:
        assert process.stdout is not None
        output = await asyncio.wait_for(process.stdout.read(1), 10)
        await asyncio.wait_for(process.wait(), 5)
        observation = await read_probe_child_error(process)
        error = observation["stderr"]
        assert process.returncode != 0 and isinstance(error, str)
        assert "requires the root controller" in error
        assert observation["returncode"] == process.returncode
        assert observation["stderr_truncated"] is False
        assert output == b""
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    with pytest.raises(FileNotFoundError):
        await spawn_probe_child(packet, "namespace_child", captured_runtime=True)
    assert not (tmp_path / "root-runs").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [0, 8192, 8193])
@pytest.mark.parametrize("reader_limit", [1, 8192])
async def test_terminal_child_error_observation_is_bounded(size: int, reader_limit: int) -> None:
    """Actual terminated pipes retain a bounded prefix and honest truncation."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        (
            f"import os,sys;size={size};"
            "os.write(2,b'x'*min(size,8192));os.write(1,b'ready\\n');"
            "sys.stdin.buffer.read(1);os.write(2,b'x'*max(size-8192,0));"
            "raise SystemExit(17)"
        ),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=reader_limit,
    )
    try:
        assert process.stdout is not None and process.stdin is not None
        assert await asyncio.wait_for(process.stdout.readexactly(6), 5) == b"ready\n"
        process.stdin.write(b"!")
        await asyncio.wait_for(process.stdin.drain(), 3)

        async def terminated() -> None:
            while process.returncode is None:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(terminated(), 5)
        observed = await read_probe_child_error(process)
        assert observed == {
            "returncode": 17,
            "stderr": "x" * min(size, 8192),
            "stderr_truncated": size > 8192,
        }
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.kill()
        if process.stderr is not None:
            await asyncio.wait_for(process.stderr.read(8193), 3)
        await asyncio.wait_for(process.wait(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, True])
async def test_error_observation_requires_a_terminated_child_with_a_pipe(live: bool) -> None:
    """Real live children and actual missing pipes cannot yield a diagnostic claim."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        "-c",
        "import sys;sys.stdin.buffer.read(1)" if live else "raise SystemExit(17)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if live else asyncio.subprocess.DEVNULL,
    )
    try:
        if not live:
            await asyncio.wait_for(process.wait(), 5)
        with pytest.raises(ValueError, match="terminated child and error pipe"):
            await read_probe_child_error(process)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["closed", "inet", "datagram"])
async def test_probe_refuses_unsuitable_configuration_channels(kind: str) -> None:
    """Actual unsuitable endpoints fail before a fixed probe executable is started."""
    family = socket.AF_INET if kind == "inet" else socket.AF_UNIX
    mode = socket.SOCK_DGRAM if kind == "datagram" else socket.SOCK_STREAM
    with socket.socket(family, mode) as channel:
        if kind == "closed":
            channel.close()
        with pytest.raises(ValueError, match="live Unix stream"):
            await spawn_probe_child(
                Path("/code"),
                "namespace_child",
                captured_runtime=False,
                input_channel=channel,
            )


@pytest.mark.parametrize(
    "case", ["valid", "marker", "missing", "two", "truncated", "credentials", "eof", "late"]
)
def test_real_namespace_descriptor_frames_close_all_refused_rights(case: str) -> None:
    """Actual kernel ancillary messages preserve one reference or leak no descriptors."""
    if not _linux_namespace_profile_or_refusal():
        return
    reference = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        left, right = socket.socketpair()
        with left, right:
            if case == "credentials":
                right.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            if case == "valid":
                send_probe_namespace_reference(left, reference)
            elif case == "missing":
                left.sendall(b"N")
            elif case == "eof":
                left.shutdown(socket.SHUT_WR)
            elif case != "late":
                count = 3 if case == "truncated" else 2 if case == "two" else 1
                left.sendmsg(
                    [b"X" if case == "marker" else b"N"],
                    [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [reference] * count))],
                )
            before = set(os.listdir("/proc/self/fd"))
            if case == "valid":
                received = receive_probe_namespace_reference(right.fileno())
                try:
                    expected, actual = os.fstat(reference), os.fstat(received)
                    assert (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                    assert not os.get_inheritable(received)
                finally:
                    os.close(received)
            else:
                with pytest.raises(TimeoutError if case == "late" else ValueError):
                    receive_probe_namespace_reference(right.fileno())
            assert set(os.listdir("/proc/self/fd")) == before
    finally:
        os.close(reference)


def test_real_namespace_descriptor_send_failure_does_not_close_parent_reference() -> None:
    """A closed peer fails without retry or ownership transfer of the original FD."""
    if not _linux_namespace_profile_or_refusal():
        return
    reference = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        left, right = socket.socketpair()
        with left, right:
            right.close()
            with pytest.raises(BrokenPipeError):
                send_probe_namespace_reference(left, reference)
            assert os.fstat(reference).st_ino > 0
    finally:
        os.close(reference)


def test_namespace_channel_refuses_an_actual_file_without_leaking_its_duplicate() -> None:
    """A real non-socket stdin cannot install a namespace reference or leak an FD."""
    if not _linux_namespace_profile_or_refusal():
        return
    descriptor = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    try:
        before = set(os.listdir("/proc/self/fd"))
        with pytest.raises(OSError) as refused:
            receive_probe_namespace_reference(descriptor)
        assert refused.value.errno == errno.ENOTSOCK
        assert set(os.listdir("/proc/self/fd")) == before
        assert os.fstat(descriptor).st_ino > 0
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("case", ["valid", "mismatch", "other-type", "file", "negative", "zero"])
def test_host_wrapper_consumes_only_the_enrolled_namespace_and_closes_reference(
    tmp_path: Path, case: str
) -> None:
    """Validate actual nsfs types/pins and close every delivered descriptor before launch."""
    if not _linux_namespace_profile_or_refusal():
        return
    ordinary = tmp_path / "ordinary-file"
    ordinary.touch()
    path = (
        ordinary
        if case == "file"
        else Path("/proc/self/ns/user" if case == "other-type" else "/proc/self/ns/mnt")
    )
    reference = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        host = os.stat("/proc/self/ns/mnt")
        expected = (host.st_dev, host.st_ino + (case == "mismatch"))
        if case == "negative":
            expected = (-1, host.st_ino)
        elif case == "zero":
            expected = (host.st_dev, 0)
        left, right = socket.socketpair()
        with left, right:
            send_probe_namespace_reference(left, reference)
            before = set(os.listdir("/proc/self/fd"))
            if case == "valid":
                consume_probe_namespace_reference(expected, right.fileno())
            else:
                with pytest.raises(OSError if case == "file" else ValueError):
                    consume_probe_namespace_reference(expected, right.fileno())
            assert set(os.listdir("/proc/self/fd")) == before
            assert os.fstat(reference).st_ino > 0
    finally:
        os.close(reference)


def _namespace_transport_command() -> list[str]:
    """Construct a real read-only namespace probe with a two-stage stdin barrier."""
    source = Path(__file__).resolve().parents[1]
    inside = (
        "import errno,json,os,socket,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "from tools.protected_runtime_probe.runtime import "
        "retire_probe_input;"
        "current=os.stat('/proc/self/ns/mnt');"
        "config=json.loads(sys.stdin.buffer.readline(8193));"
        "assert config['second']=='configuration';"
        "host=tuple(config['host_namespace']);"
        "assert host!=(current.st_dev,current.st_ino);\n"
        "for name in os.listdir('/proc/self/fd'):\n"
        " try: info=os.fstat(int(name))\n"
        " except OSError as error:\n"
        "  assert error.errno==errno.EBADF;continue\n"
        " assert (info.st_dev,info.st_ino)!=host\n"
        "peer=socket.socket(fileno=os.dup(0));\n"
        "try: peer.send(b'forbidden reverse channel')\n"
        "except BrokenPipeError: pass\n"
        "else: raise AssertionError('construction stdin allowed a reverse write')\n"
        "peer.close();"
        "retire_probe_input();\n"
        "try: os.fstat(0)\n"
        "except OSError as error: assert error.errno==errno.EBADF\n"
        "else: raise AssertionError('construction descriptor zero remains open')\n"
        "assert sys.stdin.closed;"
        "print(json.dumps(list(host)),flush=True)"
    )
    wrapper = (
        "import json,subprocess,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "from tools.protected_runtime_probe.runtime import consume_probe_namespace_reference;"
        "config=json.loads(sys.stdin.buffer.readline(8193));"
        "assert config['first']=='configuration';"
        "print('namespace-launching',flush=True);"
        "consume_probe_namespace_reference(tuple(config['host_namespace']));"
        "raise SystemExit(subprocess.call(sys.argv[1:],timeout=10,start_new_session=True))"
    )
    return [
        sys.executable,
        "-I",
        "-B",
        "-c",
        wrapper,
        "/usr/bin/bwrap",
        "--new-session",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-pid",
        "--as-pid-1",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        sys.executable,
        "-I",
        "-B",
        "-c",
        inside,
    ]


def test_real_host_namespace_reference_is_retired_before_bwrap_and_child_barrier() -> None:
    """Retire the validated nsfs reference before real sandbox creation and its barrier."""
    if not _linux_namespace_profile_or_refusal():
        return
    reference = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    left, right = socket.socketpair()
    try:
        with left, right:
            left.shutdown(socket.SHUT_RD)
            right.shutdown(socket.SHUT_WR)
            process = subprocess.Popen(
                _namespace_transport_command(),
                stdin=right.fileno(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            right.close()
            try:
                expected = os.fstat(reference)
                host = [expected.st_dev, expected.st_ino]
                left.sendall(
                    json.dumps({"first": "configuration", "host_namespace": host}).encode() + b"\n"
                )
                assert process.stdout is not None
                assert process.stdout.readline() == b"namespace-launching\n"
                send_probe_namespace_reference(left, reference)
                left.sendall(
                    json.dumps({"second": "configuration", "host_namespace": host}).encode() + b"\n"
                )
                output, error = process.communicate(timeout=15)
                assert process.returncode == 0, error.decode()
                assert json.loads(output) == [expected.st_dev, expected.st_ino]
                with pytest.raises(OSError) as refused:
                    left.send(b"no receiver write channel")
                assert refused.value.errno in {errno.EPIPE, errno.ECONNRESET}
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
    finally:
        os.close(reference)


def test_retiring_actual_probe_stdin_closes_wrapper_and_kernel_descriptor() -> None:
    """Observe EBADF on the original socket without opening a replacement FD0."""
    source = Path(__file__).resolve().parents[1]
    code = (
        "import errno,json,os,sys;"
        f"sys.path.insert(0,{str(source)!r});"
        "from tools.protected_runtime_probe.runtime import retire_probe_input;"
        "raw=sys.stdin.buffer.raw;assert raw.closefd is False;"
        "assert sys.stdin.buffer.read(1)==b'!';"
        "retire_probe_input();assert sys.stdin.closed;\n"
        "try: os.fstat(0)\n"
        "except OSError as error: assert error.errno==errno.EBADF\n"
        "else: raise AssertionError('construction descriptor zero remains open')\n"
        "print(json.dumps({'wrapper_closed':True,'descriptor_errno':errno.EBADF}),flush=True)"
    )
    left, right = socket.socketpair()
    with left, right:
        left.shutdown(socket.SHUT_RD)
        right.shutdown(socket.SHUT_WR)
        left.sendall(b"!")
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", code],
            stdin=right.fileno(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        right.close()
        try:
            output, error = process.communicate(timeout=5)
            assert process.returncode == 0, error.decode()
            assert json.loads(output) == {"wrapper_closed": True, "descriptor_errno": errno.EBADF}
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


@pytest.mark.parametrize("case", ["release", "wrong-byte", "eof", "deadline"])
def test_actual_parent_barrier_consumes_one_byte_and_retires_descriptor(case: str) -> None:
    """The public release gate closes the actual pipe for every observation outcome."""
    from tools.protected_runtime_probe.runtime import wait_probe_writer_release

    reader, writer = os.pipe()
    try:
        if case != "deadline":
            if case != "eof":
                os.write(writer, b"\x01" if case == "release" else b"\x00")
            os.close(writer)
            writer = -1
        if case == "release":
            wait_probe_writer_release(reader, timeout_seconds=0.05)
        else:
            failure = TimeoutError if case == "deadline" else ValueError
            with pytest.raises(failure):
                wait_probe_writer_release(reader, timeout_seconds=0.05)
        with pytest.raises(OSError) as error:
            os.fstat(reader)
        assert error.value.errno == errno.EBADF
        reader = -1
    finally:
        for descriptor in (reader, writer):
            if descriptor >= 0:
                os.close(descriptor)


@pytest.mark.parametrize("raw", ["-1", "null", '"4"', "[]", "{}"])
def test_parent_barrier_refuses_invalid_descriptor_before_pipe_access(raw: str) -> None:
    """Malformed descriptor inputs must not consume or retire an existing pipe."""
    from tools.protected_runtime_probe.runtime import wait_probe_writer_release

    reader, writer = os.pipe()
    try:
        os.write(writer, b"\x01")
        with pytest.raises(ValueError, match="invalid prerequisite writer release descriptor"):
            wait_probe_writer_release(json.loads(raw), timeout_seconds=0.05)
        assert os.read(reader, 1) == b"\x01"
        assert os.fstat(reader).st_ino == os.fstat(writer).st_ino
    finally:
        os.close(reader)
        os.close(writer)


@pytest.mark.parametrize("raw", ["0.0", "-0.1", "15.0001", "1", "null", '"1.0"', "NaN", "Infinity"])
def test_parent_barrier_refuses_invalid_deadline_without_retiring_pipe(raw: str) -> None:
    """Reject wrong types and nonfinite or out-of-range deadlines before observation."""
    from tools.protected_runtime_probe.runtime import wait_probe_writer_release

    reader, writer = os.pipe()
    try:
        os.write(writer, b"\x01")
        with pytest.raises(ValueError, match="invalid prerequisite writer release deadline"):
            wait_probe_writer_release(reader, timeout_seconds=json.loads(raw))
        assert os.read(reader, 1) == b"\x01"
        assert os.fstat(reader).st_ino == os.fstat(writer).st_ino
    finally:
        os.close(reader)
        os.close(writer)
