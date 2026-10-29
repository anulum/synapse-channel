# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — run one catalogue operation end to end, or say why not
"""Run one command-line catalogue operation along one checked path.

Every run takes the same steps, and each can stop it:

1. **Exact target.** :func:`plan` finds the row for exactly this host, vendor,
   component, version, operating system, transport and variant. It resolves
   the executable and fingerprints its bytes, and admits the row only when the
   catalogue's qualification and that fingerprint agree. The qualification
   gate (:func:`synapse_channel.operations.qualification_bundle.gate`) must
   then pass for the named profile: ``personal`` rests on the operator's
   qualification, ``production`` on a signed, current qualification bundle.
2. **Current authority.** :func:`execute` reads the operator's grants again,
   immediately before the run, and requires every permission the row's effects
   need for this subject and component. A grant withdrawn after planning
   refuses the run, and so does a qualification gate that no longer passes,
   for example a bundle that expired since planning.
3. **Qualified invocation.** The executable is resolved and fingerprinted
   again. A program replaced after planning refuses the run.
4. **Recorded start.** A start record is written and synced before the
   component is invoked. An operation id that has a start is never run again.
5. **External effect.** The component runs without a shell, with a deadline.
6. **Verification or explicit uncertainty.** The outcome record keeps the
   process result apart from the effect. A result verifier
   (:mod:`synapse_channel.result_verification`) can confirm the effect. A run
   interrupted after its start, cut off by its deadline, or whose outcome
   could not be stored, is ``unknown``. :func:`inquire` reports it and can
   verify the effect, but never re-runs it.

Refusals happen before the start record, so a refused run had no effect.
After the start nothing raises a refusal: a failure there is reported in the
run report, because the component may already have had its effect.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import selectors
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from synapse_channel.operations.catalogue import Catalogue
from synapse_channel.operations.qualification_bundle import gate
from synapse_channel.operations.row import OperationRow
from synapse_channel.operations.runner_journal import (
    run_directory,
    run_state,
    write_record,
)
from synapse_channel.result_verification import ResultVerification

MAX_OUTPUT_BYTES = 1 << 20
"""Largest part of the component's standard output a run keeps and returns."""

DRAIN_GRACE_SECONDS = 1.0
"""How long output is still read after the component exits, if something still holds it."""

AUTHORITY_SOURCE = "local operator grants file"
"""Where a run's authority comes from: not a hub grant and not an authenticated seat."""

Verifier = Callable[[str, str], ResultVerification]
"""Check the effect of a run: called with the operation id and its process state."""


class OperationRunRefused(ValueError):
    """The run was refused before it started; nothing was invoked."""


@dataclass(frozen=True, slots=True)
class OperationTarget:
    """Exactly which row to run."""

    operation: str
    host: str
    vendor: str
    component: str
    component_version: str
    os: str
    transport: str = "cli"
    variant: str = "default"


@dataclass(frozen=True, slots=True)
class RunProfile:
    """The qualification profile a run must pass, with what production needs."""

    name: str
    envelope: Mapping[str, Any] | None = None
    trusted_keys: Mapping[str, bytes] = field(default_factory=dict)
    required_environment: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunPlan:
    """A row admitted for one exact executable and profile, with its command line."""

    row: OperationRow
    argv: tuple[str, ...]
    executable: str
    fingerprint: str
    profile: RunProfile
    basis: str


@dataclass(frozen=True, slots=True)
class RunReport:
    """What one run, refusal or inquiry established."""

    operation_id: str
    state: str
    observation: str
    returncode: int | None = None
    output: str = ""
    verification: ResultVerification | None = None
    verification_error: str = ""
    output_truncated: bool = False
    output_complete: bool = False
    output_error: str = ""
    authority: str = ""

    def to_json(self) -> dict[str, object]:
        """Return the report as plain data; the process state is not the effect."""
        verification = None if self.verification is None else self.verification.to_json()
        effect = "unknown" if self.verification is None else self.verification.effect
        return {
            "operation_id": self.operation_id,
            "state": self.state,
            "observation": self.observation,
            "returncode": self.returncode,
            "output": self.output,
            "external_effect": effect,
            "verification": verification,
            "verification_error": self.verification_error,
            "output_truncated": self.output_truncated,
            "output_complete": self.output_complete,
            "output_error": self.output_error,
            "authority": self.authority,
            "automatic_retry": False,
        }


