# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — captured prerequisite runtime and fixed launch commands
"""Capture an exact runtime closure and launch without ambient executable fallback."""

from __future__ import annotations

import array
import asyncio
import hashlib
import os
import re
import select
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .capsule import (
    CapturedCapsule,
    decode_manifest,
    read_relative_file,
    require_posix_descriptors,
)
from .inventory import verify_directory_inventory
from .roles import ROOT_COMMAND_ENV

if TYPE_CHECKING:
    from .instrumentation_launch import RecordingLaunch

_NS_GET_NSTYPE = 0xB703
_CLONE_NEWNS = 0x00020000

EXECUTABLE_PAYLOADS = frozenset(
    {"runtime/native/lib/ld-linux-x86-64.so.2", "runtime/python/bin/python3.12"}
)
_REQUIRED = EXECUTABLE_PAYLOADS | {
    "runtime/python/lib/python3.12/encodings/__init__.py",
    "runtime/python/lib/python3.12/dist-packages/synapse_channel/core/protected_write_kernel_boundary.py",
    "runtime/python/lib/python3.12/dist-packages/websockets/__init__.py",
    "runtime/config/openssl.cnf",
    "runtime/config/UTC",
    "runtime/trust/ca-certificates.crt",
}


def captured_runtime_selected(capsule: CapturedCapsule) -> bool:
    """Identify an explicitly enrolled complete runtime profile, never a host fallback.

    Returns
    -------
    bool
        False only for an explicitly source-only prerequisite capsule. A capsule
        containing any runtime entry must include every fixed launch prerequisite.

    Raises
    ------
    ValueError
        If a partial runtime would otherwise select a fallback interpreter.

    Notes
    -----
    This checks required entry points, not exhaustive dynamic dependency closure.
    The operator-approved manifest and native qualification supply that evidence.
    """
    names = frozenset(capsule.content)
    selected = any(name.startswith("runtime/") for name in names)
    if selected and not _REQUIRED <= names:
        raise ValueError("captured prerequisite runtime is incomplete")
    return selected


