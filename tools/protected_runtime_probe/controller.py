# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — finite privileged prerequisite qualification controller
"""Join real role, peer, namespace, resource and effect observations without grants."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import select
import signal
import socket
import stat
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from synapse_channel.core.protected_write_mounts import parse_protected_writer_mounts

from .capsule import CapturedCapsule
from .instrumentation import instrumentation_file_limit_failure
from .reports import publish_probe_result
from .resources import ResourceScope, create_resource_scope, finalize_probe_resources
from .roles import ROOT_COMMAND_ENV, Role, ensure_roles, observe_probe_writer_credentials
from .runtime import (
    captured_runtime_selected,
    read_probe_child_error,
    send_probe_namespace_reference,
    spawn_probe_child,
)
from .storage import open_directory, read_effect_marker

if TYPE_CHECKING:
    from .instrumentation_campaign import InstrumentationCampaign
    from .instrumentation_outputs import RecordingOutput

_NATIVE_TOOL_PATHS = (
    "/usr/bin/python3.12",
    "/usr/bin/bwrap",
    "/usr/bin/setfacl",
    "/usr/bin/getfacl",
    "/usr/sbin/useradd",
    "/etc/passwd",
    "/etc/group",
    "/etc/nsswitch.conf",
    "/etc/login.defs",
)


def _event_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject repeated child fields before they can overwrite an observation."""
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("prerequisite child returned a repeated event field")
        result[name] = value
    return result


async def read_probe_event(
    process: asyncio.subprocess.Process, expected: str, *, expected_role: str | None = None
) -> dict[str, object]:
    """Read one exact finite-protocol observation from an owned child's pipe.

    Parameters
    ----------
    process:
        Child retained by the controller, with its actual stdout pipe.
    expected:
        Authority, namespace, peer or writer event required at this phase.
    expected_role:
        Exact client or operator role for a peer observation; absent otherwise.

    Returns
    -------
    dict[str, object]
        Validated fields only. Unknown keys, duplicate keys, non-finite values,
        boolean integers and observations for another role are refused.

    Raises
    ------
    ValueError
        On unsupported expectations, missing framing or invalid field values.
    TimeoutError
        Unless the complete bounded event arrives within fifteen seconds.
    """
    signals = {"authority-listening", "namespace-launching", "writer-ready", "writer-restricted"}
    if expected not in signals | {"peer-probe-complete", "writer-probe-complete"}:
        raise ValueError("unsupported prerequisite child event expectation")
    if expected == "peer-probe-complete":
        if expected_role not in {"synapse-client", "synapse-operator"}:
            raise ValueError("prerequisite peer event requires its exact expected role")
    elif expected_role is not None:
        raise ValueError("prerequisite non-peer event cannot have a peer role")
    if process.stdout is None:
        raise ValueError("prerequisite child has no observation pipe")
    raw = await asyncio.wait_for(process.stdout.readline(), 15)
    if not 0 < len(raw) <= 8192 or not raw.endswith(b"\n"):
        raise ValueError("prerequisite child event is missing or unbounded")
    event = json.loads(raw, object_pairs_hook=_event_object)
    if type(event) is not dict or event.get("event") != expected:
        raise ValueError("prerequisite child returned an unexpected event")
    fields = {"event"}
    if expected == "peer-probe-complete":
        fields.add("role")
        if event.get("role") != expected_role:
            raise ValueError("prerequisite child returned an unexpected peer role")
    elif expected == "writer-probe-complete":
        fields.update({"marker_sha256", "marker_bytes", "base_write_errno", "scm_rights_errno"})
        _require_writer_effect_event(event)
    if set(event) != fields:
        raise ValueError("prerequisite child returned an unexpected event field set")
    return event


def _require_writer_effect_event(event: dict[str, Any]) -> None:
    """Require bounded marker evidence and the two exact native refusal observations."""
    digest = event.get("marker_sha256")
    if type(digest) is not str or re.fullmatch("[0-9a-f]{64}", digest) is None:
        raise ValueError("prerequisite writer event has an invalid marker digest")
    for name in ("marker_bytes", "base_write_errno", "scm_rights_errno"):
        if type(event.get(name)) is not int:
            raise ValueError("prerequisite writer event requires exact integer fields")
    if (
        not 0 < event["marker_bytes"] <= 4096
        or event["base_write_errno"] != errno.EROFS
        or event["scm_rights_errno"] != errno.EPERM
    ):
        raise ValueError("prerequisite writer event has invalid effect observations")


