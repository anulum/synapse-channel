# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — six-process finite recorder ownership
"""Own recorder outputs and parent observations until quiescent raw-byte sealing."""

from __future__ import annotations

import hashlib
import json
import os
import re
import select
import stat
import sys
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Literal

from . import require_native_linux
from .capsule import CapturedCapsule
from .instrumentation import CONFIGURATION, RECORDING_ROLES, instrumentation_selected
from .instrumentation_launch import RecordingLaunch
from .instrumentation_outputs import RecordingOutput, create_recording_output, seal_recording_output
from .instrumentation_recording import CoverageRecording, prepare_recording
from .instrumentation_retention import publish_recording_outputs
from .roles import NAMES, Role


def _require_root_controller() -> None:
    """Refuse filesystem or process enrollment outside the actual root controller."""
    require_native_linux(sys.platform, operation="finite recorder campaign")
    if os.getresuid() != (0, 0, 0) or os.getresgid() != (0, 0, 0):
        raise PermissionError("finite recorder campaign requires the actual root controller")


def _directory(parent: int, name: str) -> int:
    """Create one fixed private directory owned by this root campaign."""
    observed = os.fstat(parent)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or (observed.st_uid, observed.st_gid) != (0, 0)
        or stat.S_IMODE(observed.st_mode) != 0o700
    ):
        raise PermissionError("recording backing is not the private root-owned directory")
    os.mkdir(name, 0o700, dir_fd=parent)
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
    )
    try:
        os.fchmod(descriptor, 0o700)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _require_role_principals(roles: Mapping[str, Role]) -> None:
    """Require the complete distinct numeric identities resolved by the root parent."""
    if set(roles) != set(NAMES):
        raise ValueError("recording roles require a complete enrollment")
    principals = [(role.uid, role.gid) for role in roles.values()]
    if (
        any(role.name != name for name, role in roles.items())
        or any(
            type(uid) is not int
            or type(gid) is not int
            or not 0 < uid < 2**32 - 1
            or uid == 1000
            or not 0 < gid < 2**32 - 1
            for uid, gid in principals
        )
        or len({uid for uid, _gid in principals}) != len(NAMES)
        or len({gid for _uid, gid in principals}) != len(NAMES)
    ):
        raise ValueError("recording roles are not distinct dedicated principals")


