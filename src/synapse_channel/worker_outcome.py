# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — worker process outcome observations
"""Report native process boundaries without inferring business effects or tree-wide stop."""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from synapse_channel.core.secure_path import (
    assert_owner_only_file_path,
    read_owner_only_file_bytes,
)
from synapse_channel.durable_dir import private_dir, sync_directory
from synapse_channel.execution_outcome import OUTCOME_SCHEMA, ExecutionOutcome, OutcomeState
from synapse_channel.launch_record import EVIDENCE_CLASS, process_identity

MAX_REPORT_BYTES = 65536
"""Bound for a single owner-local outcome snapshot, without argv or returned content."""

OPERATION_ID_ENV = "SYN_OPERATION_ID"
"""Environment variable that names the provider's operation to the provider itself.

``synapse worker-session`` sets it to the operation id its outcome report carries,
so the provider can declare that operation on its claimed task before the effect.
"""


class WorkerExit(int):
    """Retain the provider/attachment exit code and an independently scoped observation."""

    outcome: ExecutionOutcome

    def __new__(cls, returncode: int, outcome: ExecutionOutcome) -> WorkerExit:
        """Keep legacy integer semantics for callers and operating-system exit."""
        result = super().__new__(cls, returncode)
        result.outcome = outcome
        return result