async def _send(
    channel: asyncio.subprocess.Process | socket.socket, config: dict[str, Any]
) -> None:
    """Send bounded root-selected configuration over the owned child channel."""
    raw = json.dumps(config, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(raw) > 8192:
        raise ValueError("prerequisite root configuration exceeds byte bound")
    if isinstance(channel, socket.socket):
        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(channel, raw), 3)
    else:
        if channel.stdin is None:
            raise ValueError("prerequisite child has no configuration pipe")
        channel.stdin.write(raw)
        await asyncio.wait_for(channel.stdin.drain(), 3)


async def run_probe_command(
    command: tuple[str, ...], *, timeout_seconds: float = 5.0
) -> tuple[int, bytes, bytes]:
    """Capture one caller-approved command and reap its child on expiry or cancellation.

    The caller retains command provenance and permissions. Commands use absolute
    program paths, fixed qualification environment, closed stdin and captured
    output. The timeout must be a finite positive float of at most 30 seconds.
    This controls one owned child; subtree qualification remains separate.
    """
    if type(timeout_seconds) is not float or not 0 < timeout_seconds <= 30:
        raise ValueError("invalid prerequisite command deadline")
    if not command or not Path(command[0]).is_absolute():
        raise ValueError("invalid prerequisite command path")
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=ROOT_COMMAND_ENV,
    )
    try:
        output, errors = await asyncio.wait_for(process.communicate(), timeout_seconds)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return await process.wait(), output, errors


async def _acl(path: Path, client_uid: int) -> str:
    """Install and independently read the one named client ACL on the request socket."""
    status, _output, _errors = await run_probe_command(
        ("/usr/bin/setfacl", "-m", f"u:{client_uid}:rw,m::rw", str(path))
    )
    if status:
        raise OSError("prerequisite client socket ACL installation failed")
    status, raw, _errors = await run_probe_command(
        ("/usr/bin/getfacl", "--omit-header", "--numeric", "--absolute-names", str(path))
    )
    expected = {"user::rw-", f"user:{client_uid}:rw-", "group::rw-", "mask::rw-", "other::---"}
    text = raw.decode("ascii")
    if status or set(text.split()) != expected:
        raise ValueError("prerequisite request ACL differs from exact client enrollment")
    return text


def _preflight(paths: tuple[str, ...] = _NATIVE_TOOL_PATHS) -> None:
    """Verify actual root-controlled objects; the campaign uses the fixed native set."""
    if not paths:
        raise ValueError("prerequisite native object set is empty")
    for name in paths:
        path = Path(name).resolve(strict=True)
        descriptor = open_directory(path.parent, trusted=True, create=False)
        try:
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_uid or info.st_gid or info.st_mode & 0o022:
                raise ValueError("prerequisite native tool/configuration is not root controlled")
        finally:
            os.close(descriptor)


def _isolated_writer_pid(scope: ResourceScope, host: tuple[int, int]) -> int:
    """Select the unique namespace child through the retained writer subtree."""
    members = (scope.path("writer") / "cgroup.procs").read_text().split()
    isolated: list[int] = []
    for member in members:
        process = Path("/proc") / member
        namespace = (process / "ns/mnt").stat()
        if (namespace.st_dev, namespace.st_ino) != host:
            isolated.append(int(member))
    if len(isolated) != 1:
        raise ValueError("prerequisite writer lacks one independently observable namespace child")
    return isolated[0]


