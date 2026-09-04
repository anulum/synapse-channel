# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable one-use setup authorization ledger tests

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel.setup_authorization import (
    SetupAuthorizationError,
    build_setup_authorization,
)
from synapse_channel.setup_ledger import (
    SETUP_LEDGER_DATABASE,
    SetupAuthorizationLedger,
    SetupLedgerError,
    _decode_record,
    _restrict_file,
    default_setup_ledger_dir,
)
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import SetupProfile, get_setup_profile

NONCE = "ledger_nonce_0123456789ab"
RECEIPT = "b" * 64
RECOVERY_RECEIPT = "c" * 64


def _profile() -> SetupProfile:
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    return profile


def _plan() -> dict[str, object]:
    profile = _profile()
    statuses = {
        requirement.requirement_id: "fail" if requirement.requirement_id == "waiter" else "pass"
        for requirement in profile.requirements
    }
    checks = [
        {
            "id": requirement.requirement_id,
            "status": statuses[requirement.requirement_id],
            "required": requirement.required,
            "value": {
                "kind": "systemd-user",
                "executable": "/usr/bin/systemctl",
                "hub_pid": 0,
            }
            if requirement.requirement_id == "service_manager"
            else {},
            "detail": "Observed fixture.",
            "remedy": "",
        }
        for requirement in profile.requirements
    ]
    inspection = {
        "schema_version": "synapse-setup.v1",
        "document_kind": "inspection",
        "profile": "local-single-user",
        "profile_version": 1,
        "read_only": True,
        "ready": False,
        "target": {
            "uri": "ws://localhost:8876",
            "project": "DEMO",
            "identity": "DEMO/codex-one",
        },
        "summary": {
            status: sum(check["status"] == status for check in checks)
            for status in ("pass", "warn", "fail", "unavailable")
        },
        "checks": checks,
    }
    return build_setup_plan(profile, inspection)


def _authorization(
    plan: dict[str, object],
    *,
    nonce: str = NONCE,
    issued_at: int = 100,
) -> dict[str, object]:
    return build_setup_authorization(
        plan,
        confirm_digest=cast(str, plan["plan_digest"]),
        nonce=nonce,
        expires_in=300,
        restart_pid=None,
        clock=lambda: float(issued_at),
    )


def _assert_code(expected: str, error: BaseException) -> None:
    assert isinstance(error, SetupAuthorizationError)
    assert error.code == expected


def _reserve_in_process(
    directory: str,
    plan: dict[str, object],
    authorization: dict[str, object],
    gate: Any,
    results: Any,
) -> None:
    """Contend for one nonce from an independent interpreter process."""
    gate.wait(timeout=10.0)
    try:
        with SetupAuthorizationLedger(directory) as ledger:
            outcome = ledger.reserve(plan, authorization, now=102).state
    except SetupLedgerError as exc:
        cause = exc.__cause__
        outcome = exc.code if cause is None else f"{exc.code}:{type(cause).__name__}:{cause}"
    results.put(outcome)


def test_default_ledger_directory_obeys_absolute_xdg_or_home() -> None:
    assert default_setup_ledger_dir(env={"XDG_STATE_HOME": "/state"}) == Path(
        "/state/synapse-channel"
    )
    assert default_setup_ledger_dir(env={"HOME": "/home/demo"}) == Path(
        "/home/demo/.local/state/synapse-channel"
    )
    with pytest.raises(SetupLedgerError) as caught:
        default_setup_ledger_dir(env={"XDG_STATE_HOME": "relative"})
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_reservation_is_private_durable_and_token_free(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])
    directory = tmp_path / "ledger"

    with SetupAuthorizationLedger(directory) as ledger:
        record = ledger.reserve(plan, authorization, now=101)
        assert record.state == "reserved"
        assert record.authorization_digest == auth_digest
        assert "confirmation_nonce" not in record.as_dict()
        assert ledger.get(auth_digest) == record
        assert ledger.get("a" * 64) is None
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(ledger.path.stat().st_mode) == 0o600

    database = directory / SETUP_LEDGER_DATABASE
    assert NONCE.encode("ascii") not in database.read_bytes()
    with SetupAuthorizationLedger(directory) as reopened:
        assert reopened.get(auth_digest) == record


def test_nonce_and_authorization_are_reserved_exactly_once_across_connections(
    tmp_path: Path,
) -> None:
    plan = _plan()
    first = _authorization(plan)
    same_nonce_new_envelope = _authorization(plan, issued_at=101)
    directory = tmp_path / "ledger"

    with (
        SetupAuthorizationLedger(directory) as one,
        SetupAuthorizationLedger(directory) as two,
    ):
        one.reserve(plan, first, now=101)
        for authorization, now in ((first, 102), (same_nonce_new_envelope, 102)):
            with pytest.raises(SetupLedgerError) as caught:
                two.reserve(plan, authorization, now=now)
            _assert_code("authorization_replayed", caught.value)


