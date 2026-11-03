# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — disposable namespace prerequisite writer
"""Construct a minimal private mount graph and exercise actual kernel restrictions."""

from __future__ import annotations

import argparse
import array
import asyncio
import errno
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.protected_write_descriptor_filter import (
    install_protected_writer_descriptor_filter,
)
from synapse_channel.core.protected_write_kernel_boundary import (
    ProtectedRootKernelIdentity,
    ProtectedWriterKernelPolicy,
    capture_protected_writer_kernel_evidence,
    verify_protected_writer_kernel_boundary,
)
from synapse_channel.core.protected_write_mount_attributes import (
    apply_protected_writer_mount_restrictions,
)

from . import require_native_linux
from .authority_child import call_endpoint
from .resources import enter_resource_leaf
from .roles import ROOT_COMMAND_ENV, Role, drop_enrolled_identity, validate_role_identity
from .runtime import (
    consume_probe_namespace_reference,
    probe_python_command,
    retire_probe_input,
    wait_probe_writer_release,
)

if TYPE_CHECKING:
    from .instrumentation_launch import RecordingLaunch


def _instrumented_writer(config: dict[str, Any]) -> tuple[RecordingLaunch, int] | None:
    """Decode only the complete fixed recording enrollment sent by the parent."""
    if "instrumentation" not in config:
        return None
    from .instrumentation_launch import RecordingLaunch

    record = config["instrumentation"]
    if (
        config["captured_runtime"] is not True
        or type(record) is not dict
        or record.keys() != {"manifest_sha256", "output_fd"}
        or type(record["output_fd"]) is not int
        or record["output_fd"] <= 2
    ):
        raise ValueError("invalid writer instrumentation enrollment")
    return (
        RecordingLaunch(
            "writer", record["manifest_sha256"], config["writer_uid"], config["writer_gid"]
        ),
        record["output_fd"],
    )


def _host_prerequisite_bindings() -> list[str]:
    """Expose the explicitly weaker host-tools profile without captured-runtime claims."""
    arguments = [
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
    ]
    for path in ("/etc/passwd", "/etc/group", "/etc/nsswitch.conf"):
        arguments += ["--ro-bind", path, path]
    return arguments


def _mount_arguments(config: dict[str, Any]) -> list[str]:
    """Declare the fixed minimal filesystem and namespace graph for bubblewrap."""
    arguments = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--as-pid-1",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--hostname",
        "synapse-prerequisite",
        "--chdir",
        "/",
        "--clearenv",
    ]
    captured_runtime = config["captured_runtime"]
    if type(captured_runtime) is not bool:
        raise ValueError("invalid namespace runtime profile")
    for key, value in ROOT_COMMAND_ENV.items():
        arguments += ["--setenv", key, value]
    if not captured_runtime:
        arguments += _host_prerequisite_bindings()
    arguments += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/sys/fs",
        "--ro-bind",
        "/sys/fs/cgroup",
        "/sys/fs/cgroup",
        "--ro-bind",
        config["view"],
        "/code",
        "--ro-bind",
        config["endpoints"],
        "/endpoints",
        "--bind-fd",
        str(config["data_fd"]),
        "/data",
    ]
    instrumentation = _instrumented_writer(config)
    if instrumentation is not None:
        arguments += ["--bind-fd", str(instrumentation[1]), "/instrumentation-data"]
    arguments += ["--cap-drop", "ALL"]
    if captured_runtime:
        cpu = "/sys/devices/system/cpu/online"
        arguments += ["--ro-bind", cpu, cpu]
    return arguments


async def _launch(config: dict[str, Any]) -> int:
    """Retain a root wrapper in its cgroup until all namespace children are reaped."""
    instrumentation = _instrumented_writer(config)
    if instrumentation is not None:
        from .capsule import capture_capsule
        from .instrumentation import instrumentation_selected

        capsule = capture_capsule(Path(config["view"]), instrumentation[0].manifest_sha256)
        instrumentation_selected(capsule, requested=True)
    validate_role_identity(Role("synapse-writer", config["writer_uid"], config["writer_gid"]))
    enter_resource_leaf(Path(config["cgroup"]))
    print('{"event":"namespace-launching"}', flush=True)
    consume_probe_namespace_reference(tuple(config["host_namespace"]))
    arguments = _mount_arguments(config)
    for capability in (
        "CAP_SYS_ADMIN",
        "CAP_DAC_OVERRIDE",
        "CAP_SETUID",
        "CAP_SETGID",
        "CAP_SETPCAP",
    ):
        arguments += ["--cap-add", capability]
    arguments += [
        *probe_python_command(
            Path("/code"),
            "namespace_child",
            captured_runtime=config["captured_runtime"],
            recording=instrumentation[0] if instrumentation is not None else None,
        ),
        "--inside",
    ]
    process = await asyncio.create_subprocess_exec(
        *arguments,
        pass_fds=(config["data_fd"],)
        + ((instrumentation[1],) if instrumentation is not None else ()),
        env=ROOT_COMMAND_ENV,
        start_new_session=True,
    )
    try:
        return await asyncio.wait_for(process.wait(), 60)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def _enrolled_policy(raw: bytes, config: dict[str, Any]) -> ProtectedWriterKernelPolicy:
    """Decode only the root controller's independently observed kernel enrollment."""
    if len(raw) > 8192:
        raise ValueError("prerequisite kernel enrollment exceeds byte bound")
    record = json.loads(raw)
    keys = {
        "mount_namespace",
        "mountinfo_sha256",
        "root",
        "subtree_identity",
    }
    instrumentation = _instrumented_writer(config)
    if instrumentation is not None:
        keys.add("instrumentation_root")
    if type(record) is not dict or record.keys() != keys:
        raise ValueError("prerequisite kernel enrollment shape differs")
    roots = [ProtectedRootKernelIdentity("/data", *record["root"])]
    if instrumentation is not None:
        roots.append(
            ProtectedRootKernelIdentity("/instrumentation-data", *record["instrumentation_root"])
        )
    return ProtectedWriterKernelPolicy(
        writer_uid=config["writer_uid"],
        writer_gid=config["writer_gid"],
        shared_uid=config["shared_uid"],
        host_mount_namespace=tuple(config["host_namespace"]),
        writer_mount_namespace=tuple(record["mount_namespace"]),
        mountinfo_sha256=record["mountinfo_sha256"],
        roots=tuple(roots),
        writable_mount_targets=frozenset(root.name for root in roots),
        writer_subtree_identity=tuple(record["subtree_identity"]),
    )