def _independent_enrollment(
    scope: ResourceScope,
    host: tuple[int, int],
    data: int,
    *,
    instrumentation_output: RecordingOutput | None = None,
) -> dict[str, Any]:
    """Observe the actual writer namespace, mount graph and data inode from its root parent."""
    leaf = next(item for item in scope.leaves if item.name == "writer")
    process = Path("/proc") / str(_isolated_writer_pid(scope, host))
    namespace = (process / "ns/mnt").stat()
    with (process / "mountinfo").open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("prerequisite independent mount observation exceeds bound")
    mounts = parse_protected_writer_mounts(raw)
    target = [mount for mount in mounts if mount.target == "/data"]
    observed = (process / "root/data").stat()
    retained = os.fstat(data)
    if len(target) != 1 or (observed.st_dev, observed.st_ino) != (retained.st_dev, retained.st_ino):
        raise ValueError("prerequisite independent writer data binding differs")
    result = {
        "mount_namespace": [namespace.st_dev, namespace.st_ino],
        "mountinfo_sha256": hashlib.sha256(raw).hexdigest(),
        "root": [retained.st_dev, retained.st_ino, target[0].mount_id],
        "subtree_identity": list(leaf.identity),
    }
    if instrumentation_output is not None:
        target = [mount for mount in mounts if mount.target == "/instrumentation-data"]
        observed = (process / "root/instrumentation-data").stat()
        retained = os.fstat(instrumentation_output.descriptor)
        if (
            len(target) != 1
            or (observed.st_dev, observed.st_ino) != instrumentation_output.identity
            or (retained.st_dev, retained.st_ino) != instrumentation_output.identity
        ):
            raise ValueError("prerequisite independent instrumentation data binding differs")
        result["instrumentation_root"] = [retained.st_dev, retained.st_ino, target[0].mount_id]
    return result