def test_simultaneous_reservation_has_exactly_one_winner(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    directory = tmp_path / "ledger"
    barrier = threading.Barrier(2, timeout=5.0)

    def reserve(ledger: SetupAuthorizationLedger) -> str:
        barrier.wait()
        try:
            return ledger.reserve(plan, authorization, now=101).state
        except SetupLedgerError as exc:
            return exc.code

    with (
        SetupAuthorizationLedger(directory) as one,
        SetupAuthorizationLedger(directory) as two,
        ThreadPoolExecutor(max_workers=2) as executor,
    ):
        results = sorted(executor.map(reserve, (one, two)))

    assert results == ["authorization_replayed", "reserved"]


def test_cross_process_reservation_has_exactly_one_nonce_winner(tmp_path: Path) -> None:
    plan = _plan()
    authorizations = (
        _authorization(plan, issued_at=100),
        _authorization(plan, issued_at=101),
    )
    directory = tmp_path / "ledger"
    with SetupAuthorizationLedger(directory):
        pass
    context = mp.get_context("spawn")
    gate = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_reserve_in_process,
            args=(str(directory), plan, authorization, gate, results),
        )
        for authorization in authorizations
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20.0)
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5.0)

    assert [process.exitcode for process in processes] == [0, 0]
    assert sorted(results.get(timeout=5.0) for _ in processes) == [
        "authorization_replayed",
        "reserved",
    ]


def test_reserve_validates_expiry_before_writing(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        with pytest.raises(SetupAuthorizationError) as caught:
            ledger.reserve(plan, authorization, now=400)
        _assert_code("authorization_expired", caught.value)
        assert ledger.get(cast(str, authorization["authorization_digest"])) is None


def test_finish_and_recovery_transitions_are_explicit(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        ledger.reserve(plan, authorization, now=101)
        failed = ledger.finish(auth_digest, outcome="failed", receipt_digest=RECEIPT)
        assert failed.state == "failed"
        assert failed.effect_receipt_digest == RECEIPT
        recovered = ledger.recover(auth_digest, receipt_digest=RECOVERY_RECEIPT)
        assert recovered.state == "recovered"
        assert recovered.effect_receipt_digest == RECEIPT
        assert recovered.recovery_receipt_digest == RECOVERY_RECEIPT
        with pytest.raises(SetupLedgerError) as caught:
            ledger.finish(auth_digest, outcome="applied", receipt_digest=RECEIPT)
        _assert_code("authorization_transition_invalid", caught.value)


def test_reserved_authorization_can_be_recovered_without_an_effect_receipt(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        ledger.reserve(plan, authorization, now=101)
        recovered = ledger.recover(auth_digest, receipt_digest=RECOVERY_RECEIPT)
        assert recovered.state == "recovered"
        assert recovered.effect_receipt_digest is None


@pytest.mark.parametrize(
    ("method", "digest", "outcome"),
    [
        ("finish", "short", "applied"),
        ("finish", "a" * 64, "unknown"),
        ("recover", "short", "recovered"),
    ],
)
def test_invalid_transitions_fail_before_mutation(
    tmp_path: Path,
    method: str,
    digest: str,
    outcome: str,
) -> None:
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        with pytest.raises(SetupLedgerError) as caught:
            if method == "finish":
                ledger.finish(
                    digest,
                    outcome=cast(Any, outcome),
                    receipt_digest=RECEIPT,
                )
            else:
                ledger.recover(digest, receipt_digest=RECOVERY_RECEIPT)
        _assert_code("authorization_transition_invalid", caught.value)


def test_missing_transition_and_unsafe_directory_fail_closed(tmp_path: Path) -> None:
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        with pytest.raises(SetupLedgerError) as caught:
            ledger.finish("a" * 64, outcome="applied", receipt_digest=RECEIPT)
        _assert_code("authorization_transition_invalid", caught.value)

    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(SetupLedgerError) as caught:
        SetupAuthorizationLedger(link)
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_incompatible_metadata_is_refused_on_reopen(tmp_path: Path) -> None:
    directory = tmp_path / "ledger"
    with SetupAuthorizationLedger(directory):
        pass
    database = directory / SETUP_LEDGER_DATABASE
    connection = sqlite3.connect(database)
    connection.execute("UPDATE setup_ledger_metadata SET version = 99")
    connection.commit()
    connection.close()

    with pytest.raises(SetupLedgerError) as caught:
        SetupAuthorizationLedger(directory)
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_malformed_existing_table_is_refused_on_reopen(tmp_path: Path) -> None:
    directory = tmp_path / "ledger"
    with SetupAuthorizationLedger(directory):
        pass
    database = directory / SETUP_LEDGER_DATABASE
    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE setup_authorizations")
    connection.execute("CREATE TABLE setup_authorizations (bad INTEGER)")
    connection.commit()
    connection.close()

    with pytest.raises(SetupLedgerError) as caught:
        SetupAuthorizationLedger(directory)
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_database_leaf_symlink_is_refused(tmp_path: Path) -> None:
    directory = tmp_path / "ledger"
    directory.mkdir(mode=0o700)
    target = tmp_path / "target.db"
    target.write_bytes(b"")
    (directory / SETUP_LEDGER_DATABASE).symlink_to(target)
    with pytest.raises(SetupLedgerError) as caught:
        SetupAuthorizationLedger(directory)
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_applied_transition_records_receipt(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])
    ledger = SetupAuthorizationLedger(tmp_path / "ledger")
    ledger.reserve(plan, authorization, now=101)
    record = ledger.finish(auth_digest, outcome="applied", receipt_digest=RECEIPT)
    assert record.state == "applied"
    assert record.effect_receipt_digest == RECEIPT
    ledger.close()


def test_closed_connection_failures_are_bounded(tmp_path: Path) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])

    ledger = SetupAuthorizationLedger(tmp_path / "reserve-ledger")
    ledger.close()
    with pytest.raises(SetupLedgerError) as caught:
        ledger.get(auth_digest)
    _assert_code("authorization_ledger_unavailable", caught.value)
    with pytest.raises(SetupLedgerError) as caught:
        ledger.reserve(plan, authorization, now=101)
    _assert_code("authorization_ledger_unavailable", caught.value)

    ledger = SetupAuthorizationLedger(tmp_path / "finish-ledger")
    ledger.reserve(plan, authorization, now=101)
    ledger.close()
    with pytest.raises(SetupLedgerError) as caught:
        ledger.finish(auth_digest, outcome="applied", receipt_digest=RECEIPT)
    _assert_code("authorization_ledger_unavailable", caught.value)