def _actual_effects(data: int, endpoint: Path, authority_uid: int) -> dict[str, object]:
    """Exercise permitted data creation, forbidden base writes and descriptor sends."""
    content = b"synapse-prerequisite-data-effect-v1\n"
    descriptor = os.open(
        "marker", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=data
    )
    try:
        if os.write(descriptor, content) != len(content):
            raise OSError("prerequisite marker write was incomplete")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        outside = os.open("/outside-marker", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as error:
        if error.errno != errno.EROFS:
            raise
        outside_errno = error.errno
    else:
        os.close(outside)
        raise ValueError("prerequisite writer changed the read-only base")
    left, right = socket.socketpair()
    try:
        try:
            left.sendmsg([b"x"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [data]))])
        except OSError as error:
            if error.errno != errno.EPERM:
                raise
        else:
            raise ValueError("prerequisite writer transferred an enrolled descriptor")
    finally:
        left.close()
        right.close()
    call_endpoint(endpoint, expected_peer_uid=authority_uid)
    return {
        "marker_sha256": hashlib.sha256(content).hexdigest(),
        "marker_bytes": len(content),
        "base_write_errno": outside_errno,
        "scm_rights_errno": errno.EPERM,
    }


def _inside(config: dict[str, Any]) -> None:
    """Apply root-only mount restrictions, then verify the independently enrolled writer."""
    data: int | None = None
    instrumentation_data: int | None = None
    subtree: int | None = None
    configuration_open = True
    release: int | None = None
    try:
        data = os.open("/data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        roots = {"/data": data}
        if _instrumented_writer(config) is not None:
            instrumentation_data = os.open(
                "/instrumentation-data",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            roots["/instrumentation-data"] = instrumentation_data
        subtree = os.open(config["cgroup"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        apply_protected_writer_mount_restrictions(
            host_mount_namespace=tuple(config["host_namespace"]),
            writable_mount_descriptors=roots,
            writer_uid=config["writer_uid"],
            writer_gid=config["writer_gid"],
            shared_uid=config["shared_uid"],
        )
        print('{"event":"writer-ready"}', flush=True)
        policy = _enrolled_policy(sys.stdin.buffer.readline(8193), config)
        release = os.dup(0)
        configuration_open = False
        retire_probe_input()
        drop_enrolled_identity(config["writer_uid"], config["writer_gid"])
        install_protected_writer_descriptor_filter(expected_abi="linux-x86_64")
        observed = capture_protected_writer_kernel_evidence(roots, subtree_descriptor=subtree)
        verify_protected_writer_kernel_boundary(observed, policy)
        print('{"event":"writer-restricted"}', flush=True)
        barrier, release = release, None
        wait_probe_writer_release(barrier)
        result = _actual_effects(data, Path("/endpoints/request.sock"), config["authority_uid"])
        print(json.dumps({"event": "writer-probe-complete", **result}), flush=True)
    finally:
        try:
            if configuration_open:
                retire_probe_input()
        finally:
            for descriptor in (release, data, instrumentation_data, subtree):
                if descriptor is not None:
                    os.close(descriptor)


def main(argv: list[str] | None = None) -> int:
    """Launch the fixed namespace builder or exercise its private root-stage child.

    Parameters
    ----------
    argv:
        Root-controlled mode. Configuration arrives over the controller's pipe.

    Returns
    -------
    int
        Zero only after independently enrolled kernel verification and real effects.

    Raises
    ------
    ValueError, OSError
        On unavailable isolation, malformed bounded configuration or a refused probe.

    Notes
    -----
    This is a disposable component qualification; it grants no Core execution
    permit and cannot replace full installed profile or CLASS acceptance.
    """
    require_native_linux(sys.platform, operation="prerequisite namespace entry")
    if os.getresuid() != (0, 0, 0):
        raise PermissionError("prerequisite namespace entry requires the root controller")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--instrumentation", action="store_true")
    arguments = parser.parse_args(argv)
    raw = sys.stdin.buffer.readline(8193)
    if len(raw) > 8192:
        raise ValueError("prerequisite namespace configuration exceeds byte bound")
    config = json.loads(raw)
    if ("instrumentation" in config) != arguments.instrumentation:
        raise ValueError("namespace instrumentation selector differs from parent enrollment")
    if arguments.inside:
        _inside(config)
    else:
        # The wrapper validates and closes the ancillary reference before launch.
        # Only the inside child then reads the remaining configuration from stdin.
        return asyncio.run(_launch(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
