# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — protected execution driver regressions
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

import test_protected_write_preparation as preparation_fixtures
from synapse_channel.core.persistence import EventStore, OperationCommitResult
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_execution_observations import (
    verify_protected_execution_final_state,
    verify_protected_execution_observations,
)
from synapse_channel.core.protected_write_file_execution import fsync_retained_protected_object
from synapse_channel.core.protected_write_inspection import inspect_protected_file
from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation
from synapse_channel.core.protected_write_preparation import verify_prepared_begin
from test_protected_write_admission import CONTEXT
from test_protected_write_content import CONTENT
from test_protected_write_effects import _plan
from test_protected_write_inspection import _inspect
from test_protected_write_preparation import _begun

pytestmark = pytest.mark.skipif(os.name != "posix", reason="real protected POSIX driver")


@pytest.mark.parametrize("crash_point", ["before_effect", "after_effect"])
def test_owned_writer_process_exit_preserves_started_record_and_authority_custody(
    tmp_path: Path,
    crash_point: str,
) -> None:
    import subprocess
    import sys

    from test_protected_write_admission_journal import POLICY
    from test_protected_write_transition_journal import _state

    script = r"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_protected_write_preparation import _begun
from test_protected_write_admission import CONTEXT
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_driver import execute_prepared_protected_write
from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
from synapse_channel.core.protected_write_preparation import verify_prepared_begin
path, point = Path(sys.argv[1]), sys.argv[2]
authority, prepared, state = _begun(path)
service = EventStore(path / "crashed-service.db")
evidence = ProtectedExecutionEvidenceStore(
    service, "text", prepared.policy.limits.operation_limits, 4096, 32,
)
root = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
checks = 0
def current():
    global checks
    checks += 1
    if point == "before_effect" and checks == 3:
        os._exit(73)
    return verify_prepared_begin(
        prepared, state=state, store=authority,
        authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
        # Unit-only positive policy callback; activation needs actor-consistent current begin and
        # trust lookup.
        authorize_current=lambda _p, _r: True,
    )
def store(content):
    if point == "after_effect":
        os._exit(73)
    return evidence.write(content)
execute_prepared_protected_write(
    prepared, root_descriptors={"memory": root, "parent": root},
    owner_uid=os.getuid(), service_journal=service, max_journal_operations=32,
    max_locks=8, max_evidence_bytes=4096, verify_current=current,
    # Unit-only positive isolation callback; activation needs a supervised separate-principal
    # writer and real namespace checks.
    verify_isolation=lambda: True, store_evidence=store, read_evidence=evidence.read,
)
raise RuntimeError("crash point was not reached")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), crash_point],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 73, completed.stderr
    authority = EventStore(tmp_path / "prepared-begin.db")
    service = EventStore(tmp_path / "crashed-service.db")
    try:
        state = _state(authority, POLICY)
        assert state.protected_write_reservations["reservation"].holds_custody
        assert "reservation" in state.protected_claim_custody
        records = service.read_operations()
        assert len(records) == 2
        assert records[0].response["state"] == "execution_consumed"
        assert records[1].response["phase"] == "started"
        assert records[0].request_digest is not None
        replayed = service.commit_operation(
            operation_key=records[0].key,
            request_digest=records[0].request_digest,
            response=records[0].response,
            events=(("not-inserted", {}),),
            intent={},
        )
        assert replayed.outcome == "replayed"
        assert len(service.read_operations()) == 2
    finally:
        service.close()
        authority.close()
    target = tmp_path / "records/note.md"
    assert target.exists() == (crash_point == "after_effect")
    if target.exists():
        assert target.read_bytes() == CONTENT


