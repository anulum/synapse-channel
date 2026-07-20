# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

import hashlib
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypedDict

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.aef_canonical import canonical_json
from synapse_channel.core.aef_emission import (
    AefEmissionError,
    AefReceiptLog,
    derive_aef_log_id,
    sign_aef_receipt,
)
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefTrustedKey,
    AefTrustStore,
    verify_aef_receipt,
)
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.receipt_signing import ReceiptSigningKey, receipt_key_id

_HUB_ID = "hub.example"
_LEGACY_ROOT = "a" * 64


class _AppendLegacyKwargs(TypedDict, total=False):
    legacy_seq: int
    legacy_root: str
    legacy_tree_size: int
    evidence: dict[str, object]


def _key(seed: int = 0) -> ReceiptSigningKey:
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(seed, seed + 32)))
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return ReceiptSigningKey(key_id=receipt_key_id(public), private_key=private)


def _lease_subject(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "epoch": 1,
        "lease_expires_at": 1_783_944_000_000,
    }


def _append_grant(log: AefReceiptLog, task_id: str, legacy_seq: int) -> dict[str, object]:
    return log.append(
        receipt_type="lease",
        action="grant",
        actor_id="agent-1",
        subject=_lease_subject(task_id),
        issued_at=1_783_940_400_000,
        decision="allow",
        legacy_seq=legacy_seq,
    )


def test_log_id_derivation_is_domain_bound_to_hub_algorithm_and_key() -> None:
    key = _key()
    public = key.private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    expected = hashlib.sha256(b"aef-log:hub.example:ed25519:" + public).hexdigest()

    assert derive_aef_log_id(_HUB_ID, public) == expected
    assert derive_aef_log_id("other.example", public) != expected


@pytest.mark.parametrize(
    ("hub_id", "public_key", "message"),
    [
        ("", b"x" * 32, "hub id"),
        ("\ud800", b"x" * 32, "Unicode scalar"),
        ("x" * 4097, b"x" * 32, "hub id"),
        (_HUB_ID, b"short", "32-byte"),
    ],
)
def test_log_id_derivation_rejects_noncanonical_inputs(
    hub_id: str, public_key: bytes, message: str
) -> None:
    with pytest.raises(AefEmissionError, match=message):
        derive_aef_log_id(hub_id, public_key)


def test_native_receipts_chain_verify_and_survive_reopen(tmp_path: Path) -> None:
    path = tmp_path / "aef.db"
    key = _key()
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key) as log:
        first = _append_grant(log, "task-1", 41)
        second = _append_grant(log, "task-2", 42)
        assert log.count() == 2
        log_id = log.log_id

    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key) as reopened:
        receipts = reopened.read_all()
        public = reopened.public_key

    trust = AefTrustStore(
        keys={key.key_id: AefTrustedKey(public)},
        logs={log_id: key.key_id},
    )
    assert [receipt["seq"] for receipt in receipts] == [1, 2]
    assert first["prev_receipt"] == "aef1:" + "0" * 64
    assert second["prev_receipt"] == first["receipt_id"]
    evidence_rows = [receipt["evidence"] for receipt in receipts]
    assert all(isinstance(evidence, dict) for evidence in evidence_rows)
    assert [evidence["legacy_seq"] for evidence in evidence_rows if isinstance(evidence, dict)] == [
        41,
        42,
    ]
    assert all(
        verify_aef_receipt(
            receipt,
            trust_store=trust,
            now_ms=1_783_940_400_000,
        ).verdict
        is AefVerdictCode.VALID
        for receipt in receipts
    )


def test_genesis_can_bind_but_not_merge_the_frozen_legacy_tree(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "anchor.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        genesis = log.append(
            receipt_type="lease",
            action="grant",
            actor_id="agent-1",
            subject=_lease_subject("task-1"),
            issued_at=1_783_940_400_000,
            decision="allow",
            legacy_seq=8,
            legacy_root=_LEGACY_ROOT,
            legacy_tree_size=8,
        )
        with pytest.raises(AefEmissionError, match="genesis"):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject=_lease_subject("task-2"),
                issued_at=1_783_940_400_000,
                decision="allow",
                legacy_root=_LEGACY_ROOT,
                legacy_tree_size=8,
            )

    assert genesis["evidence"] == {
        "legacy_seq": 8,
        "legacy_root": _LEGACY_ROOT,
        "legacy_tree_size": 8,
    }