async def _peers(
    view: Path,
    scope: ResourceScope,
    roles: dict[str, Role],
    endpoints: Path,
    data: int,
    children: list[asyncio.subprocess.Process],
    *,
    captured_runtime: bool,
    instrumentation: InstrumentationCampaign | None = None,
) -> None:
    """Exercise both real non-writer principals and their cross-role refusals."""
    for short in ("client", "operator"):
        reference = os.open(".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=data)
        try:
            process = await spawn_probe_child(
                view,
                "authority_child",
                captured_runtime=captured_runtime,
                descriptors=(reference,),
                arguments=("--cgroup", str(scope.path(short))),
                recording=instrumentation.launch(short) if instrumentation is not None else None,
            )
        finally:
            os.close(reference)
        children.append(process)
        if instrumentation is not None:
            instrumentation.observe_process(short, process.pid)
        await _send(
            process,
            {
                "role": "synapse-" + short,
                "endpoints": str(endpoints),
                "data_reference": reference,
                "authority_uid": roles["synapse-authority"].uid,
            },
        )
        await read_probe_event(process, "peer-probe-complete", expected_role="synapse-" + short)
        if await asyncio.wait_for(process.wait(), 5) != 0:
            raise ValueError("prerequisite dedicated peer exited unsuccessfully")


@dataclass
class _Campaign:
    """Root-controller custody of one finite qualification's live resources."""

    capsule: CapturedCapsule
    backing: int
    view: Path
    run_id: str
    retire_view: Callable[[], dict[str, object]]
    captured_runtime: bool
    instrumentation: InstrumentationCampaign | None = None
    roles: dict[str, Role] = field(default_factory=dict, init=False)
    scope: ResourceScope | None = field(default=None, init=False)
    children: list[asyncio.subprocess.Process] = field(default_factory=list, init=False)
    sockets: list[socket.socket] = field(default_factory=list, init=False)
    data: int | None = field(default=None, init=False)
    host_fd: int | None = field(default=None, init=False)
    endpoints: Path | None = field(default=None, init=False)
    report: dict[str, Any] = field(default_factory=dict, init=False)


async def _start_authority(state: _Campaign) -> None:
    """Hand over bound descriptors; listen only after the actual authority UID drop."""
    if state.scope is None:
        raise ValueError("prerequisite resource scope is unavailable")
    authority = await spawn_probe_child(
        state.view,
        "authority_child",
        captured_runtime=state.captured_runtime,
        descriptors=tuple(item.fileno() for item in state.sockets),
        arguments=("--cgroup", str(state.scope.path("authority"))),
        recording=state.instrumentation.launch("authority")
        if state.instrumentation is not None
        else None,
    )
    state.children.append(authority)
    if state.instrumentation is not None:
        state.instrumentation.observe_process("authority", authority.pid)
    await _send(
        authority,
        {
            "role": "synapse-authority",
            "request_fd": state.sockets[0].fileno(),
            "operator_fd": state.sockets[1].fileno(),
            "writer_uid": state.roles["synapse-writer"].uid,
            "client_uid": state.roles["synapse-client"].uid,
            "operator_uid": state.roles["synapse-operator"].uid,
        },
    )
    for item in state.sockets:
        item.close()
    await read_probe_event(authority, "authority-listening")


async def _run_writer(state: _Campaign) -> None:
    """Independently enroll the actual private writer before releasing its barrier."""
    if state.data is None or state.endpoints is None or state.scope is None:
        raise ValueError("prerequisite writer roots are unavailable")
    state.host_fd = os.open("/proc/self/ns/mnt", os.O_RDONLY | os.O_CLOEXEC)
    host_info = os.fstat(state.host_fd)
    host = (host_info.st_dev, host_info.st_ino)
    config: dict[str, Any] = {
        "view": str(state.view),
        "captured_runtime": state.captured_runtime,
        "endpoints": str(state.endpoints),
        "data_fd": state.data,
        "host_namespace": list(host),
        "cgroup": str(state.scope.path("writer")),
        "writer_uid": state.roles["synapse-writer"].uid,
        "writer_gid": state.roles["synapse-writer"].gid,
        "shared_uid": 1000,
        "authority_uid": state.roles["synapse-authority"].uid,
    }
    inherited: tuple[int, ...] = (state.data,)
    if state.instrumentation is not None:
        recording = state.instrumentation.launch("writer")
        descriptor = state.instrumentation.output("writer").descriptor
        config["instrumentation"] = {
            "manifest_sha256": recording.manifest_sha256,
            "output_fd": descriptor,
        }
        inherited += (descriptor,)
    control, child_stdin = socket.socketpair()
    with control, child_stdin:
        control.shutdown(socket.SHUT_RD)
        child_stdin.shutdown(socket.SHUT_WR)
        control.setblocking(False)
        writer = await spawn_probe_child(
            state.view,
            "namespace_child",
            captured_runtime=state.captured_runtime,
            descriptors=inherited,
            input_channel=child_stdin,
            recording=state.instrumentation.launch("namespace-wrapper")
            if state.instrumentation is not None
            else None,
        )
        state.children.append(writer)
        if state.instrumentation is not None:
            state.instrumentation.observe_process("namespace-wrapper", writer.pid)
        child_stdin.close()
        await _send(control, config)
        await read_probe_event(writer, "namespace-launching")
        send_probe_namespace_reference(control, state.host_fd)
        await _send(control, config)
        await read_probe_event(writer, "writer-ready")
        await _observe_restricted_writer(state, state.scope, state.data, host, control, writer)


async def _observe_restricted_writer(
    state: _Campaign,
    scope: ResourceScope,
    data: int,
    host: tuple[int, int],
    control: socket.socket,
    writer: asyncio.subprocess.Process,
) -> None:
    """Observe retained writer credentials and release only after the last liveness check."""
    output = state.instrumentation.output("writer") if state.instrumentation is not None else None
    enrollment = _independent_enrollment(scope, host, data, instrumentation_output=output)
    process_id = _isolated_writer_pid(scope, host)
    if state.instrumentation is not None:
        state.instrumentation.observe_process("writer", process_id)
    process_reference = os.pidfd_open(process_id)
    try:
        await _send(control, enrollment)
        await read_probe_event(writer, "writer-restricted")
        principal = observe_probe_writer_credentials(
            process_id,
            expected_uid=state.roles["synapse-writer"].uid,
            expected_gid=state.roles["synapse-writer"].gid,
        )
        monitor = select.poll()
        monitor.register(process_reference, select.POLLIN)
        if (
            _isolated_writer_pid(scope, host) != process_id
            or _independent_enrollment(scope, host, data, instrumentation_output=output)
            != enrollment
            or monitor.poll(0)
        ):
            raise ValueError("prerequisite writer changed before parent release")
        state.report["independent_writer_credentials"] = principal
        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(control, b"\x01"), 3)
        state.report["writer"] = await read_probe_event(writer, "writer-probe-complete")
        if await asyncio.wait_for(writer.wait(), 5) != 0:
            raise ValueError("prerequisite writer exited unsuccessfully")
        state.report["independent_enrollment"] = enrollment
    finally:
        os.close(process_reference)