@pytest.mark.parametrize(
    "revocation_point", ["before_consumption", "before_effect", "after_effect"]
)
def test_durable_revocation_stops_writer_and_retains_custody_after_both_reopens(
    tmp_path: Path,
    revocation_point: str,
) -> None:
    from test_protected_write_admission_journal import POLICY
    from test_protected_write_transition_journal import _pending, _state

    authority, prepared, state = _begun(tmp_path)
    service_path = tmp_path / "revoked-service.db"
    service = EventStore(service_path)
    evidence = ProtectedExecutionEvidenceStore(
        service, "text", prepared.policy.limits.operation_limits, 4096, 32
    )
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    checks = 0

    def current() -> ProtectedWriteReservation:
        nonlocal checks
        checks += 1
        if (revocation_point == "before_consumption" and checks == 1) or (
            revocation_point == "before_effect" and checks == 3
        ):
            _pending(authority, state)
        return verify_prepared_begin(
            prepared,
            state=state,
            store=authority,
            authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
            # Unit-only positive policy callback; activation needs actor-consistent current begin
            # and trust lookup.
            authorize_current=lambda _p, _r: True,
        )

    def store(content: bytes) -> Mapping[str, object]:
        reference = evidence.write(content)
        if revocation_point == "after_effect":
            _pending(authority, state)
        return reference

    try:
        with pytest.raises(ValueError):
            execute_prepared_protected_write(
                prepared,
                root_descriptors={"memory": root, "parent": root},
                owner_uid=os.getuid(),
                service_journal=service,
                max_journal_operations=32,
                max_locks=8,
                max_evidence_bytes=4096,
                verify_current=current,
                # Unit-only positive isolation callback; activation needs a supervised
                # separate-principal writer and real namespace checks.
                verify_isolation=lambda: True,
                store_evidence=store,
                read_evidence=evidence.read,
            )
        reservation = state.protected_write_reservations["reservation"]
        assert reservation.holds_custody
        assert json.loads(reservation.result_bytes)["body"]["revocation_phase"] == "requested"
        assert "reservation" in state.protected_claim_custody
        records = service.read_operations()
        if revocation_point == "before_consumption":
            assert not records
        elif revocation_point == "before_effect":
            assert len(records) == 3
            assert records[-1].response["phase"] == "unknown"
        else:
            assert len(records) == 4
            assert records[-1].response["phase"] == "completed"
            assert records[-1].response["step_index"] == 0
        target = tmp_path / "records/note.md"
        assert target.exists() == (revocation_point == "after_effect")
        if target.exists():
            assert target.read_bytes() == CONTENT
        expected_custody = dict(state.protected_claim_custody)
    finally:
        os.close(root)
        service.close()
        authority.close()

    reopened_authority = EventStore(tmp_path / "prepared-begin.db")
    reopened_service = EventStore(service_path)
    try:
        restored = _state(reopened_authority, replace(POLICY, limits=prepared.policy.limits))
        assert restored.protected_claim_custody == expected_custody
        assert restored.protected_write_reservations["reservation"] == reservation
        assert reopened_service.read_operations() == records
        with pytest.raises(ValueError):
            verify_prepared_begin(
                prepared,
                state=restored,
                store=reopened_authority,
                authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
                # Unit-only positive policy callback; activation needs actor-consistent current
                # begin and trust lookup.
                authorize_current=lambda _p, _r: True,
            )
    finally:
        reopened_service.close()
        reopened_authority.close()


@pytest.mark.parametrize("run", ["durable_all"], indirect=True)
def test_all_opcodes_retain_evidence_after_service_database_close(
    run: tuple[Callable[..., Any], EventStore],
    tmp_path: Path,
) -> None:
    from synapse_channel.core.protected_write_evidence_store import ProtectedExecutionEvidenceStore
    from test_protected_write_proposal import LIMITS

    execute, journal = run
    results = execute()
    references = [item.operation.response["evidence_reference"] for item in results[1:]]
    assert len(references) == 10
    assert len(journal.read_operations()) == 31
    journal.close()
    reopened = EventStore(tmp_path / "driver.db")
    try:
        reader = ProtectedExecutionEvidenceStore(
            reopened, "text", LIMITS.operation_limits, 4096, 32
        )
        for index, reference in enumerate(references):
            content = reader.read(reference, 4096)
            assert hashlib.sha256(content).hexdigest() == reference["sha256"]
            assert json.loads(content)["step_index"] == index
        assert len(reopened.read_operations()) == 31
    finally:
        reopened.close()
    assert (tmp_path / "records/note.md").read_bytes() == CONTENT