def test_corrupt_record_read_is_bounded(tmp_path: Path) -> None:
    digest = "d" * 64
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        connection = sqlite3.connect(ledger.path)
        connection.execute(
            "INSERT INTO setup_authorizations "
            "(nonce_digest, authorization_digest, plan_digest, reserved_at, state, "
            "effect_receipt_digest, recovery_receipt_digest) "
            "VALUES (?, ?, ?, ?, 'reserved', NULL, NULL)",
            ("c" * 64, digest, "invalid", 1),
        )
        connection.commit()
        connection.close()

        with pytest.raises(SetupLedgerError) as caught:
            ledger.get(digest)
        _assert_code("authorization_ledger_unavailable", caught.value)


def test_transition_refuses_a_missing_committed_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    authorization = _authorization(plan)
    auth_digest = cast(str, authorization["authorization_digest"])
    with SetupAuthorizationLedger(tmp_path / "ledger") as ledger:
        ledger.reserve(plan, authorization, now=101)
        monkeypatch.setattr(SetupAuthorizationLedger, "get", lambda self, digest: None)
        with pytest.raises(SetupLedgerError) as caught:
            ledger.finish(auth_digest, outcome="applied", receipt_digest=RECEIPT)
        _assert_code("authorization_ledger_unavailable", caught.value)


@pytest.mark.parametrize(
    "row",
    [
        (),
        ("a" * 64, "b" * 64, "c" * 64, -1, "reserved", None, None),
        ("a" * 64, "b" * 64, "c" * 64, 1, "unknown", None, None),
    ],
)
def test_malformed_ledger_records_are_rejected(row: tuple[object, ...]) -> None:
    with pytest.raises(ValueError, match="ledger|reservation"):
        _decode_record(row)


def test_missing_optional_sidecar_is_ignored(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db-wal"
    _restrict_file(missing)
    with pytest.raises(FileNotFoundError):
        _restrict_file(missing, required=True)


def test_nonregular_database_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fstat = os.fstat

    def directory_fstat(descriptor: int) -> os.stat_result:
        values = list(real_fstat(descriptor))
        values[0] = stat.S_IFDIR | 0o700
        return os.stat_result(values)

    monkeypatch.setattr("synapse_channel.setup_ledger.os.fstat", directory_fstat)
    with pytest.raises(SetupLedgerError) as caught:
        SetupAuthorizationLedger(tmp_path / "ledger")
    _assert_code("authorization_ledger_unavailable", caught.value)
