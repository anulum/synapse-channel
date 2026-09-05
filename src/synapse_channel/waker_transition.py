# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable active-waker control outcomes
"""Persist control intent before side effects and gate recovery after interruption."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.private_dir import ensure_private_dir
from synapse_channel.core.secret_files import read_secret_file
from synapse_channel.waker_config import clean_waker_text, waker_config_dir, waker_config_path

TransitionState = Literal["idle", "pending", "uncertain"]
"""Observed control state; uncertain requires explicit recovery acknowledgement."""


class WakerTransitionError(SynapseError, ValueError):
    """A previous control operation has not been safely acknowledged."""

    code = "waker_transition"


@dataclass(frozen=True)
class _Record:
    identity: str
    generation: int
    operation: str
    state: str
    pid: int
    start_ticks: str
    boot_id: str
    schema_version: int = 1


def _process_identity(pid: int) -> tuple[str, str]:
    """Read Linux boot and process start identity without trusting a reused PID."""
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    if fields[0] in {"Z", "X"}:
        raise ProcessLookupError("control process has exited")
    return boot, fields[19]


def _path(identity: str, home: Path | None) -> Path:
    return waker_config_path(identity, home=home).with_suffix(".transition.json")


def _read(identity: str, home: Path | None) -> _Record | None:
    path = _path(identity, home)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    document = json.loads(
        read_secret_file(path, flag="waker transition", require_single_link=True, limit=8192)
    )
    if not isinstance(document, dict) or set(document) != set(_Record.__dataclass_fields__):
        raise WakerTransitionError("invalid waker transition fields")
    if document["schema_version"] != 1 or isinstance(document["schema_version"], bool):
        raise WakerTransitionError("unsupported waker transition schema")
    if document["identity"] != identity:
        raise WakerTransitionError("waker transition identity mismatch")
    for key in ("pid", "generation"):
        if type(document[key]) is not int or document[key] < 1:
            raise WakerTransitionError(f"invalid waker transition {key}")
    for key in ("start_ticks", "boot_id", "operation"):
        clean_waker_text(document[key], field=f"transition {key}")
    if document["state"] not in ("pending", "complete", "uncertain"):
        raise WakerTransitionError("invalid waker transition state")
    return _Record(**document)


def _write(record: _Record, home: Path | None) -> None:
    directory = ensure_private_dir(
        waker_config_dir(home=home), parents=True, purpose="waker transition directory"
    )
    fd, name = tempfile.mkstemp(prefix=".transition-", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(record), handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, _path(record.identity, home))
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _state(record: _Record | None) -> TransitionState:
    if record is None or record.state == "complete":
        return "idle"
    if record.state == "pending":
        try:
            boot, start = _process_identity(record.pid)
            if (boot, start) == (record.boot_id, record.start_ticks):
                return "pending"
        except (OSError, ValueError, IndexError):
            pass
    return "uncertain"


def transition_state(
    identity: str, *, home: Path | None = None, generation: int | None = None
) -> TransitionState:
    """Read control state, refusing unreadable, mismatched or orphaned intent.

    Parameters
    ----------
    identity : str
        Exact configured waker identity.
    home : pathlib.Path or None
        Configuration root; defaults to the current user's home.
    generation : int or None
        Reject a mismatched completion. A live intent for the next generation is
        allowed while its configuration replacement is still being persisted.

    Returns
    -------
    str
        idle, pending for a live exact controller, or uncertain. Malformed records
        and unavailable process identity are uncertain, never evidence of readiness.
    """
    try:
        record = _read(identity, home)
        if record is not None and generation is not None and record.generation != generation:
            if record.generation != generation + 1 or _state(record) != "pending":
                return "uncertain"
        return _state(record)
    except (OSError, ValueError):
        return "uncertain"


@dataclass
class WakerTransition:
    """In-memory completion receipt for a durable intent owned under the CLI lock."""

    completed: bool = False

    def complete(self) -> None:
        """Mark successful command completion; persistence occurs on context exit."""
        self.completed = True


@contextmanager
def waker_transition(
    identity: str,
    generation: int,
    operation: str,
    *,
    home: Path | None = None,
    acknowledge_uncertain: bool = False,
) -> Iterator[WakerTransition]:
    """Persist intent under the caller's identity lock before any side effect.

    Parameters
    ----------
    identity : str
        Exact configured identity, already protected by waker_control_lock.
    generation : int
        Configuration generation this operation will write.
    operation : str
        configure (no start), install, stop or resume.
    home : pathlib.Path or None
        Configuration root.
    acknowledge_uncertain : bool
        Explicit operator assertion that an older uncertain operation and any
        manager jobs have settled. Accepted only for resume; never auto-inferred.

    Yields
    ------
    WakerTransition
        Call complete only after every command succeeds. Errors and early returns
        retain uncertainty, including after stopping or configuring an uncertain seat.

    Raises
    ------
    WakerTransitionError
        A live pending operation exists or recovery acknowledgement is missing.
    OSError
        Durable intent could not be persisted; no side effects should follow.
    """
    if (
        operation not in {"configure", "install", "stop", "resume"}
        or type(generation) is not int
        or generation < 1
    ):
        raise WakerTransitionError("invalid waker transition operation or generation")
    if acknowledge_uncertain and operation != "resume":
        raise WakerTransitionError("only resume accepts recovery acknowledgement")
    previous = _read(identity, home)
    state = _state(previous)
    if state == "pending":
        raise WakerTransitionError("previous waker operation is still pending")
    if (
        state == "uncertain"
        and operation not in {"stop", "configure"}
        and not acknowledge_uncertain
    ):
        raise WakerTransitionError(
            "waker recovery required: verify old control processes and systemd jobs "
            "have settled, then resume with --acknowledge-uncertain"
        )
    boot, start = _process_identity(os.getpid())
    retain_uncertainty = state == "uncertain" and operation in {"stop", "configure"}
    record = _Record(
        identity,
        generation,
        operation,
        "uncertain" if retain_uncertainty else "pending",
        os.getpid(),
        start,
        boot,
    )
    _write(record, home)
    receipt = WakerTransition()
    try:
        yield receipt
    except BaseException:
        receipt.completed = False
        raise
    finally:
        resolved = receipt.completed and not retain_uncertainty
        _write(replace(record, state="complete" if resolved else "uncertain"), home)