class WorkerObservation:
    """Own one launch observation and an optional exclusive private report file.

    The record is a same-account runtime observation, not an authenticated
    isolation profile or an execution grant. No argv, credential, prompt or
    returned content is retained. Existing report files are never adopted.
    """

    def __init__(self, report_file: Path | None = None) -> None:
        """Allocate one operation before any provider or sidecar is started."""
        self.path = report_file.absolute() if report_file is not None else None
        self.operation_id = "worker-" + uuid.uuid4().hex
        self.owner = process_identity(os.getpid()) if self.path is not None else None
        self.outcome = ExecutionOutcome(
            self.operation_id,
            "worker_process",
            OutcomeState.NOT_STARTED,
            "provider_not_invoked",
            external_effect=OutcomeState.NOT_STARTED,
        )
        self.returncode: int | None = None
        self.published: bytes | None = None
        # Descriptors of every report inode this launch published, with the bytes written
        # to each: a writer that opened the path before a replace lands in a detached
        # inode, which only these descriptors can still see. A launch publishes a handful
        # of updates, so all of them are held until finish or refusal.
        self._held: list[tuple[int, bytes]] = []
        self.publish()

    def observe(
        self,
        state: OutcomeState,
        observation: str,
        *,
        stop_requested: bool = False,
        stop_confirmed: bool = False,
        returncode: int | None = None,
    ) -> None:
        """Publish exact local lifecycle facts without claiming effects were undone."""
        self.outcome = ExecutionOutcome(
            self.operation_id,
            "worker_process",
            state,
            observation,
            external_effect=(
                OutcomeState.NOT_STARTED
                if state is OutcomeState.NOT_STARTED
                else OutcomeState.UNKNOWN
            ),
            stop_requested=stop_requested,
            stop_confirmed=stop_confirmed,
        )
        self.returncode = returncode
        self.publish()

    def finish(self, returncode: int, *, tmux: bool = False) -> WorkerExit:
        """Distinguish a provider exit from an attachment whose worker outcome is unknown."""
        if tmux:
            state, observation = OutcomeState.UNKNOWN, "tmux_attachment_ended"
        else:
            state = (
                OutcomeState.CONFIRMED_DONE if returncode == 0 else OutcomeState.CONFIRMED_FAILED
            )
            observation = "provider_process_exited"
        try:
            self.observe(state, observation, returncode=returncode)
        finally:
            self.close()
        return WorkerExit(returncode, self.outcome)

    def tmux_result(self, launch: Callable[[], int]) -> WorkerExit:
        """Observe actual tmux invocation and keep exceptions or attachment exit unknown."""
        self.observe(OutcomeState.STARTED, "tmux_launch_invoked")
        try:
            code = launch()
        except BaseException:
            self.observe(OutcomeState.UNKNOWN, "tmux_exchange_unconfirmed")
            raise
        return self.finish(code, tmux=True)

    def publish(self) -> None:
        """Create exclusively, then replace only the previous bytes of this operation.

        A record changed by anyone else is never overwritten: the update is refused
        with ``ValueError`` and the changed bytes stay at the path. A write that races
        a replace and lands in the detached inode is put back at the path, so the next
        update refuses it exactly as if it had landed a moment later.
        """
        if self.path is None:
            return
        assert self.owner is not None
        private_dir(self.path.parent, purpose="worker outcome directory")
        payload = {
            **self.outcome.to_json(),
            "recorded_at": time.time(),
            "evidence_class": EVIDENCE_CLASS,
            "launcher": {
                "pid": self.owner.pid,
                "boot": self.owner.boot,
                "started": self.owner.started,
            },
            "returncode": self.returncode,
        }
        data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
        if self.published is None:
            self._hold(self._create(data), data)
        else:
            self._restore_raced_write()
            previous = read_owner_only_file_bytes(
                self.path, purpose="worker outcome", max_bytes=MAX_REPORT_BYTES
            )
            if previous != self.published:
                self.close()
                raise ValueError("worker outcome record changed; inquire before continuing")
            self._hold(self._replace(data), data)
        self.published = data

    def close(self) -> None:
        """Release the held report descriptors once no further update will follow."""
        while self._held:
            os.close(self._held.pop()[0])

    def _create(self, data: bytes) -> int | None:
        """Write the first record into a new exclusive file; keep its descriptor on POSIX."""
        assert self.path is not None
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            assert_owner_only_file_path(self.path, purpose="worker outcome")
            _write_durably(descriptor, data)
        except BaseException:
            os.close(descriptor)
            raise
        sync_directory(self.path.parent)
        return _kept(descriptor)

    def _replace(self, data: bytes) -> int | None:
        """Atomically replace the record; keep the new inode's descriptor on POSIX."""
        assert self.path is not None
        descriptor, temporary = tempfile.mkstemp(prefix=".worker-outcome-", dir=self.path.parent)
        temporary_path = Path(temporary)
        open_descriptor = True
        try:
            assert_owner_only_file_path(temporary_path, purpose="worker outcome")
            _write_durably(descriptor, data)
            kept = _kept(descriptor)
            open_descriptor = kept is not None
            os.replace(temporary_path, self.path)
        except BaseException:
            if open_descriptor:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise
        sync_directory(self.path.parent)
        return kept

    def _hold(self, descriptor: int | None, data: bytes) -> None:
        """Track the inode now at the path alongside every inode it replaced."""
        if descriptor is not None:
            self._held.append((descriptor, data))

    def _restore_raced_write(self) -> None:
        """Put a write that landed in the detached inode back at the path.

        Only when the path still holds this observation's own latest bytes: a change
        made at the path itself is already visible and is never overwritten.
        """
        assert self.path is not None
        changed = [
            current
            for detached, written in self._held[:-1]
            if (current := _read_bounded(detached)) != written
        ]
        if not changed:
            return
        at_path = read_owner_only_file_bytes(
            self.path, purpose="worker outcome", max_bytes=MAX_REPORT_BYTES
        )
        if at_path == self._held[-1][1]:
            self._hold(self._replace(changed[0]), changed[0])


def _kept(descriptor: int) -> int | None:
    """Keep a report descriptor on POSIX; close it elsewhere.

    Windows cannot rename over a file that another handle holds open, and there a
    concurrent writer's open handle already makes the replace fail.
    """
    if os.name == "posix":
        return descriptor
    os.close(descriptor)
    return None


def _write_durably(descriptor: int, data: bytes) -> None:
    """Write all of ``data`` at the start of ``descriptor`` and flush it to disk."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    view = memoryview(data)
    while view:
        view = view[os.write(descriptor, view) :]
    os.fsync(descriptor)


def _read_bounded(descriptor: int) -> bytes:
    """Read a held report inode from its start, one byte past the report bound."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = MAX_REPORT_BYTES + 1
    while remaining and (chunk := os.read(descriptor, remaining)):
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _string(document: dict[str, Any], key: str) -> str:
    """Read a required string without echoing rejected file content."""
    value = document.get(key)
    if not isinstance(value, str):
        raise ValueError("invalid worker outcome text")
    return value