def test_invalid_receipt_rolls_back_without_consuming_a_sequence(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "rollback.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        with pytest.raises(AefEmissionError, match="self-verification"):
            log.append(
                receipt_type="lease",
                action="deny",
                actor_id="agent-2",
                subject={"task_id": "task-1", "holder": "agent-1"},
                issued_at=1_783_940_400_000,
                decision="deny",
            )
        accepted = _append_grant(log, "task-1", 1)

    assert accepted["seq"] == 1


def test_deny_receipt_accepts_reason_and_expiry(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "deny.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        denied = log.append(
            receipt_type="lease",
            action="deny",
            actor_id="agent-2",
            subject={"task_id": "task-1", "holder": "agent-1"},
            issued_at=1_783_940_400_000,
            expires_at=1_783_940_401_000,
            decision="deny",
            reason_code="LEASE_LIVE",
        )

    assert denied["reason_code"] == "LEASE_LIVE"
    assert denied["expires_at"] == 1_783_940_401_000


def test_native_tables_can_share_hub_db_without_touching_legacy_rows(tmp_path: Path) -> None:
    path = tmp_path / "hub.db"
    with EventStore(path) as events:
        legacy_seq = events.append("claim", {"task_id": "legacy"}, durable=True)
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=_key()) as log:
        _append_grant(log, "task-1", legacy_seq)
    with EventStore(path) as events:
        stored = events.read_all()

    assert [(event.seq, event.kind, event.payload) for event in stored] == [
        (legacy_seq, "claim", {"task_id": "legacy"})
    ]


def test_concurrent_connections_append_one_unbroken_chain(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    key = _key()
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key):
        pass
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(
            executor.map(
                lambda item: _append_in_own_connection(path, key, *item),
                (("task-1", 1), ("task-2", 2)),
            )
        )
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key) as reopened:
        stored = reopened.read_all()

    assert {receipt["receipt_id"] for receipt in receipts} == {
        receipt["receipt_id"] for receipt in stored
    }
    assert [receipt["seq"] for receipt in stored] == [1, 2]
    assert stored[1]["prev_receipt"] == stored[0]["receipt_id"]


def test_reopen_refuses_a_different_hub_or_signing_key(tmp_path: Path) -> None:
    path = tmp_path / "identity.db"
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=_key()):
        pass

    with pytest.raises(AefEmissionError, match="metadata"):
        AefReceiptLog(path, hub_id="other.example", signing_key=_key())
    with pytest.raises(AefEmissionError, match="metadata"):
        AefReceiptLog(path, hub_id=_HUB_ID, signing_key=_key(1))
    mismatched = ReceiptSigningKey(key_id="0" * 16, private_key=_key().private_key)
    with pytest.raises(AefEmissionError, match="does not match"):
        AefReceiptLog(tmp_path / "bad-key.db", hub_id=_HUB_ID, signing_key=mismatched)


def test_signer_rejects_predeclared_metadata_and_log_is_owner_only(tmp_path: Path) -> None:
    key = _key()
    with pytest.raises(AefEmissionError, match="predeclare"):
        sign_aef_receipt({"receipt_id": "foreign"}, signing_key=key)
    mismatched = ReceiptSigningKey(key_id="0" * 16, private_key=key.private_key)
    with pytest.raises(AefEmissionError, match="does not match"):
        sign_aef_receipt({}, signing_key=mismatched)
    path = tmp_path / "private.db"
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key) as log:
        assert log.encrypted is False
        _append_grant(log, "task-1", 1)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_read_refuses_noncanonical_stored_receipt(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=_key()) as log:
        _append_grant(log, "task-1", 1)
        log._conn.execute(
            "UPDATE aef_receipts SET canonical_receipt = ? WHERE seq = 1",
            (b'{ "not":"the signed receipt"}',),
        )
        log._conn.commit()
        with pytest.raises(AefEmissionError, match="canonical"):
            log.read_all()


