# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from synapse_channel.dashboard_setup_receipts import (
    SETUP_RECEIPT_DATABASE,
    SetupArtifactDigest,
    SetupEffectReceipt,
    SetupReceiptDraft,
    SetupReceiptIntegrityError,
    SetupReceiptPersistenceError,
    SetupReceiptStore,
)

_REQUEST_ID = "123e4567-e89b-42d3-a456-426614174000"
_PLAN_ID = "A" * 22
_DIGEST = "a" * 64


def _draft(*, request_id: str = _REQUEST_ID, outcome: str = "planned") -> SetupReceiptDraft:
    reason = "none" if outcome in {"planned", "authorised", "applied"} else "plan_drift"
    return SetupReceiptDraft(
        request_id=request_id,
        plan_id=_PLAN_ID,
        plan_digest=_DIGEST,
        principal_id="SYNAPSE-CHANNEL/setup-admin",
        capability="setup_plan",
        profile="local-ephemeral",
        profile_version=1,
        configuration_generation="b" * 64,
        timestamp_ms=1_784_758_400_000,
        outcome=outcome,  # type: ignore[arg-type]
        effects=(
            SetupEffectReceipt(
                "runtime_directory",
                "synapse-user-runtime",
                "not_checked",
                "not_checked",
            ),
        ),
        package_version="0.99.12",
        template_version="setup-v1",
        artifacts=(SetupArtifactDigest("runtime-template", "c" * 64),),
        reason=reason,  # type: ignore[arg-type]
    )