def _record_child_observation(
    state: _Campaign, process: asyncio.subprocess.Process, observation: Mapping[str, object]
) -> None:
    """Retain the actual child outcome and classify active measurement file-limit errors."""
    state.report.setdefault("children", []).append(observation)
    if state.instrumentation is None:
        return
    if process.returncode != 0:
        state.report["component_checks_passed"] = False
    failure = instrumentation_file_limit_failure(
        returncode=process.returncode, stderr=str(observation.get("stderr", ""))
    )
    if failure is not None:
        state.report["instrumentation_error"] = failure
        state.report["component_checks_passed"] = False


async def _cleanup(state: _Campaign) -> None:
    """Attempt every owned subtree stop and retain failures before reading actual effects."""
    cancelled: asyncio.CancelledError | None = None
    try:
        if state.instrumentation is not None and state.children:
            await _finish_authority_recording(state)
    except asyncio.CancelledError as error:
        cancelled = error
        state.report["component_checks_passed"] = False
        state.report["authority_recording_graceful_exit"] = False
    finally:
        try:
            if state.scope is not None:
                state.scope.close()
                state.report["quiescent"] = True
        except Exception as error:
            state.report["cleanup_error"] = f"{type(error).__name__}: {error}"
    for process in state.children:
        if process.stdin is not None:
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            state.report["quiescent"] = False
            # A trusted child can fail during import before entering its leaf.
            # Reap that exact owned Process too; never substitute this for the
            # complete cgroup stop or upgrade the failed empty-state observation.
            if process.returncode is None:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), 5)
                except (OSError, TimeoutError) as error:
                    state.report["child_reap_error"] = f"{type(error).__name__}: {error}"
        try:
            observation = await read_probe_child_error(process)
        except (OSError, TimeoutError, ValueError):
            state.report["component_checks_passed"] = False
            state.report.setdefault("children", []).append(
                {"returncode": process.returncode, "stderr_unavailable": True}
            )
        else:
            _record_child_observation(state, process, observation)

    if cancelled is not None:
        raise cancelled


async def _finish_authority_recording(state: _Campaign) -> None:
    """Wait for the actual retained authority's finite save before the final subtree stop."""
    authority = state.children[0]
    try:
        if authority.returncode is None:
            authority.send_signal(signal.SIGTERM)
        if await asyncio.wait_for(authority.wait(), 5) != 0:
            raise ValueError("instrumented authority did not exit successfully")
        state.report["authority_recording_graceful_exit"] = True
    except (OSError, ValueError, TimeoutError) as error:
        state.report["component_checks_passed"] = False
        state.report["authority_recording_graceful_exit"] = False
        state.report["instrumentation_stop_error"] = f"{type(error).__name__}: {error}"


def _retire_files(state: _Campaign) -> None:
    """Close owned descriptors and remove the finite campaign's ephemeral endpoints."""
    with ExitStack() as retired:
        if state.endpoints is not None:
            retired.callback(state.endpoints.rmdir)
            for name in ("request.sock", "operator.sock"):
                retired.callback((state.endpoints / name).unlink, missing_ok=True)
        if state.data is not None:
            retired.callback(os.close, state.data)
            retired.callback(_verify_effect, state.data, state.report)
        if state.host_fd is not None:
            retired.callback(os.close, state.host_fd)
        for item in state.sockets:
            retired.callback(item.close)


def _instrumentation_resource_envelope() -> dict[str, object]:
    """Retain actual controller limits inherited by the instrumented role processes."""
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    return {
        "rlimit_fsize_soft_bytes": soft,
        "rlimit_fsize_hard_bytes": hard,
        "rlimit_infinity_value": resource.RLIM_INFINITY,
        "authority_stop": "SIGTERM with --graceful-stop; instrumented profile only",
        "file_limit_failure_grade": "ERROR: instrumentation artifact, not a product result",
    }


def _record_instrumentation_os_error(state: _Campaign, error: BaseException) -> None:
    """Classify only actual OS file-limit failures under the selected instrumented profile."""
    if state.instrumentation is not None and isinstance(error, OSError):
        failure = instrumentation_file_limit_failure(error=error)
        if failure is not None:
            state.report["instrumentation_error"] = failure
            state.report["component_checks_passed"] = False


def _cleanup_failed(state: _Campaign, error: BaseException) -> None:
    """Record interrupted finalization without preserving a provisional success grade."""
    state.report["component_checks_passed"] = False
    state.report["quiescent"] = False
    state.report["cleanup_error"] = f"{type(error).__name__}: {error}"
    _record_instrumentation_os_error(state, error)