@pytest.mark.parametrize(
    "evidence",
    [
        {"legacy_seq": 1},
        {"legacy_root": _LEGACY_ROOT},
        {"legacy_tree_size": 1},
    ],
)
def test_reserved_legacy_evidence_requires_indexed_fields(
    tmp_path: Path, evidence: dict[str, object]
) -> None:
    with AefReceiptLog(tmp_path / "reserved.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        with pytest.raises(AefEmissionError, match="legacy"):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject=_lease_subject("task-1"),
                issued_at=1_783_940_400_000,
                decision="allow",
                evidence=evidence,
            )


def test_read_refuses_table_identity_that_disagrees_with_signed_receipt(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "identity.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        _append_grant(log, "task-1", 1)
        log._conn.execute(
            "UPDATE aef_receipts SET receipt_id = ? WHERE seq = 1",
            ("aef1:" + "f" * 64,),
        )
        log._conn.commit()
        with pytest.raises(AefEmissionError, match="does not match"):
            log.read_all()


def test_read_refuses_legacy_index_that_disagrees_with_signed_evidence(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "legacy-index.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        _append_grant(log, "task-1", 1)
        log._conn.execute("UPDATE aef_receipts SET legacy_seq = 2 WHERE seq = 1")
        log._conn.commit()
        with pytest.raises(AefEmissionError, match="does not match"):
            log.read_all()


def test_read_normalises_malformed_database_bytes(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "malformed.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        _append_grant(log, "task-1", 1)
        log._conn.execute(
            "UPDATE aef_receipts SET canonical_receipt = ? WHERE seq = 1",
            (b"not-json",),
        )
        log._conn.commit()
        with pytest.raises(AefEmissionError, match="not canonical"):
            log.read_all()


def test_read_refuses_a_canonical_receipt_with_a_corrupt_signature(tmp_path: Path) -> None:
    with AefReceiptLog(tmp_path / "signature.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        receipt = _append_grant(log, "task-1", 1)
        signature = receipt["signature"]
        assert isinstance(signature, dict)
        signature["value"] = "A" * 86 + "=="
        log._conn.execute(
            "UPDATE aef_receipts SET canonical_receipt = ? WHERE seq = 1",
            (canonical_json(receipt),),
        )
        log._conn.commit()
        with pytest.raises(AefEmissionError, match="failed verification"):
            log.read_all()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"legacy_seq": 0}, "positive integer"),
        ({"legacy_seq": 1, "evidence": {"legacy_seq": 2}}, "conflicts"),
        ({"legacy_root": "bad", "legacy_tree_size": 1}, "canonical anchor"),
        (
            {
                "legacy_root": _LEGACY_ROOT,
                "legacy_tree_size": 1,
                "evidence": {"legacy_root": "b" * 64},
            },
            "conflicts",
        ),
    ],
)
def test_indexed_legacy_fields_reject_invalid_or_conflicting_values(
    tmp_path: Path, kwargs: _AppendLegacyKwargs, message: str
) -> None:
    with AefReceiptLog(tmp_path / "legacy-fields.db", hub_id=_HUB_ID, signing_key=_key()) as log:
        with pytest.raises(AefEmissionError, match=message):
            log.append(
                receipt_type="lease",
                action="grant",
                actor_id="agent-1",
                subject=_lease_subject("task-1"),
                issued_at=1_783_940_400_000,
                decision="allow",
                **kwargs,
            )


def test_in_memory_permission_restriction_is_a_noop() -> None:
    AefReceiptLog._restrict(":memory:")


def _append_in_own_connection(
    path: Path, key: ReceiptSigningKey, task_id: str, legacy_seq: int
) -> dict[str, object]:
    with AefReceiptLog(path, hub_id=_HUB_ID, signing_key=key) as log:
        return _append_grant(log, task_id, legacy_seq)