def test_receipts_chain_canonically_and_survive_reopen(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        first = store.append(_draft())
        second = store.append(
            replace(
                _draft(request_id="123e4567-e89b-42d3-a456-426614174001"),
                capability="setup_apply",
                outcome="authorised",
            )
        )
        assert store.count() == 2

    with SetupReceiptStore(directory) as reopened:
        stored = reopened.read_all()

    assert stored == (first, second)
    assert [receipt.sequence for receipt in stored] == [1, 2]
    assert first.previous_receipt_digest == "0" * 64
    assert second.previous_receipt_digest == first.receipt_digest
    assert len(first.receipt_digest) == 64


def test_browser_projection_is_token_free_and_canonical(tmp_path: Path) -> None:
    with SetupReceiptStore(tmp_path / "receipts") as store:
        projection = store.append(_draft()).browser_projection()

    encoded = json.dumps(projection, sort_keys=True)
    assert projection["outcome"] == "planned"
    assert projection["reason"] == "none"
    assert "bearer" not in encoded.lower()
    assert "nonce" not in encoded.lower()
    assert "authorization" not in encoded.lower()
    assert "confirmation" not in encoded.lower()

    with SetupReceiptStore(tmp_path / "failure-receipts") as store:
        drifted = store.append(_draft(outcome="drifted"))
    assert drifted.browser_projection()["reason"] == "plan_drift"


def test_store_creates_owner_only_directory_database_and_wal(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        store.append(_draft())
        database = store.path
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(database.stat().st_mode) == 0o600
        wal = Path(f"{database}-wal")
        assert wal.exists()
        assert stat.S_IMODE(wal.stat().st_mode) == 0o600


def test_store_refuses_a_symlink_database_leaf(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o700)
    target = tmp_path / "foreign.db"
    target.write_bytes(b"")
    (directory / SETUP_RECEIPT_DATABASE).symlink_to(target)

    with pytest.raises(SetupReceiptPersistenceError, match="unsafe"):
        SetupReceiptStore(directory)


def test_commit_failure_rolls_back_without_consuming_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    with SetupReceiptStore(tmp_path / "receipts") as store:
        original = receipts_module._commit

        def disk_full(_connection: sqlite3.Connection) -> None:
            raise sqlite3.OperationalError("database or disk is full")

        monkeypatch.setattr(receipts_module, "_commit", disk_full)
        with pytest.raises(SetupReceiptPersistenceError, match="durably persisted") as caught:
            store.append(_draft())
        assert "disk" not in str(caught.value).lower()
        assert store.count() == 0

        monkeypatch.setattr(receipts_module, "_commit", original)
        accepted = store.append(_draft())

    assert accepted.sequence == 1


def test_interrupted_commit_rolls_back_and_propagates_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    with SetupReceiptStore(tmp_path / "receipts") as store:
        monkeypatch.setattr(
            receipts_module,
            "_commit",
            lambda _connection: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        with pytest.raises(KeyboardInterrupt):
            store.append(_draft())
        assert store.count() == 0


def test_reopen_refuses_tampered_canonical_receipt(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        store.append(_draft())
        path = store.path

    with sqlite3.connect(path) as connection:
        raw = connection.execute(
            "SELECT canonical_receipt FROM setup_receipts WHERE sequence = 1"
        ).fetchone()[0]
        document = json.loads(raw)
        document["outcome"] = "denied"
        connection.execute(
            "UPDATE setup_receipts SET canonical_receipt = ? WHERE sequence = 1",
            (json.dumps(document, sort_keys=True, separators=(",", ":")).encode(),),
        )

    with pytest.raises(SetupReceiptIntegrityError, match="malformed|invalid"):
        SetupReceiptStore(directory)


def test_reopen_refuses_a_broken_chain_index(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        store.append(_draft())
        store.append(_draft(request_id="123e4567-e89b-42d3-a456-426614174001"))
        path = store.path

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE setup_receipts SET previous_receipt_digest = ? WHERE sequence = 2",
            ("f" * 64,),
        )

    with pytest.raises(SetupReceiptIntegrityError, match="chain"):
        SetupReceiptStore(directory)


def test_append_refuses_corruption_introduced_after_open(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        store.append(_draft())
        with sqlite3.connect(store.path) as connection:
            connection.execute(
                "UPDATE setup_receipts SET receipt_digest = ? WHERE sequence = 1",
                ("f" * 64,),
            )
        with pytest.raises(SetupReceiptIntegrityError, match="chain"):
            store.append(_draft(request_id="123e4567-e89b-42d3-a456-426614174001"))
        assert store.count() == 1


def test_concurrent_connections_assign_one_unbroken_order(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory):
        pass

    request_ids = [f"123e4567-e89b-42d3-a456-42661417400{index}" for index in range(4)]

    def append_one(request_id: str) -> int:
        with SetupReceiptStore(directory) as store:
            return store.append(_draft(request_id=request_id)).sequence

    with ThreadPoolExecutor(max_workers=4) as executor:
        sequences = list(executor.map(append_one, request_ids))
    with SetupReceiptStore(directory) as store:
        stored = store.read_all()

    assert sorted(sequences) == [1, 2, 3, 4]
    assert [receipt.sequence for receipt in stored] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"request_id": "not-a-uuid"}, "request id"),
        ({"plan_id": "short"}, "plan id"),
        ({"principal_id": "Bearer secret"}, "principal"),
        ({"profile_version": True}, "profile version"),
        ({"timestamp_ms": 0}, "timestamp"),
        ({"package_version": "bad version"}, "package version"),
        ({"outcome": "drifted"}, "bounded reason"),
        ({"outcome": "planned", "reason": "plan_drift"}, "cannot carry"),
    ],
)
def test_draft_rejects_unbounded_or_inconsistent_fields(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_draft(), **change)  # type: ignore[arg-type]


def test_effect_and_artifact_models_reject_unknown_or_duplicate_values() -> None:
    with pytest.raises(ValueError, match="effect kind"):
        SetupEffectReceipt("unknown", "target", "absent", "satisfied")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="verdict"):
        SetupEffectReceipt("user_unit", "unit", "unknown", "satisfied")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="artifact digest"):
        SetupArtifactDigest("unit", "invalid")
    duplicate = SetupArtifactDigest("unit", "d" * 64)
    with pytest.raises(ValueError, match="unique"):
        replace(_draft(), artifacts=(duplicate, duplicate))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"capability": "root"}, "capability"),
        ({"profile": "foreign"}, "profile"),
        ({"outcome": "unknown"}, "outcome"),
        ({"effects": (_draft().effects[0],) * 17}, "effect count"),
        (
            {"artifacts": tuple(SetupArtifactDigest(f"a{index}", "d" * 64) for index in range(17))},
            "artifact count",
        ),
        ({"reason": "raw_error"}, "reason is invalid"),
    ],
)
def test_draft_rejects_unknown_enums_and_oversized_collections(
    change: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        replace(_draft(), **change)  # type: ignore[arg-type]


def test_open_and_initialisation_failures_are_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("raw path")),
    )
    with pytest.raises(SetupReceiptPersistenceError, match="could not be opened") as caught:
        SetupReceiptStore(tmp_path / "open-failure")
    assert "raw path" not in str(caught.value)