def _flag(document: dict[str, Any], key: str) -> bool:
    """Read a required boolean without accepting integer stand-ins."""
    value = document.get(key)
    if not isinstance(value, bool):
        raise ValueError("invalid worker outcome stop/undo fact")
    return value


def _state(value: object) -> OutcomeState:
    """Read a closed state and keep arbitrary invalid content out of errors."""
    if not isinstance(value, str):
        raise ValueError("invalid worker outcome state")
    try:
        return OutcomeState(value)
    except ValueError:
        raise ValueError("invalid worker outcome state") from None


def _launcher(document: dict[str, Any]) -> tuple[int, str, str]:
    """Validate and project only the recorded birth reference, never extra fields."""
    value = document.get("launcher")
    if not isinstance(value, dict):
        raise ValueError("invalid worker launcher reference")
    pid, boot, started = value.get("pid"), value.get("boot"), value.get("started")
    if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid < 2**32:
        raise ValueError("invalid worker launcher PID")
    if not isinstance(boot, str) or not isinstance(started, str):
        raise ValueError("invalid worker launcher birth reference")
    if boot:
        try:
            valid = str(uuid.UUID(boot)) == boot
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("invalid worker launcher boot reference")
    if started and (len(started) > 32 or not started.isascii() or not started.isdecimal()):
        raise ValueError("invalid worker launcher start reference")
    return pid, boot, started


def _document_outcome(document: dict[str, Any]) -> tuple[ExecutionOutcome, int | None]:
    """Decode the known report envelope without granting authority to its contents."""
    if document.get("schema") != OUTCOME_SCHEMA or document.get("boundary") != "worker_process":
        raise ValueError("unsupported worker outcome report")
    state = _state(document.get("state"))
    outcome = ExecutionOutcome(
        _string(document, "operation_id"),
        "worker_process",
        state,
        _string(document, "observation"),
        external_effect=_state(document.get("external_effect")),
        stop_requested=_flag(document, "stop_requested"),
        stop_confirmed=_flag(document, "stop_confirmed"),
        effects_undone=_flag(document, "effects_undone"),
    )
    code = document.get("returncode")
    if code is not None and (
        not isinstance(code, int) or isinstance(code, bool) or not -(2**31) <= code < 2**32
    ):
        raise ValueError("invalid worker outcome return code")
    if state is OutcomeState.CONFIRMED_DONE and code != 0:
        raise ValueError("inconsistent worker process completion")
    if state is OutcomeState.CONFIRMED_FAILED and (code is None or code == 0):
        raise ValueError("inconsistent worker process failure")
    return outcome, code


def read_worker_outcome(path: Path) -> dict[str, object]:
    """Read an owner-local report; a departed/unknown launcher leaves inflight work unknown.

    The birth reference is an observation of one launcher, not the provider's
    identity, a whole process tree or an isolation boundary. Terminal outcomes
    remain historical launcher declarations. Reading never starts, kills or
    retries work, and does not upgrade a declaration into independent authority.
    """
    raw = read_owner_only_file_bytes(path, purpose="worker outcome", max_bytes=MAX_REPORT_BYTES)
    try:
        document: Any = json.loads(raw)
    except ValueError:
        raise ValueError("invalid worker outcome document") from None
    if not isinstance(document, dict) or document.get("evidence_class") != EVIDENCE_CLASS:
        raise ValueError("invalid worker outcome document")
    outcome, code = _document_outcome(document)
    recorded = document.get("recorded_at")
    if (
        not isinstance(recorded, (int, float))
        or isinstance(recorded, bool)
        or not 0 < recorded < 253402300800
    ):
        raise ValueError("invalid worker outcome observation time")
    pid, boot, started = _launcher(document)
    if outcome.state is OutcomeState.STARTED:
        current = process_identity(pid)
        same = (
            bool(started)
            and current.boot == boot
            and current.started == started
            and not current.unavailable
        )
        if not same:
            outcome = ExecutionOutcome(
                outcome.operation_id,
                "worker_process",
                OutcomeState.UNKNOWN,
                "launcher_unconfirmed",
            )
    return {
        **outcome.to_json(),
        "recorded_at": recorded,
        "evidence_class": EVIDENCE_CLASS,
        "launcher": {"pid": pid, "boot": boot, "started": started},
        "returncode": code,
    }