def capture_runtime_content(image: Path, manifest: Path, approved_sha256: str) -> dict[str, bytes]:
    """Read exactly an independently pinned runtime-only capsule inventory.

    Parameters
    ----------
    image:
        Unprivileged source image. No source ownership is promoted to root trust.
    manifest:
        Canonical capsule manifest whose entries all start with ``runtime/``.
        The prefix is removed only while reading the source image.
    approved_sha256:
        Independently retained raw manifest digest. It is checked before decoding.

    Returns
    -------
    dict[str, bytes]
        Exact detached runtime bytes, with no extra, omitted or linked source file.

    Raises
    ------
    ValueError, OSError
        On manifest mismatch, unsafe source, incomplete scope or changed bytes.

    Notes
    -----
    All file/count/aggregate limits come from the existing strict capsule codec.
    This performs no privileged action or installation. Root copies only the
    separately approved final capsule bytes, never executable staging paths.
    """
    require_posix_descriptors(os.name)
    if type(approved_sha256) is not str or re.fullmatch("[0-9a-f]{64}", approved_sha256) is None:
        raise ValueError("invalid independently retained runtime manifest digest")
    parent = os.open(manifest.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = read_relative_file(parent, manifest.name, 2 * 1024 * 1024, require_single_link=True)
    finally:
        os.close(parent)
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("runtime manifest differs from independent pin")
    parsed = decode_manifest(raw)
    if any(not pin.path.startswith("runtime/") for pin in parsed.files):
        raise ValueError("runtime manifest contains a foreign payload scope")
    expected = {pin.path.removeprefix("runtime/"): pin for pin in parsed.files}
    descriptor = os.open(image, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        verify_directory_inventory(descriptor, frozenset(expected))
        content = {}
        for name, pin in expected.items():
            data = read_relative_file(descriptor, name, pin.size_bytes, require_single_link=True)
            if len(data) != pin.size_bytes or hashlib.sha256(data).hexdigest() != pin.sha256:
                raise ValueError("runtime bytes differ from enrolled pin")
            content[pin.path] = data
        captured_runtime_selected(CapturedCapsule(parsed, approved_sha256, content))
        return content
    finally:
        os.close(descriptor)


def probe_python_command(
    view: Path, module: str, *, captured_runtime: bool, recording: RecordingLaunch | None = None
) -> list[str]:
    """Construct one fixed probe launch without a shell or ambient Python imports.

    Parameters
    ----------
    view:
        Operator-retained readonly capsule view, or its private namespace alias.
    module:
        Exactly authority_child or namespace_child; arbitrary modules are refused.
    captured_runtime:
        Exact profile selected from the approved capsule, not caller routing text.
    recording:
        Optional independently enrolled finite recorder. Requires the captured
        runtime; the fixed entry rechecks its complete manifest before collection.

    Returns
    -------
    list[str]
        Isolated interpreter command with the exact frozen probe entry point.

    Raises
    ------
    ValueError
        On a relative view, invalid entry point or non-boolean profile.

    Notes
    -----
    This construction is not proof of root ownership or executable custody.
    The root controller must first seal and reverify the complete capsule view.
    Site loading is disabled before imports. Resource paths come from this
    validated view and are installed explicitly by the fixed entry code;
    the subprocess itself receives only the minimal command environment.
    """
    if (
        not view.is_absolute()
        or ".." in view.parts
        or module not in {"authority_child", "namespace_child"}
    ):
        raise ValueError("invalid fixed prerequisite launch")
    if type(captured_runtime) is not bool:
        raise ValueError("invalid prerequisite runtime selection")
    if recording is not None and not captured_runtime:
        raise ValueError("instrumentation child requires the captured runtime profile")
    paths = [str(view / "probe")]
    if captured_runtime:
        base = view / "runtime"
        paths.append(str(base / "python/lib/python3.12/dist-packages"))
        prefix = [
            str(base / "native/lib/ld-linux-x86-64.so.2"),
            "--inhibit-cache",
            "--library-path",
            str(base / "native/lib"),
            str(base / "python/bin/python3.12"),
        ]
    else:
        prefix = ["/usr/bin/python3.12"]
        paths.append(str(view / "app"))
    entry = (
        "import os,sys,runpy;os.environ.update("
        + repr(probe_environment(view, captured_runtime=captured_runtime))
        + ");sys.path[:0]="
        + repr(paths)
    )
    if recording is not None:
        entry += ";sys.argv[1:1]=" + repr(recording.entry_arguments(view, module))
    target = "instrumentation_launch" if recording is not None else module
    entry += (
        ";runpy.run_module(" + repr("protected_runtime_probe." + target) + ",run_name='__main__')"
    )
    return [*prefix, "-I", "-B", "-S", "-c", entry]


def probe_environment(view: Path, *, captured_runtime: bool) -> dict[str, str]:
    """Return the fixed public resource environment for the selected launch profile.

    The captured profile has no user site, writable home or ambient OpenSSL,
    certificate or timezone configuration. Its kernel interfaces still require
    explicit namespace enrollment. This returns no service credential.
    """
    if not view.is_absolute() or type(captured_runtime) is not bool:
        raise ValueError("invalid prerequisite environment selection")
    if not captured_runtime:
        return {"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"}
    base = view / "runtime"
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "HOME": "/nonexistent",
        "OPENSSL_CONF": str(base / "config/openssl.cnf"),
        "SSL_CERT_FILE": str(base / "trust/ca-certificates.crt"),
        "SSL_CERT_DIR": str(base / "trust/no-capath"),
        "TZ": ":" + str(base / "config/UTC"),
    }


async def read_probe_child_error(
    process: asyncio.subprocess.Process,
) -> dict[str, int | str | bool]:
    """Retain bounded error evidence from an already terminated owned child.

    Parameters
    ----------
    process:
        Actual owned child, independently stopped and reaped by its parent.
        Its error pipe must be exclusively retained by that parent.

    Returns
    -------
    dict[str, int | str | bool]
        Actual return code, at most 8192 error bytes decoded as UTF-8, and
        whether one additional observed byte proves truncation.

    Raises
    ------
    ValueError
        If the child remains live or lacks the required observation pipe.
    TimeoutError, OSError
        If bounded error observation fails. Never imply successful cleanup.

    Notes
    -----
    Reads internal qualification diagnostics only; it neither signals a child
    nor establishes cgroup quiescence, operation success or caller-safe text.
    """
    returncode = process.returncode
    if returncode is None or process.stderr is None:
        raise ValueError("probe error observation requires a terminated child and error pipe")
    try:
        raw = await asyncio.wait_for(process.stderr.readexactly(8193), 3)
    except asyncio.IncompleteReadError as error:
        raw = error.partial
    return {
        "returncode": returncode,
        "stderr": raw[:8192].decode("utf-8", errors="replace"),
        "stderr_truncated": len(raw) > 8192,
    }


async def spawn_probe_child(
    view: Path,
    module: str,
    *,
    captured_runtime: bool,
    descriptors: tuple[int, ...] = (),
    arguments: tuple[str, ...] = (),
    input_channel: socket.socket | None = None,
    recording: RecordingLaunch | None = None,
) -> asyncio.subprocess.Process:
    """Start one approved probe child using only the explicitly selected profile.

    Parameters
    ----------
    view:
        Root controller's retained readonly view. Ordinary preparation/testing
        may use owned staging but cannot claim installed or privileged custody.
    module:
        Fixed authority_child or namespace_child entry, never a shell command.
    captured_runtime:
        Manifest-selected profile. A missing captured executable fails; it never
        retries through the host interpreter.
    descriptors:
        Exact root-controller-retained FDs to inherit across this launch.
    arguments:
        Fixed entry-point arguments selected by the trusted root controller.
    input_channel:
        Owned anonymous Unix stream endpoint to inherit as stdin, or None for
        the ordinary configuration pipe. Ownership remains with the caller.
    recording:
        Optional finite profile enrollment. Only its dedicated output descriptor
        is inherited in addition to the independently selected construction FDs.

    Returns
    -------
    asyncio.subprocess.Process
        Owned session leader without the caller's controlling terminal, with
        bounded observation/configuration pipes. Caller
        must enforce deadlines, cgroup custody and complete child reaping.

    Raises
    ------
    ValueError
        On invalid view, module or profile, before process creation.
    OSError
        On unavailable executable or failed actual subprocess creation.
    """
    command = probe_python_command(
        view, module, captured_runtime=captured_runtime, recording=recording
    )
    if recording is not None and recording.output_descriptor is not None:
        descriptors = tuple(dict.fromkeys((*descriptors, recording.output_descriptor)))
    if input_channel is not None and (
        input_channel.fileno() < 0
        or input_channel.family != socket.AF_UNIX
        or input_channel.type != socket.SOCK_STREAM
    ):
        raise ValueError("probe configuration channel requires a live Unix stream socket")
    return await asyncio.create_subprocess_exec(
        *command,
        *arguments,
        stdin=asyncio.subprocess.PIPE if input_channel is None else input_channel.fileno(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=descriptors,
        env=ROOT_COMMAND_ENV,
        start_new_session=True,
        cwd="/",
        limit=8192,
    )


def send_probe_namespace_reference(channel: socket.socket, descriptor: int) -> None:
    """Send one retained kernel descriptor over the private construction channel.

    Parameters
    ----------
    channel:
        Parent-owned Unix stream socket after the wrapper's launch barrier.
    descriptor:
        Actual retained host mount namespace descriptor, never a numeric claim.

    Raises
    ------
    OSError
        If the single-byte descriptor frame cannot be sent. No retry or
        alternate namespace reference is selected.
    """
    rights = array.array("i", [descriptor])
    channel.sendmsg([b"N"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)])


def receive_probe_namespace_reference(channel_descriptor: int = 0) -> int:
    """Receive exactly one close-on-exec kernel descriptor from the construction channel.

    Parameters
    ----------
    channel_descriptor:
        Inherited private construction stdin. The original descriptor remains
        open; this function owns and closes only its duplicate.

    Returns
    -------
    int
        Received descriptor owned by the caller. Namespace identity is checked
        separately against the root controller's retained kernel reference.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On a malformed, missing, truncated or late frame. Every installed
        ancillary descriptor is closed on refusal.
    """
    duplicate = os.dup(channel_descriptor)
    try:
        channel = socket.socket(fileno=duplicate)
    except OSError:
        os.close(duplicate)
        raise
    with channel:
        if not select.select([channel], [], [], 3)[0]:
            raise TimeoutError("probe namespace descriptor frame did not arrive")
        raw, ancillary, flags, _address = channel.recvmsg(
            1,
            socket.CMSG_SPACE(array.array("i").itemsize),
            socket.MSG_CMSG_CLOEXEC | socket.MSG_DONTWAIT,
        )
    received: list[int] = []
    for level, kind, content in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            rights = array.array("i")
            rights.frombytes(content)
            received.extend(rights)
    try:
        if flags & socket.MSG_CTRUNC or raw != b"N" or len(ancillary) != 1 or len(received) != 1:
            raise ValueError("probe namespace descriptor frame differs")
        return received.pop()
    finally:
        for descriptor in received:
            os.close(descriptor)


def consume_probe_namespace_reference(
    host_mount_namespace: tuple[int, int], channel_descriptor: int = 0
) -> None:
    """Validate and retire the host namespace reference before sandbox creation.

    Parameters
    ----------
    host_mount_namespace:
        Independently enrolled host device/inode pin from the root controller.
        The controller retains its original reference outside the sandbox.
    channel_descriptor:
        Host-side wrapper's private construction channel, never sandbox stdin.

    Raises
    ------
    ValueError, OSError, TimeoutError
        On malformed enrollment, a different namespace type or identity, or a
        refused frame. The received descriptor is closed before any return.

    Notes
    -----
    Call only in the trusted host wrapper before launching the privileged
    namespace stage. Only the validated numeric pin crosses that boundary.
    """
    if (
        type(host_mount_namespace) is not tuple
        or len(host_mount_namespace) != 2
        or any(type(value) is not int for value in host_mount_namespace)
        or host_mount_namespace[0] < 0
        or host_mount_namespace[1] <= 0
    ):
        raise ValueError("invalid independently enrolled host namespace identity")
    import fcntl

    descriptor = receive_probe_namespace_reference(channel_descriptor)
    try:
        if fcntl.ioctl(descriptor, _NS_GET_NSTYPE) != _CLONE_NEWNS:
            raise ValueError("retained host descriptor is not a mount namespace")
        metadata = os.fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) != host_mount_namespace:
            raise ValueError("retained host namespace differs from independent enrollment")
    finally:
        os.close(descriptor)