def test_initial_commit_failure_is_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    monkeypatch.setattr(
        receipts_module,
        "_commit",
        lambda _connection: (_ for _ in ()).throw(sqlite3.OperationalError("disk full")),
    )
    with pytest.raises(SetupReceiptPersistenceError, match="initialised"):
        SetupReceiptStore(tmp_path / "initial-commit-failure")


def test_append_preserves_a_store_integrity_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    with SetupReceiptStore(tmp_path / "receipts") as store:
        monkeypatch.setattr(
            receipts_module,
            "_build_receipt",
            lambda *_args: (_ for _ in ()).throw(SetupReceiptIntegrityError("invalid")),
        )
        with pytest.raises(SetupReceiptIntegrityError, match="invalid"):
            store.append(_draft())
        assert store.count() == 0


def test_reopen_refuses_invalid_metadata_version(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    with SetupReceiptStore(directory) as store:
        path = store.path
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE setup_receipt_metadata SET version = 2")

    with pytest.raises(SetupReceiptIntegrityError, match="metadata"):
        SetupReceiptStore(directory)


def test_private_decode_helpers_fail_closed_on_malformed_shapes() -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    with pytest.raises(SetupReceiptIntegrityError, match="rows"):
        receipts_module._decode_rows(object())
    with pytest.raises(SetupReceiptIntegrityError, match="row"):
        receipts_module._decode_row(
            (1, 2, 3),
            expected_sequence=1,
            expected_previous="0" * 64,
        )
    with pytest.raises(SetupReceiptIntegrityError, match="canonical"):
        receipts_module._load_canonical_receipt(b'{"z":1, "a":2}')
    with pytest.raises(ValueError, match="fields"):
        receipts_module._receipt_from_document({"version": 1})
    with pytest.raises(ValueError, match="collections"):
        document = SetupReceiptStoreErrorDocument.build()
        document["effects"] = "invalid"
        receipts_module._receipt_from_document(document)
    with pytest.raises(ValueError, match="effect fields"):
        receipts_module._effect_from_document({"kind": "user_unit"})
    with pytest.raises(ValueError, match="artifact fields"):
        receipts_module._artifact_from_document({"artifact": "unit"})
    with pytest.raises(ValueError, match="must be text"):
        receipts_module._require_str(1)
    with pytest.raises(ValueError, match="must be an integer"):
        receipts_module._require_int(True)
    with pytest.raises(SetupReceiptIntegrityError, match="integrity check"):
        receipts_module._require_database_integrity(("corrupt",))


class SetupReceiptStoreErrorDocument:
    """Build a valid document used only for hostile decoder shape tests."""

    @staticmethod
    def build() -> dict[str, object]:
        import synapse_channel.dashboard_setup_receipts as receipts_module

        return receipts_module._build_receipt(1, _draft(), "0" * 64).as_dict()


def test_path_and_permission_helpers_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import synapse_channel.dashboard_setup_receipts as receipts_module

    receipts_module._restrict_file(tmp_path / "absent")
    path = tmp_path / "unsafe.db"
    path.write_bytes(b"")
    real_fstat = os.fstat
    monkeypatch.setattr(
        os,
        "fstat",
        lambda _descriptor: SimpleNamespace(
            st_mode=stat.S_IFDIR | 0o700,
            st_uid=os.geteuid(),
        ),
    )
    with pytest.raises(SetupReceiptPersistenceError, match="unsafe"):
        receipts_module._prepare_database_file(path)
    monkeypatch.setattr(os, "fstat", real_fstat)
    monkeypatch.setattr(
        os,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    with pytest.raises(SetupReceiptPersistenceError, match="permissions"):
        receipts_module._restrict_file(path)


def test_request_id_must_use_canonical_lowercase_uuid() -> None:
    with pytest.raises(ValueError, match="request id"):
        replace(_draft(), request_id=_REQUEST_ID.upper())


def test_preexisting_loose_directory_and_database_are_tightened(tmp_path: Path) -> None:
    directory = tmp_path / "receipts"
    directory.mkdir(mode=0o755)
    database = directory / SETUP_RECEIPT_DATABASE
    descriptor = os.open(database, os.O_CREAT | os.O_WRONLY, 0o666)
    os.close(descriptor)
    os.chmod(database, 0o666)

    with SetupReceiptStore(directory):
        pass

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
