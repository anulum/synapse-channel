# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_legacy_mapping import (
    AefLegacyMappingError,
    legacy_event_to_aef,
)
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.receipt_signing import ReceiptSigningKey, receipt_key_id

_DIGEST = "a" * 64


def _event(kind: str, payload: dict[str, object], *, seq: int = 7) -> StoredEvent:
    return StoredEvent(seq, 1_783_940_400.125, kind, payload)


def _key() -> ReceiptSigningKey:
    private = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return ReceiptSigningKey(key_id=receipt_key_id(public), private_key=private)


def _claim_payload() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "owner": "agent-1",
        "lease_expires_at": 1_783_944_000.75,
        "epoch": 3,
        "version": 2,
        "status": "working",
        "worktree": "repo",
        "paths": ["src/a.py"],
    }


def test_claim_maps_to_integer_time_lease_grant() -> None:
    request = legacy_event_to_aef(_event(EventKind.CLAIM, _claim_payload()))

    assert request is not None
    assert (request.receipt_type, request.action, request.actor_id) == (
        "lease",
        "grant",
        "agent-1",
    )
    assert request.issued_at == 1_783_940_400_125
    assert request.expires_at == 1_783_944_000_750
    assert request.subject == {
        "task_id": "task-1",
        "epoch": 3,
        "lease_expires_at": 1_783_944_000_750,
        "worktree": "repo",
        "paths": ["src/a.py"],
        "version": 2,
        "status": "working",
    }
    assert request.decision == "allow"


def test_empty_optional_claim_scope_is_omitted() -> None:
    payload = _claim_payload()
    payload.update({"worktree": "", "paths": []})

    request = legacy_event_to_aef(_event(EventKind.CLAIM, payload))

    assert request is not None
    assert "worktree" not in request.subject
    assert request.subject["paths"] == []


def test_absent_optional_claim_fields_stay_absent() -> None:
    payload = _claim_payload()
    for key in ("paths", "status", "version", "worktree"):
        payload.pop(key)

    request = legacy_event_to_aef(_event(EventKind.CLAIM, payload))

    assert request is not None
    assert request.subject == {
        "task_id": "task-1",
        "epoch": 3,
        "lease_expires_at": 1_783_944_000_750,
    }


def test_claim_denial_preserves_only_digest_identifiers() -> None:
    request = legacy_event_to_aef(
        _event(
            EventKind.CLAIM_DENIAL,
            {
                "claimant": "agent-2",
                "claimant_sha256": "b" * 64,
                "scope_sha256": "c" * 64,
                "task_id_sha256": "d" * 64,
                "reason_code": "SCOPE_CONFLICT",
            },
        )
    )

    assert request is not None
    assert request.actor_id == "sha256:" + "b" * 64
    assert request.subject == {
        "task_id": "sha256:" + "d" * 64,
        "holder": "undisclosed:legacy-minimized",
    }
    assert request.decision == "deny"
    assert request.reason_code == "SCOPE_CONFLICT"
    assert request.evidence is not None
    assert request.evidence["identifier_profile"] == "digest-only"
    assert "agent-2" not in repr(request)
    assert "task-1" not in repr(request)


def test_guard_denial_remains_digest_only() -> None:
    request = legacy_event_to_aef(
        _event(
            EventKind.GUARD_DENIAL,
            {
                "actor_sha256": "b" * 64,
                "call_sha256": "c" * 64,
                "scope_sha256": "d" * 64,
                "credential_principal_sha256": "e" * 64,
                "recorder_sha256": "f" * 64,
                "path_count": 2,
                "provider": "codex",
                "reason_code": "GUARD_NO_CLAIM",
            },
        )
    )

    assert request is not None
    assert request.actor_id == "sha256:" + "b" * 64
    assert request.subject == {
        "tool": "codex",
        "guard": "claim-guard",
        "call_id": "sha256:" + "c" * 64,
    }
    assert request.reason_code == "GUARD_NO_CLAIM"
    assert request.evidence is not None
    assert request.evidence["path_count"] == 2


def test_sandbox_run_maps_digest_only_execution_receipt() -> None:
    request = legacy_event_to_aef(
        _event(
            EventKind.SANDBOX_RUN,
            {
                "tool_id": "lint-tool",
                "content_digest": "sha256:" + "b" * 64,
                "inputs_digest": "sha256:" + "c" * 64,
                "output_digest": "sha256:" + "d" * 64,
                "granted_capabilities": ["fs:/repo:r"],
                "exit": "ok",
                "fuel_used": 42,
                "reason": "raw output must not migrate",
            },
        )
    )

    assert request is not None
    assert (request.receipt_type, request.action, request.actor_id) == (
        "tool_call",
        "execute",
        "SynapseHub",
    )
    assert request.subject["call_id"] == "legacy-event:7"
    assert request.evidence == {
        "legacy_event_kind": EventKind.SANDBOX_RUN,
        "content_digest": "sha256:" + "b" * 64,
        "fuel_used": 42,
    }
    assert "raw output" not in repr(request)