def _publish_campaign(state: _Campaign) -> None:
    """Publish the final finite outcome after every endpoint retirement attempt."""
    if state.instrumentation is not None:
        try:
            state.report["instrumentation"] = state.instrumentation.finish_collection(
                quiescent=state.report["quiescent"],
                producers_succeeded=state.report.get("authority_recording_graceful_exit") is True
                and all(process.returncode == 0 for process in state.children),
            )
        except Exception as error:
            state.report["component_checks_passed"] = False
            _record_instrumentation_os_error(state, error)
            state.report["instrumentation"] = {
                "collection_complete": False,
                "error": f"{type(error).__name__}: {error}",
                "retention_hold": state.instrumentation.failure_retention(),
            }
    try:
        state.report["code_view_cleanup"] = state.retire_view()
    except Exception as error:
        _cleanup_failed(state, error)
        state.report["code_view_cleanup"] = {
            "path": str(state.view),
            "removed": False,
            "error": f"{type(error).__name__}: {error}",
        }
    target = publish_probe_result(state.backing, state.run_id, state.report)
    print(
        json.dumps(
            {
                "report": str(target),
                "component_checks_passed": state.report["component_checks_passed"],
                "quiescent": state.report["quiescent"],
            }
        ),
        flush=True,
    )


def _peer_data_reference_observation(data: int) -> dict[str, object]:
    """Record the actual data inode and the peer reference's deliberate metadata capability."""
    metadata = os.fstat(data)
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "owner_uid": metadata.st_uid,
        "owner_gid": metadata.st_gid,
        "mode": stat.S_IMODE(metadata.st_mode),
        "peer_descriptor_kind": "O_PATH only",
        "peer_metadata_access": "fstat is permitted; directory reads are not granted",
        "read_probe": "fresh DAC reopen through the peer's own /proc/self/fd reference",
    }


def _campaign_report(state: _Campaign) -> dict[str, object]:
    """Build a finite diagnostic report with actual instrumented resource disclosures."""
    report: dict[str, object] = {
        "schema": "synapse-prerequisite-result.v3",
        "interpreter_profile": "captured-runtime"
        if state.captured_runtime
        else "host-tools-prerequisite",
        "manifest_sha256": state.capsule.manifest_sha256,
        "source_head": state.capsule.manifest.source_head,
        "run_id": state.run_id,
        "whole_runtime_accepted": False,
        "class_consumption_accepted": False,
        "component_checks_passed": False,
        "quiescent": False,
        "phase": "native-preflight",
    }
    if state.instrumentation is not None:
        report["instrumentation_envelope"] = _instrumentation_resource_envelope()
    return report


async def _qualify(
    capsule: CapturedCapsule,
    backing: int,
    view: Path,
    run_id: str,
    retire_view: Callable[[], dict[str, object]],
    instrumentation: InstrumentationCampaign | None = None,
) -> int:
    """Own one finite component campaign, preserving failure and quiescence separately."""
    captured_runtime = captured_runtime_selected(capsule)
    state = _Campaign(
        capsule, backing, view, run_id, retire_view, captured_runtime, instrumentation
    )
    state.report = _campaign_report(state)
    try:
        _preflight()
        state.report["phase"] = "role-enrollment"
        roles = await ensure_roles()
        state.roles = roles
        if instrumentation is not None:
            instrumentation.enroll_roles(roles)
        state.report["roles"] = {
            name: {"uid": role.uid, "gid": role.gid} for name, role in roles.items()
        }
        state.report["phase"] = "resource-scope"
        state.scope = create_resource_scope(run_id)
        state.report["phase"] = "preparing-files"
        state.data, state.endpoints, state.sockets = await _prepare_files(backing, run_id, roles)
        state.report["peer_data_reference"] = _peer_data_reference_observation(state.data)
        state.report["phase"] = "authority-startup"
        await _start_authority(state)
        state.report["phase"] = "peer-checks"
        await _peers(
            view,
            state.scope,
            roles,
            state.endpoints,
            state.data,
            state.children,
            captured_runtime=captured_runtime,
            instrumentation=instrumentation,
        )
        state.report["phase"] = "writer-checks"
        await _run_writer(state)
        state.report["phase"] = "complete"
        state.report["component_checks_passed"] = True
    except (Exception, asyncio.CancelledError) as error:
        state.report["error"] = f"{type(error).__name__}: {error}"
        _record_instrumentation_os_error(state, error)
    finally:
        await finalize_probe_resources(
            _cleanup(state),
            retire=lambda: _retire_files(state),
            publish=lambda: _publish_campaign(state),
            failed=lambda error: _cleanup_failed(state, error),
        )
    return 0 if state.report["component_checks_passed"] and state.report["quiescent"] else 1