class InstrumentationCampaign:
    """Retain six independently enrolled outputs through collection and publication.

    Notes
    -----
    Created only after immutable readonly capsule recapture, before importing
    the actual controller. The root controller supplies real role accounts,
    retained process handles/PIDs and independent final quiescence. This
    object issues no protected-operation or consumer-acceptance grant.
    """

    def __init__(self, capsule: CapturedCapsule, backing: int, view: Path, run_id: str) -> None:
        """Start controller recording over the already admitted complete profile."""
        _require_root_controller()
        if re.fullmatch("run-[0-9a-f]{16}", run_id) is None:
            raise ValueError("invalid recorder campaign identifier")
        instrumentation_selected(capsule, requested=True)
        self._capsule = capsule
        self._view = view
        self._run_id = run_id
        self._handles = ExitStack()
        self._outputs: dict[str, RecordingOutput] = {}
        self._pids: dict[str, int] = {}
        self._pidfds: dict[str, int] = {}
        self._active = False
        self._state: Literal["open", "finished", "closed"] = "open"
        try:
            self._incoming = self._retain(_directory(backing, "recordings"))
            self._sealed = self._retain(_directory(backing, "sealed-recordings"))
            output = self._new_output("controller", 0, 0)
            self.observe_process("controller", os.getpid())
            self._controller: CoverageRecording = prepare_recording(
                view,
                capsule.manifest_sha256,
                role="controller",
                output_descriptor=output.descriptor,
                uid=0,
                gid=0,
            )
            self._active = True
            self._controller.collector.start()
        except BaseException:
            self.close()
            raise

    def _retain(self, descriptor: int) -> int:
        self._handles.callback(os.close, descriptor)
        return descriptor

    def _new_output(self, role: str, uid: int, gid: int) -> RecordingOutput:
        output = create_recording_output(self._incoming, role, uid, gid)
        self._retain(output.descriptor)
        self._outputs[role] = output
        return output

    def enroll_roles(self, roles: Mapping[str, Role]) -> None:
        """Create five remaining leaves from the parent's actual role enrollment.

        Parameters
        ----------
        roles:
            Exact four independently resolved dedicated role accounts.

        Raises
        ------
        ValueError, OSError
            On a repeated/changed enrollment or unavailable private output.
        """
        _require_root_controller()
        if self._state != "open" or set(self._outputs) != {"controller"}:
            raise ValueError("recording roles require one complete fresh enrollment")
        _require_role_principals(roles)
        self._new_output("namespace-wrapper", 0, 0)
        for short in ("authority", "client", "operator", "writer"):
            role = roles["synapse-" + short]
            self._new_output(short, role.uid, role.gid)

    def output(self, role: str) -> RecordingOutput:
        """Return the exact enrolled leaf without selecting an arbitrary output path."""
        if self._state != "open" or role not in self._outputs:
            raise ValueError("recording output is not enrolled for this live campaign")
        return self._outputs[role]

    def launch(self, role: str) -> RecordingLaunch:
        """Return fixed child metadata, preserving the writer's namespace-only output root."""
        output = self.output(role)
        return RecordingLaunch(
            role,
            self._capsule.manifest_sha256,
            output.uid,
            output.gid,
            None if role == "writer" else output.descriptor,
        )

    def observe_process(self, role: str, host_pid: int) -> None:
        """Retain a pidfd for the parent's actual owned/observed process before it exits.

        Parameters
        ----------
        role:
            Exact enrolled recording role.
        host_pid:
            Parent-owned subprocess PID or independently observed inside-writer PID.

        Raises
        ------
        ValueError, OSError
            On duplicate/unknown identity, invalid PID or absent actual process.
        """
        _require_root_controller()
        if (
            self._state != "open"
            or role not in self._outputs
            or role in self._pids
            or type(host_pid) is not int
            or host_pid <= 0
            or host_pid in self._pids.values()
        ):
            raise ValueError("recording process identity is missing or already enrolled")
        self._pidfds[role] = self._retain(os.pidfd_open(host_pid))
        self._pids[role] = host_pid

    def finish_collection(self, *, quiescent: bool, producers_succeeded: bool) -> dict[str, object]:
        """Stop/save controller, then seal raw child bytes only after actual final quiescence.

        Parameters
        ----------
        quiescent:
            Exact successful parent observation after complete cgroup cleanup and
            child reaping. A flag alone grants no recovery or consumer authority.
        producers_succeeded:
            Exact parent observation of graceful authority completion and zero
            exit status for every owned producer. Forced cleanup cannot satisfy it.

        Returns
        -------
        dict[str, object]
            Complete root-generated dataset manifest pin and readonly retention.

        Raises
        ------
        ValueError, OSError
            On missing roles, unquiesced processes, invalid files or failed publication.
            Failure is terminal; any partial datasets remain in the failed backing.
        """
        _require_root_controller()
        if self._state != "open":
            raise ValueError("recording campaign is already terminal")
        self._state = "finished"
        self._stop_controller()
        if (
            quiescent is not True
            or producers_succeeded is not True
            or set(self._pids) != set(RECORDING_ROLES)
        ):
            raise ValueError(
                "recording sealing requires all six successful processes and quiescence"
            )
        self._require_stopped_children()
        datasets = [
            seal_recording_output(self._outputs[role], self._sealed, host_pid=self._pids[role])
            for role in RECORDING_ROLES
        ]
        digest, sources_digest = self._write_manifest(datasets)
        os.fchmod(self._incoming, 0o500)
        os.fchmod(self._sealed, 0o555)
        retention = publish_recording_outputs(self._sealed, self._run_id)
        return {
            "collection_complete": True,
            "manifest_sha256": digest,
            "source_entries_sha256": sources_digest,
            "datasets": datasets,
            "retention": retention,
        }

    def _require_stopped_children(self) -> None:
        """Cross-check each retained child pidfd without substituting for cgroup custody."""
        for role, descriptor in self._pidfds.items():
            if role == "controller":
                continue
            monitor = select.poll()
            monitor.register(descriptor, select.POLLIN)
            if not monitor.poll(0):
                raise ValueError("recording producer is still live at sealing")

    def _write_manifest(self, datasets: list[dict[str, str | int]]) -> tuple[str, str]:
        """Write only parent-owned process/source metadata, without opening a child database."""
        sources = [
            {"path": pin.path, "sha256": pin.sha256, "size_bytes": pin.size_bytes}
            for pin in self._capsule.manifest.files
            if pin.path.endswith(".py")
            and pin.path.startswith(("app/synapse_channel/", "probe/protected_runtime_probe/"))
        ]
        sources_digest = hashlib.sha256(_canonical(sources)).hexdigest()
        record = {
            "schema": "synapse.runtime-recordings.v1",
            "run_id": self._run_id,
            "capsule_manifest_sha256": self._capsule.manifest_sha256,
            "configuration_sha256": hashlib.sha256(
                self._capsule.content[CONFIGURATION]
            ).hexdigest(),
            "source_entries_sha256": sources_digest,
            "sources": sources,
            "code_view": str(self._view),
            "writer_code_view": "/code",
            "datasets": datasets,
        }
        raw = _canonical(record)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("recording manifest exceeds its byte bound")
        descriptor = os.open(
            "manifest.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o400,
            dir_fd=self._sealed,
        )
        try:
            if os.write(descriptor, raw) != len(raw):
                raise OSError("recording manifest write was incomplete")
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o444)
        finally:
            os.close(descriptor)
        return hashlib.sha256(raw).hexdigest(), sources_digest

    def _stop_controller(self) -> None:
        if self._active:
            self._active = False
            try:
                self._controller.collector.stop()
            finally:
                self._controller.save()

    def failure_retention(self) -> dict[str, object]:
        """Name exact retained source identity and a possible exposure without certifying it.

        Returns
        -------
        dict[str, object]
            Owner-inspection hold after failed sealing/publication. This is not
            a successful mount observation or permission to retire backing.

        Raises
        ------
        ValueError
            If the campaign has closed its retained descriptors.
        """
        if self._state == "closed":
            raise ValueError("recording campaign is closed")
        original = os.fstat(self._sealed)
        return {
            "source_device": original.st_dev,
            "source_inode": original.st_ino,
            "backing_relative_path": "sealed-recordings",
            "possible_exposure": str(
                Path("/opt/synapse-channel/protected-runtime-qualification-evidence") / self._run_id
            ),
            "state": "unknown; owner must inspect exact dataset bind before backing retirement",
        }

    def close(self) -> None:
        """Stop an interrupted controller and close every owned output/pidfd descriptor.

        Notes
        -----
        This closes handles only. It does not remove failed backing, unmount a
        retained result, stop another process or upgrade a failed collection.
        Further resource access is refused, including after cleanup raises.
        """
        if self._state == "closed":
            return
        self._state = "closed"
        try:
            self._stop_controller()
        finally:
            self._handles.close()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