def test_partition_maps_the_full_peer_set_without_claiming_one_peer() -> None:
    request = legacy_event_to_aef(
        _event(
            EventKind.MULTIHUB_PARTITION,
            {
                "namespace": "OWNED",
                "local_hub_id": "hub-a",
                "contesting_hubs": ["hub-c", "hub-b", "hub-b"],
            },
        )
    )

    assert request is not None
    assert request.action == "partition_mark"
    peer_domain = request.subject["peer_domain"]
    assert isinstance(peer_domain, str)
    assert peer_domain.startswith("peer-set:sha256:")
    assert request.evidence == {
        "legacy_event_kind": EventKind.MULTIHUB_PARTITION,
        "peer_domains": ["hub-b", "hub-c"],
    }
    assert request.reason_code == "NAMESPACE_OWNERSHIP_CONFLICT"


def test_single_peer_partition_preserves_the_peer_domain() -> None:
    request = legacy_event_to_aef(
        _event(
            EventKind.MULTIHUB_PARTITION,
            {
                "namespace": "OWNED",
                "local_hub_id": "hub-a",
                "contesting_hubs": ["hub-b"],
            },
        )
    )

    assert request is not None
    assert request.subject["peer_domain"] == "hub-b"


def test_heal_requires_and_preserves_refreshed_observation() -> None:
    payload = {
        "namespace": "OWNED",
        "local_hub_id": "hub-a",
        "previous_contesting_hubs": ["hub-b"],
        "observation_refreshed": True,
    }
    request = legacy_event_to_aef(_event(EventKind.MULTIHUB_HEAL, payload))

    assert request is not None
    assert request.action == "heal"
    assert request.evidence is not None
    assert request.evidence["ownership_observation_refreshed"] is True

    payload["observation_refreshed"] = False
    with pytest.raises(AefLegacyMappingError, match="refreshed observation"):
        legacy_event_to_aef(_event(EventKind.MULTIHUB_HEAL, payload))


@pytest.mark.parametrize(
    "kind",
    [
        EventKind.CHAT,
        EventKind.RELEASE,
        EventKind.TASK_UPDATE,
        EventKind.DELIVERY_RECEIPT_IMMEDIATE,
    ],
)
def test_unspecified_event_kinds_stay_legacy_only(kind: str) -> None:
    assert legacy_event_to_aef(_event(kind, {"payload": "do not reinterpret"})) is None


@pytest.mark.parametrize(
    ("event", "message"),
    [
        (StoredEvent(0, 1.0, EventKind.CLAIM, _claim_payload()), "sequence"),
        (_event(EventKind.CLAIM, {**_claim_payload(), "epoch": True}), "epoch"),
        (_event(EventKind.CLAIM, {**_claim_payload(), "owner": ""}), "owner"),
        (
            _event(EventKind.CLAIM, {**_claim_payload(), "lease_expires_at": True}),
            "lease_expires_at",
        ),
        (
            _event(
                EventKind.GUARD_DENIAL,
                {
                    "actor_sha256": _DIGEST,
                    "call_sha256": _DIGEST,
                    "scope_sha256": "bad",
                    "path_count": 0,
                    "provider": "codex",
                    "reason_code": "GUARD_NO_CLAIM",
                },
            ),
            "scope_sha256",
        ),
        (
            _event(
                EventKind.SANDBOX_RUN,
                {
                    "tool_id": "tool",
                    "content_digest": "sha256:" + _DIGEST,
                    "inputs_digest": "bad",
                    "output_digest": "sha256:" + _DIGEST,
                    "granted_capabilities": [],
                    "exit": "ok",
                    "fuel_used": 1,
                },
            ),
            "inputs_digest",
        ),
        (
            _event(
                EventKind.SANDBOX_RUN,
                {
                    "tool_id": "tool",
                    "content_digest": "sha256:" + _DIGEST,
                    "inputs_digest": "sha256:" + _DIGEST,
                    "output_digest": "sha256:" + _DIGEST,
                    "granted_capabilities": "not-a-list",
                    "exit": "ok",
                    "fuel_used": 1,
                },
            ),
            "granted_capabilities",
        ),
        (
            _event(
                EventKind.MULTIHUB_PARTITION,
                {
                    "namespace": "OWNED",
                    "local_hub_id": "hub-a",
                    "contesting_hubs": [],
                },
            ),
            "name a peer",
        ),
    ],
)
def test_malformed_supported_rows_fail_closed(event: StoredEvent, message: str) -> None:
    with pytest.raises((AefLegacyMappingError, ValueError), match=message):
        legacy_event_to_aef(event)


def test_mapped_rows_emit_one_native_chain_with_legacy_reconciliation(tmp_path: Path) -> None:
    events = [
        _event(EventKind.CLAIM, _claim_payload(), seq=11),
        _event(
            EventKind.GUARD_DENIAL,
            {
                "actor_sha256": "b" * 64,
                "call_sha256": "c" * 64,
                "scope_sha256": "d" * 64,
                "path_count": 1,
                "provider": "codex",
                "reason_code": "GUARD_NO_CLAIM",
            },
            seq=12,
        ),
    ]
    with AefReceiptLog(tmp_path / "mapped.db", hub_id="hub.example", signing_key=_key()) as log:
        receipts = []
        for event in events:
            request = legacy_event_to_aef(event)
            assert request is not None
            receipts.append(request.emit(log))
        reopened = log.read_all()

    assert [receipt["seq"] for receipt in receipts] == [1, 2]
    evidence_rows = [receipt["evidence"] for receipt in reopened]
    assert all(isinstance(evidence, dict) for evidence in evidence_rows)
    assert [evidence["legacy_seq"] for evidence in evidence_rows if isinstance(evidence, dict)] == [
        11,
        12,
    ]
    assert receipts[1]["prev_receipt"] == receipts[0]["receipt_id"]