def fingerprint_executable(name: str) -> tuple[str, str]:
    """Resolve ``name`` on ``PATH`` and return its real path and ``sha256:`` digest.

    Raises
    ------
    OperationRunRefused
        If the executable cannot be found or read.
    """
    found = shutil.which(name)
    if found is None:
        raise OperationRunRefused(f"the executable {name} is not found")
    real = os.path.realpath(found)
    digest = hashlib.sha256()
    try:
        with open(real, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise OperationRunRefused(f"the executable {name} cannot be read") from exc
    return real, "sha256:" + digest.hexdigest()


def _gate_basis(row: OperationRow, profile: RunProfile, now: float) -> str:
    """Return the basis on which ``row`` may run under ``profile`` now, or refuse."""
    try:
        verdict = gate(
            row,
            profile=profile.name,
            now=int(now),
            envelope=profile.envelope,
            trusted_keys=profile.trusted_keys,
            required_environment=profile.required_environment,
        )
    except ValueError as exc:
        raise OperationRunRefused(str(exc)) from exc
    if not verdict.runnable:
        raise OperationRunRefused(
            f"{row.operation} may not run under {profile.name}: " + "; ".join(verdict.reasons)
        )
    return verdict.basis


def plan(
    catalogue: Catalogue,
    target: OperationTarget,
    arguments: Mapping[str, object],
    *,
    profile: RunProfile,
    clock: Callable[[], float] = time.time,
) -> RunPlan:
    """Admit the exact row for the executable found now and build its command line.

    Raises
    ------
    OperationRunRefused
        If the row is not admitted for this executable, needs a person's
        authority, can print credentials, the arguments are refused, or the
        qualification gate refuses it under ``profile``.
    """
    row = catalogue.find(
        target.operation,
        host=target.host,
        vendor=target.vendor,
        component=target.component,
        component_version=target.component_version,
        os=target.os,
        transport=target.transport,
        variant=target.variant,
    )
    if row is None or row.transport != "cli":
        raise OperationRunRefused(f"{target.operation} has no command-line row for this target")
    executable, fingerprint = fingerprint_executable(row.binding.executable)
    try:
        admitted = catalogue.admit(
            target.operation,
            host=target.host,
            vendor=target.vendor,
            component=target.component,
            component_version=target.component_version,
            os=target.os,
            transport=target.transport,
            variant=target.variant,
            fingerprint=fingerprint,
        )
        argv = admitted.argv(arguments)
    except (LookupError, ValueError) as exc:
        raise OperationRunRefused(str(exc)) from exc
    if admitted.needs_human_principal:
        raise OperationRunRefused(f"{target.operation} needs a person's authority, not a seat's")
    if "credential_bearing" in admitted.disclosures:
        raise OperationRunRefused(
            f"{target.operation} can print credentials; the runner refuses it"
        )
    basis = _gate_basis(admitted, profile, clock())
    return RunPlan(admitted, (executable, *argv[1:]), executable, fingerprint, profile, basis)


def load_grants(path: Path) -> list[dict[str, Any]]:
    """Read the operator's grants file, refusing one that others may change.

    The file is ``{"grants": [{"subject", "permissions", "component",
    "expires_at"}]}`` and must be a regular file owned by this user and not
    writable by group or others.

    Raises
    ------
    OperationRunRefused
        If the file is unsafe, unreadable or malformed.
    """
    try:
        info = os.lstat(path)
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperationRunRefused("the grants file cannot be read") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise OperationRunRefused("the grants file must be this user's, writable by no one else")
    try:
        grants = json.loads(text)["grants"]
    except (ValueError, KeyError, TypeError) as exc:
        raise OperationRunRefused('the grants file is not {"grants": [...]}') from exc
    if not isinstance(grants, list) or not all(isinstance(grant, dict) for grant in grants):
        raise OperationRunRefused('the grants file is not {"grants": [...]}')
    return grants


def authorised(
    grants: list[dict[str, Any]], *, subject: str, row: OperationRow, now: float
) -> bool:
    """Return whether unexpired grants give ``subject`` every permission ``row`` needs."""
    held: set[str] = set()
    for grant in grants:
        expires = grant.get("expires_at")
        if (
            grant.get("subject") == subject
            and grant.get("component") in {row.component, "*"}
            and isinstance(expires, int | float)
            and now < expires
            and isinstance(grant.get("permissions"), list)
        ):
            held.update(str(permission) for permission in grant["permissions"])
    return set(row.required_authorities) <= held


def _decode(output: bytes) -> str:
    """Return the kept output as text, replacing bytes that are not UTF-8."""
    return output[:MAX_OUTPUT_BYTES].decode("utf-8", "replace")


def execute(
    run: RunPlan,
    *,
    operation_id: str,
    subject: str,
    grants_path: Path,
    journal: Path,
    timeout: float,
    verifier: Verifier | None = None,
    clock: Callable[[], float] = time.time,
) -> RunReport:
    """Re-check authority and the program, record the start, run, then record the outcome.

    Raises
    ------
    OperationRunRefused
        If authority was withdrawn, the qualification gate no longer passes, the
        program was replaced, the id was already used, or the deadline is not
        positive. Nothing was invoked.
    """
    directory = run_directory(journal, operation_id)
    if not 0.0 < timeout < float("inf"):
        raise OperationRunRefused("the deadline is a positive, finite number of seconds")
    if run_state(journal, operation_id)[0] != "absent":
        raise OperationRunRefused(f"{operation_id} already started; inquire, never re-run")
    if not authorised(load_grants(grants_path), subject=subject, row=run.row, now=clock()):
        raise OperationRunRefused(f"{subject} holds no current grant for {run.row.operation}")
    basis = _gate_basis(run.row, run.profile, clock())
    executable, fingerprint = fingerprint_executable(run.row.binding.executable)
    if (executable, fingerprint) != (run.executable, run.fingerprint):
        raise OperationRunRefused("the program was replaced after the run was planned")
    authority = f"{AUTHORITY_SOURCE} {grants_path}"
    try:
        write_record(
            directory,
            "start.json",
            {
                "operation_id": operation_id,
                "operation": run.row.operation,
                "row": list(run.row.key),
                "subject": subject,
                "authority": authority,
                "fingerprint": fingerprint,
                "profile": run.profile.name,
                "basis": basis,
                "argv_sha256": hashlib.sha256(json.dumps(run.argv).encode()).hexdigest(),
                "started_at": clock(),
            },
        )
    except FileExistsError as exc:
        raise OperationRunRefused(f"{operation_id} already started; inquire, never re-run") from exc
    except OSError as exc:
        raise OperationRunRefused(
            f"the start of {operation_id} could not be recorded; nothing was invoked"
        ) from exc
    captured = _run_bounded(run.argv, timeout)
    return _record_outcome(directory, operation_id, captured, authority, verifier, clock)


@dataclass(frozen=True, slots=True)
class _Captured:
    """What the component did, with at most :data:`MAX_OUTPUT_BYTES` of its output kept."""

    state: str
    observation: str
    returncode: int | None = None
    output: bytes = b""
    output_sha256: str = hashlib.sha256(b"").hexdigest()
    truncated: bool = False
    complete: bool = False
    output_error: str = ""


def _run_bounded(argv: tuple[str, ...], timeout: float) -> _Captured:
    """Run ``argv`` without a shell and read its output without a helper thread.

    Standard input and standard error are not connected. Standard output is
    read without blocking until end of file: the first :data:`MAX_OUTPUT_BYTES`
    are kept and every byte read is hashed. After the component exits, its
    output is read for at most :data:`DRAIN_GRACE_SECONDS` more; a stream that
    something else still holds open is then closed, and the output is marked
    incomplete. The pipe is always closed before this returns, so nothing of a
    run outlives it.
    """
    try:
        process = subprocess.Popen(  # noqa: S603 - an admitted argv, never a shell
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return _Captured("not_started", "component_could_not_start")
    stream = cast(io.BufferedReader, process.stdout)  # set by stdout=PIPE
    output = _Output()
    deadline = time.monotonic() + timeout
    try:
        output.drain(stream.fileno(), process, deadline)
    except Exception as exc:
        # The component is running and may have had its effect: whatever went
        # wrong while reading, it is waited for and its outcome is recorded.
        output.error = f"the output could not be read: {type(exc).__name__}"
    finally:
        stream.close()
    try:
        returncode = process.wait(timeout=max(deadline - time.monotonic(), 0.0))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        return output.captured("unknown", "deadline_passed", None)
    if returncode == 0:
        return output.captured("confirmed_done", "component_exited_zero", returncode)
    return output.captured("confirmed_failed", "component_exited_nonzero", returncode)


class _Output:
    """The kept part of a component's output, the digest of all of it, and how it ended."""

    def __init__(self) -> None:
        """Start with nothing kept, an empty digest and no error."""
        self.kept = bytearray()
        self.digest = hashlib.sha256()
        self.overflow = False
        self.complete = False
        self.error = ""

    def take(self, chunk: bytes) -> None:
        """Hash ``chunk`` and keep what still fits."""
        self.digest.update(chunk)
        room = MAX_OUTPUT_BYTES - len(self.kept)
        self.kept.extend(chunk[: max(room, 0)])
        self.overflow = self.overflow or len(chunk) > room

    def drain(self, descriptor: int, process: subprocess.Popen[bytes], deadline: float) -> None:
        """Read until end of file, the deadline, or the grace after the component exits."""
        os.set_blocking(descriptor, False)
        until = deadline
        exited = False
        with selectors.DefaultSelector() as selector:
            selector.register(descriptor, selectors.EVENT_READ)
            while (remaining := until - time.monotonic()) > 0:
                if selector.select(min(remaining, 0.05)):
                    chunk = os.read(descriptor, 1 << 16)
                    if not chunk:
                        self.complete = True
                        return
                    self.take(chunk)
                if not exited and process.poll() is not None:
                    exited = True
                    until = min(deadline, time.monotonic() + DRAIN_GRACE_SECONDS)

    def captured(self, state: str, observation: str, returncode: int | None) -> _Captured:
        """Return the run's result with this output."""
        return _Captured(
            state,
            observation,
            returncode,
            bytes(self.kept),
            self.digest.hexdigest(),
            self.overflow,
            self.complete,
            self.error,
        )


def _record_outcome(
    directory: Path,
    operation_id: str,
    captured: _Captured,
    authority: str,
    verifier: Verifier | None,
    clock: Callable[[], float],
) -> RunReport:
    """Write the outcome record and attach a verification when one is configured.

    The component has already run, so nothing here raises. An outcome that
    cannot be stored leaves the journal with a start and no outcome, and the
    report says ``unknown`` like :func:`inquire` will.
    """
    state, observation = captured.state, captured.observation
    try:
        write_record(
            directory,
            "outcome.json",
            {
                "operation_id": operation_id,
                "state": state,
                "observation": observation,
                "returncode": captured.returncode,
                "output_sha256": captured.output_sha256,
                "output_truncated": captured.truncated,
                "output_complete": captured.complete,
                "output_error": captured.output_error,
                "authority": authority,
                "recorded_at": clock(),
            },
        )
    except OSError:
        state, observation = "unknown", "outcome_not_recorded"
    verification, error = _verify(verifier, operation_id, state)
    return RunReport(
        operation_id,
        state,
        observation,
        captured.returncode,
        _decode(captured.output),
        verification,
        error,
        captured.truncated,
        captured.complete,
        captured.output_error,
        authority,
    )


def _verify(
    verifier: Verifier | None, operation_id: str, state: str
) -> tuple[ResultVerification | None, str]:
    """Run the verifier; a verifier that fails leaves the result unverified, with the reason."""
    if verifier is None:
        return None, ""
    try:
        return verifier(operation_id, state), ""
    except (OSError, ValueError) as exc:
        return None, f"the verifier failed: {type(exc).__name__}"


def inquire(journal: Path, operation_id: str, *, verifier: Verifier | None = None) -> RunReport:
    """Report what the journal knows about a run, and verify its effect; never re-run it.

    Returns
    -------
    RunReport
        ``absent`` when nothing started; ``unknown`` with ``start_without_outcome``
        for an interrupted start; otherwise the recorded outcome. A verifier,
        when given, checks the effect of whatever was recorded.
    """
    state, record = run_state(journal, operation_id)
    if state == "absent" or record is None:
        return RunReport(operation_id, "absent", "no_start_recorded")
    interrupted = "state" not in record  # a start record; every outcome names its state
    returncode = None if interrupted else record.get("returncode")
    verification, error = _verify(verifier, operation_id, state)
    return RunReport(
        operation_id,
        state,
        "start_without_outcome" if interrupted else str(record.get("observation", "")),
        returncode if isinstance(returncode, int) else None,
        "",
        verification,
        error,
        bool(record.get("output_truncated", False)),
        bool(record.get("output_complete", False)),
        str(record.get("output_error", "")),
        str(record.get("authority", "")),
    )