async def _prepare_files(
    backing: int, run_id: str, roles: dict[str, Role]
) -> tuple[int, Path, list[socket.socket]]:
    """Create only disposable writer data and two bound but not yet listening sockets."""
    os.mkdir("data", 0o700, dir_fd=backing)
    data = os.open("data", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=backing)
    # Sockets are ephemeral kernel objects; payloads and reports remain on Samsung.
    endpoints = Path("/run") / ("synapse-qualification-" + run_id)
    sockets: list[socket.socket] = []
    created = False
    try:
        os.fchown(data, roles["synapse-writer"].uid, roles["synapse-writer"].gid)
        root = open_directory(Path("/run"), trusted=True, create=False)
        os.close(root)
        endpoints.mkdir(mode=0o755)
        created = True
        for name, role in (
            ("request.sock", "synapse-writer"),
            ("operator.sock", "synapse-operator"),
        ):
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sockets.append(listener)
            path = endpoints / name
            # Atomically create a root/group-only socket. Never expose a default
            # world-writable socket between bind and permission tightening.
            previous_mask = os.umask(0o117)
            try:
                listener.bind(str(path))
            finally:
                os.umask(previous_mask)
            os.chown(path, 0, roles[role].gid)
            if stat.S_IMODE(path.lstat().st_mode) != 0o660:
                raise ValueError("prerequisite socket creation mask differs")
        await _acl(endpoints / "request.sock", roles["synapse-client"].uid)
        return data, endpoints, sockets
    except BaseException:
        for listener in sockets:
            listener.close()
        os.close(data)
        if created:
            for name in ("request.sock", "operator.sock"):
                (endpoints / name).unlink(missing_ok=True)
            endpoints.rmdir()
        raise


def _verify_effect(data: int, report: dict[str, Any]) -> None:
    """Read actual data only after the independent entire-subtree empty observation."""
    if not report["quiescent"] or not report["component_checks_passed"]:
        return
    raw = read_effect_marker(data)
    writer = report["writer"]
    if (
        len(raw) != writer["marker_bytes"]
        or hashlib.sha256(raw).hexdigest() != writer["marker_sha256"]
        or set(os.listdir(data)) != {"marker"}
    ):
        report["component_checks_passed"] = False
        raise ValueError("independent post-quiescence prerequisite effect differs")
    report["independent_effect_sha256"] = hashlib.sha256(raw).hexdigest()


def run(
    capsule: CapturedCapsule,
    backing: int,
    view: Path,
    run_id: str,
    retire_view: Callable[[], dict[str, object]],
    instrumentation: InstrumentationCampaign | None = None,
) -> int:
    """Run a bounded component qualification on fresh root-owned disposable state.

    Parameters
    ----------
    capsule:
        Independently approved complete bytes, reverified through the readonly view.
    backing:
        Root-retained Samsung run directory. No existing production data is admitted.
    view:
        Trusted root-owned immutable code mount created by the verified bootstrap.
    run_id:
        Fresh bootstrap-generated identifier shared by only this campaign's resources.
    retire_view:
        Bootstrap-owned callback that unmounts and verifies removal of its exact
        code view before evidence publication. An error preserves a failed result.
    instrumentation:
        Explicit six-process recorder prepared by the readonly root bootstrap.
        Absent retains the production profile without collector outputs or mounts.

    Returns
    -------
    int
        Zero only for passed component probes and confirmed recursive quiescence.

    Notes
    -----
    Leaves four disabled dedicated accounts and one readonly evidence bind as
    explicitly reviewed installation prerequisites. Does not install a daemon,
    configure an executor, issue an operation grant or accept CLASS consumers.
    """
    return asyncio.run(
        asyncio.wait_for(_qualify(capsule, backing, view, run_id, retire_view, instrumentation), 90)
    )