def test_readback_probe_detects_valid_prefix_with_appended_bytes(
    run: tuple[Callable[..., Any], EventStore],
) -> None:
    execute, service = run
    stored = b""

    def write(content: bytes) -> Mapping[str, object]:
        nonlocal stored
        stored = content + b"unexpected-tail"
        return {
            "domain": "text",
            "handle": "corrupted",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def read(reference: Mapping[str, object], limit: int) -> bytes:
        assert limit == cast(int, reference["size_bytes"]) + 1
        return stored[:limit]

    with pytest.raises(ValueError, match="readback mismatch"):
        execute(store_evidence=write, read_evidence=read)
    assert service.read_operations()[-1].response["phase"] == "unknown"


@pytest.mark.parametrize("failure", ["missing", "changed", "mutable", "oversized"])
def test_readback_failure_keeps_partial_result_unknown(
    run: tuple[Callable[..., Any], EventStore],
    tmp_path: Path,
    failure: str,
) -> None:
    execute, service = run

    def read(reference: Mapping[str, object], limit: int) -> bytes:
        assert 0 < limit <= 4096
        with pytest.raises(TypeError):
            cast(dict[str, object], reference)["handle"] = "redirected"
        if failure == "missing":
            raise FileNotFoundError("evidence was not retained")
        if failure == "mutable":
            return cast(bytes, bytearray())
        return b"x" * (limit + 1 if failure == "oversized" else limit)

    with pytest.raises((ValueError, FileNotFoundError)):
        execute(read_evidence=read)
    assert (tmp_path / "records/note.md").read_bytes() == CONTENT
    records = service.read_operations()
    assert len(records) == 3
    assert records[-1].response["phase"] == "unknown"
    assert execute()[0].outcome == "replayed"


def test_corrupt_prepared_digest_fails_before_consumption(
    run: tuple[Callable[..., Any], EventStore],
) -> None:
    execute, service = run
    with pytest.raises(ValueError, match="digest mismatch"):
        execute(corrupt_digest=True)
    assert not service.read_operations()


@pytest.mark.parametrize("change", ["parent", "after_bytes", "after_identity"])
def test_real_mid_execution_object_change_stops_chain(
    run: tuple[Callable[..., Any], EventStore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    from synapse_channel.core import protected_write_driver as driver

    execute, service = run
    original_observe = inspect_protected_file
    original_fsync = fsync_retained_protected_object
    calls = 0

    def observe(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        if args[1] == "records/note.md":
            calls += 1
            if change == "after_bytes" and calls == 2:
                (tmp_path / "records/note.md").write_bytes(b"external change")
        result = original_observe(*args, **kwargs)
        if change == "parent" and args[1] == "records/note.md" and calls == 1:
            (tmp_path / "records").rename(tmp_path / "old-records")
            (tmp_path / "records").mkdir(mode=0o700)
        return result

    def fsync(target: Any) -> None:
        original_fsync(target)
        if change == "after_identity":
            path = tmp_path / "records/note.md"
            path.rename(path.with_name("old-note"))
            path.write_bytes(CONTENT)
            path.chmod(0o600)

    # Injected observation mismatch checks stopping; activation needs a real competing path
    # mutation.
    monkeypatch.setattr(driver, "inspect_protected_file", observe)
    # Injected fsync fault checks stopping; activation needs a real storage or permission-induced
    # failure.
    monkeypatch.setattr(driver, "fsync_retained_protected_object", fsync)
    with pytest.raises(ValueError, match="parent binding|after-state|object identity"):
        execute()
    if change != "parent":
        assert service.read_operations()[-1].response["phase"] == "unknown"


@pytest.mark.parametrize("phase", ["started", "completed"])
def test_durable_slot_race_never_authorizes_continuation(
    run: tuple[Callable[..., Any], EventStore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    from synapse_channel.core import protected_write_driver as driver
    from synapse_channel.core.protected_write_execution_steps import record_protected_execution_step

    execute, service = run
    triggered = False

    def competing(*args: Any, **kwargs: Any) -> OperationCommitResult:
        nonlocal triggered
        if kwargs["phase"] == phase and not triggered:
            triggered = True
            if phase == "started":
                record_protected_execution_step(*args, **kwargs)
            else:
                competing_options = dict(kwargs, phase="unknown")
                competing_options.pop("evidence_reference")
                competing_options.pop("verify_completed")
                record_protected_execution_step(*args, **competing_options)
        return record_protected_execution_step(*args, **kwargs)

    # Injected journal race checks refusal; activation needs two real writer processes racing for
    # the step slot.
    monkeypatch.setattr(driver, "record_protected_execution_step", competing)
    with pytest.raises(ValueError, match="already consumed|conflicted"):
        execute()
    assert (tmp_path / "records/note.md").exists() == (phase == "completed")
    assert len(service.read_operations()) == (2 if phase == "started" else 3)


@pytest.mark.parametrize("run", ["all"], indirect=True)
def test_all_eight_opcodes_in_real_success_dependent_chain(
    run: tuple[Callable[..., Any], EventStore], tmp_path: Path
) -> None:
    execute, service = run
    results = execute()
    assert len(results) == 11
    assert all(result.outcome == "inserted" for result in results)
    assert (tmp_path / "records/note.md").read_bytes() == CONTENT
    assert (tmp_path / "records/nested").is_dir()
    assert not (tmp_path / "records/staged").exists()
    assert not (tmp_path / "records/junk").exists()
    assert len(service.read_operations()) == 21


@pytest.fixture
def run(
    tmp_path: Path, request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Callable[..., Any], EventStore]]:
    scenario = getattr(request, "param", "normal")
    if scenario in ("all", "durable_all"):
        original_plan = _plan
        original_inputs = preparation_fixtures._inputs

        def plan() -> dict[str, Any]:
            document = original_plan()
            create, flush, parent = deepcopy(document["auxiliary_operations"])
            file_state = create["paths"][0]["after"]
            absent = {"kind": "absent"}

            def operation(
                name: str, opcode: str, path: str, before: dict[str, Any], after: dict[str, Any]
            ) -> dict[str, Any]:
                return {
                    "operation_id": name,
                    "opcode": opcode,
                    "content_reference": create["content_reference"]
                    if opcode in ("create", "write")
                    else None,
                    "paths": [
                        {
                            "root_id": "memory",
                            "relative_path": path,
                            "before": before,
                            "after": after,
                        }
                    ],
                }

            staged = "records/staged"
            rename = operation("publish", "rename", staged, file_state, absent)
            rename["paths"].append(
                {
                    "root_id": "memory",
                    "relative_path": "records/note.md",
                    "before": absent,
                    "after": file_state,
                }
            )
            document["auxiliary_operations"] = [
                operation(
                    "directory",
                    "mkdir",
                    "records/nested",
                    absent,
                    {"kind": "directory", "mode": "0700"},
                ),
                operation("stage", "create", staged, absent, file_state),
                operation("lock", "lock", staged, file_state, file_state),
                operation("rewrite", "write", staged, file_state, file_state),
                operation("unlock", "unlock", staged, file_state, file_state),
                operation("stage-fsync", "fsync", staged, file_state, file_state),
                rename,
                operation("junk", "create", "records/junk", absent, file_state),
                operation("remove-junk", "unlink", "records/junk", file_state, absent),
                parent,
            ]
            return document

        def inputs(path: Path, planned: bool = False) -> Any:
            admission, proposal, policy, observations = original_inputs(path, planned)
            permissions = dict(policy.auxiliary_opcodes)
            parents = dict(policy.enrolled_parents)
            for entry in ("records/staged", "records/junk", "records/nested"):
                key = ("memory", entry)
                observations[key] = _inspect(path, entry)
                permissions[key] = frozenset(
                    {"mkdir", "create", "write", "lock", "unlock", "rename", "unlink", "fsync"}
                )
                parents[key] = ("memory", "records")
            permissions[("memory", "records/note.md")] |= frozenset({"rename"})
            return (
                admission,
                proposal,
                replace(policy, auxiliary_opcodes=permissions, enrolled_parents=parents),
                observations,
            )

        # Synthetic plan variant exercises opcode ordering; activation needs an operator-owned
        # immutable manifest.
        monkeypatch.setattr(preparation_fixtures, "_plan", plan)
        # Synthetic input variant exercises path binding; activation needs real enrolled artifacts
        # and roots.
        monkeypatch.setattr(preparation_fixtures, "_inputs", inputs)
    authority, prepared, state = _begun(tmp_path)
    service = EventStore(tmp_path / "driver.db")
    root = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    artifacts: dict[str, bytes] = {}

    def persist(content: bytes) -> Mapping[str, object]:
        handle = f"observed-{len(artifacts)}"
        artifacts[handle] = content
        return {
            "domain": "text",
            "handle": handle,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def current() -> ProtectedWriteReservation:
        return verify_prepared_begin(
            prepared,
            state=state,
            store=authority,
            authenticated_writer=(CONTEXT.writer_principal, CONTEXT.writer_incarnation),
            # Unit-only positive policy callback; activation needs actor-consistent current begin
            # and trust lookup.
            authorize_current=lambda _p, _r: True,
        )

    def execute(**overrides: Any) -> tuple[OperationCommitResult, ...]:
        verify_observations = overrides.pop("verify_observations", False)
        supplied = prepared
        if overrides.pop("corrupt_digest", False):
            supplied = replace(
                prepared, content=replace(prepared.content, proposal_sha256="a" * 64)
            )
        options: dict[str, Any] = dict(
            root_descriptors={"memory": root, "parent": root},
            owner_uid=os.getuid(),
            service_journal=service,
            max_journal_operations=32,
            max_locks=8,
            max_evidence_bytes=4096,
            verify_current=current,
            # This fixture does not prove deployed OS-principal isolation.
            # Unit-only positive isolation callback; activation needs a supervised
            # separate-principal writer and real namespace checks.
            verify_isolation=lambda: True,
            store_evidence=persist,
            read_evidence=lambda reference, limit: artifacts[str(reference["handle"])][:limit],
        )
        if scenario == "durable_all":
            retained = ProtectedExecutionEvidenceStore(
                service, "text", prepared.policy.limits.operation_limits, 4096, 32
            )
            options.update(store_evidence=retained.write, read_evidence=retained.read)
        options.update(overrides)
        results = execute_prepared_protected_write(supplied, **options)
        if verify_observations:
            observations = verify_protected_execution_observations(
                supplied,
                current(),
                service_journal=service,
                read_evidence=options["read_evidence"],
                max_total_evidence_bytes=65536,
            )
            assert len(observations) == len(results) - 1
            assert verify_protected_execution_final_state(
                supplied,
                current(),
                service_journal=service,
                read_evidence=options["read_evidence"],
                max_total_evidence_bytes=65536,
                root_descriptors=options["root_descriptors"],
                owner_uid=os.getuid(),
                max_total_content_bytes=65536,
            )
        return results

    try:
        yield execute, service
    finally:
        os.close(root)
        service.close()
        authority.close()


@pytest.mark.parametrize("run", ["durable_all"], indirect=True)
def test_all_opcode_observations_preserve_prepared_physical_identity(
    run: tuple[Callable[..., Any], EventStore],
) -> None:
    execute, _service = run
    results = execute(verify_observations=True)
    assert len(results) == 11


def test_real_create_fsync_sequence_and_no_reexecution(
    run: tuple[Callable[..., Any], EventStore], tmp_path: Path
) -> None:
    execute, service = run
    result = execute()
    assert len(result) == 4
    assert all(item.outcome == "inserted" for item in result)
    target = tmp_path / "records/note.md"
    assert target.read_bytes() == CONTENT
    assert len(service.read_operations()) == 7
    target.write_bytes(b"owner changed after execution")
    replayed = execute()
    assert len(replayed) == 1 and replayed[0].outcome == "replayed"
    assert target.read_bytes() == b"owner changed after execution"


@pytest.mark.parametrize("phase", ["initial", "before_step", "after_started"])
def test_isolation_loss_never_continues(
    run: tuple[Callable[..., Any], EventStore], tmp_path: Path, phase: str
) -> None:
    execute, service = run
    calls = 0

    def isolation() -> bool:
        nonlocal calls
        calls += 1
        return calls < {"initial": 1, "before_step": 2, "after_started": 3}[phase]

    with pytest.raises(ValueError, match="isolation"):
        execute(verify_isolation=isolation)
    assert not (tmp_path / "records/note.md").exists()
    if phase == "after_started":
        assert service.read_operations()[-1].response["phase"] == "unknown"


@pytest.mark.parametrize("failure", ["storage", "digest", "budget"])
def test_evidence_failure_preserves_file_and_stops_before_fsync(
    run: tuple[Callable[..., Any], EventStore], tmp_path: Path, failure: str
) -> None:
    execute, service = run

    def store(_content: bytes) -> Mapping[str, object]:
        if failure == "storage":
            raise OSError("evidence disk failure")
        return {"domain": "text", "handle": "bad", "sha256": "a" * 64, "size_bytes": len(_content)}

    with pytest.raises((OSError, ValueError)):
        execute(store_evidence=store, max_evidence_bytes=1 if failure == "budget" else 4096)
    assert (tmp_path / "records/note.md").read_bytes() == CONTENT
    records = service.read_operations()
    assert len(records) == 3
    assert records[-1].response["phase"] == "unknown"
    assert execute()[0].outcome == "replayed"


def test_invalid_budget_before_any_consumption(run: tuple[Callable[..., Any], EventStore]) -> None:
    execute, service = run
    with pytest.raises(ValueError, match="budget"):
        execute(max_evidence_bytes=cast(int, True))
    assert not service.read_operations()


def test_changed_initial_file_refuses_execution(
    run: tuple[Callable[..., Any], EventStore], tmp_path: Path
) -> None:
    execute, service = run
    path = tmp_path / "records/note.md"
    path.write_bytes(b"owner data")
    with pytest.raises(ValueError, match="before-state"):
        execute()
    assert path.read_bytes() == b"owner data"
    assert len(service.read_operations()) == 1