def retire_probe_input() -> None:
    """Close the construction wrapper and its original inherited descriptor zero.

    Raises
    ------
    OSError, ValueError
        If retirement fails. The caller must not drop credentials or continue
        with an unretired configuration channel.

    Notes
    -----
    Owns the inside child's construction stdin exclusively. CPython's startup
    FileIO uses closefd=False, so closing its wrapper does not retire FD0.
    The actual descriptor is closed even if wrapper cleanup raises.
    """
    try:
        sys.stdin.close()
    finally:
        os.close(0)


def wait_probe_writer_release(descriptor: int, *, timeout_seconds: float = 5.0) -> None:
    """Consume the parent's one-byte release after independent credential observation.

    Parameters
    ----------
    descriptor:
        Owned read side of the fixed controller barrier, with construction
        input already retired. Closed on every observation outcome.
    timeout_seconds:
        Finite positive wait of at most fifteen seconds.

    Raises
    ------
    ValueError
        On invalid arguments, EOF or any byte other than the fixed release.
    TimeoutError
        Unless the parent releases the prepared writer within the deadline.
    OSError
        On an unavailable barrier or failed descriptor retirement.

    Notes
    -----
    This is not a command channel and never accepts JSON, file descriptors or
    an operation request. The caller must close it before actual target access.
    """
    require_posix_descriptors(os.name)
    if type(descriptor) is not int or descriptor < 0:
        raise ValueError("invalid prerequisite writer release descriptor")
    if type(timeout_seconds) is not float or not 0 < timeout_seconds <= 15:
        raise ValueError("invalid prerequisite writer release deadline")
    try:
        if not select.select([descriptor], [], [], timeout_seconds)[0]:
            raise TimeoutError("prerequisite writer release deadline exceeded")
        if os.read(descriptor, 1) != b"\x01":
            raise ValueError("prerequisite parent did not release the writer")
    finally:
        os.close(descriptor)
