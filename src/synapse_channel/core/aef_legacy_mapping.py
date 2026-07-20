# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit legacy-event to AEF receipt mapping
"""Map supported legacy evidence rows into native AEF emission requests.

This module is a compatibility boundary, not a historical reinterpretation.
The legacy row remains authoritative and byte-unchanged; a mapped request names
its durable sequence only as reconciliation evidence. Unsupported event kinds
return ``None`` and malformed supported rows fail closed instead of inventing
missing semantics.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.aef_emission import AefReceiptLog
from synapse_channel.core.aef_time import legacy_seconds_to_epoch_ms
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import StoredEvent

_HEX_64 = re.compile(r"[0-9a-f]{64}")
_MAX_TEXT_BYTES = 4096

AEF_MAPPED_EVENT_KINDS = frozenset(
    {
        EventKind.CLAIM,
        EventKind.CLAIM_DENIAL,
        EventKind.GUARD_DENIAL,
        EventKind.SANDBOX_RUN,
        EventKind.MULTIHUB_PARTITION,
        EventKind.MULTIHUB_HEAL,
    }
)
"""Legacy kinds with an explicit native AEF v0.1 projection."""


class AefLegacyMappingError(SynapseError, ValueError):
    """A supported legacy event cannot be represented truthfully as AEF."""

    code = "aef_legacy_mapping"


@dataclass(frozen=True, slots=True)
class AefEmissionRequest:
    """One validated mapping ready for the native AEF log."""

    legacy_seq: int
    receipt_type: str
    action: str
    actor_id: str
    subject: dict[str, object]
    issued_at: int
    expires_at: int | None = None
    decision: str | None = None
    reason_code: str | None = None
    evidence: dict[str, object] | None = None

    def emit(self, log: AefReceiptLog) -> dict[str, object]:
        """Append this request to ``log`` with its signed legacy cursor."""
        return log.append(
            receipt_type=self.receipt_type,
            action=self.action,
            actor_id=self.actor_id,
            subject=self.subject,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            decision=self.decision,
            reason_code=self.reason_code,
            evidence=self.evidence,
            legacy_seq=self.legacy_seq,
        )

    def matches(self, receipt: dict[str, object]) -> bool:
        """Return whether ``receipt`` is the exact native projection of this request."""
        evidence = dict(self.evidence or {})
        evidence["legacy_seq"] = self.legacy_seq
        expected: dict[str, object] = {
            "receipt_type": self.receipt_type,
            "action": self.action,
            "actor": {"agent_id": self.actor_id},
            "subject": self.subject,
            "issued_at": self.issued_at,
            "evidence": evidence,
        }
        if self.expires_at is not None:
            expected["expires_at"] = self.expires_at
        if self.decision is not None:
            expected["decision"] = self.decision
        if self.reason_code is not None:
            expected["reason_code"] = self.reason_code
        return all(receipt.get(key) == value for key, value in expected.items())


def legacy_event_to_aef(event: StoredEvent) -> AefEmissionRequest | None:
    """Return the explicit AEF mapping for one supported durable event.

    The supported set is deliberately narrow: lease grants and denials,
    digest-only guard denials, sandbox executions, and durable multi-hub
    partition/heal transitions. Other rows stay legacy-only until their AEF
    semantics and data-minimisation profile are specified.
    """
    if isinstance(event.seq, bool) or not isinstance(event.seq, int) or event.seq < 1:
        raise AefLegacyMappingError("legacy event sequence must be a positive integer")
    issued_at = legacy_seconds_to_epoch_ms(event.ts)
    payload = event.payload
    if event.kind == EventKind.CLAIM:
        return _claim(event.seq, issued_at, payload)
    if event.kind == EventKind.CLAIM_DENIAL:
        return _claim_denial(event.seq, issued_at, payload)
    if event.kind == EventKind.GUARD_DENIAL:
        return _guard_denial(event.seq, issued_at, payload)
    if event.kind == EventKind.SANDBOX_RUN:
        return _sandbox_run(event.seq, issued_at, payload)
    if event.kind in {EventKind.MULTIHUB_PARTITION, EventKind.MULTIHUB_HEAL}:
        return _federation(event.seq, issued_at, event.kind, payload)
    return None


def _claim(seq: int, issued_at: int, payload: dict[str, Any]) -> AefEmissionRequest:
    actor = _text(payload, "owner")
    task_id = _text(payload, "task_id")
    epoch = _nonnegative_int(payload, "epoch")
    lease_expires_at = legacy_seconds_to_epoch_ms(_number(payload, "lease_expires_at"))
    subject: dict[str, object] = {
        "task_id": task_id,
        "epoch": epoch,
        "lease_expires_at": lease_expires_at,
    }
    worktree = payload.get("worktree")
    if worktree:
        subject["worktree"] = _bounded_text(worktree, "worktree")
    paths = payload.get("paths")
    if paths is not None:
        subject["paths"] = _text_list(paths, "paths")
    version = payload.get("version")
    if version is not None:
        subject["version"] = _nonnegative_value(version, "version")
    status = payload.get("status")
    if status:
        subject["status"] = _bounded_text(status, "status")
    return AefEmissionRequest(
        legacy_seq=seq,
        receipt_type="lease",
        action="grant",
        actor_id=actor,
        subject=subject,
        issued_at=issued_at,
        expires_at=lease_expires_at,
        decision="allow",
        evidence={"legacy_event_kind": EventKind.CLAIM},
    )


def _claim_denial(seq: int, issued_at: int, payload: dict[str, Any]) -> AefEmissionRequest:
    task_digest = _digest(payload, "task_id_sha256")
    claimant_digest = _digest(payload, "claimant_sha256")
    evidence = _digest_evidence(
        payload,
        "claimant_sha256",
        "scope_sha256",
        "task_id_sha256",
    )
    evidence["legacy_event_kind"] = EventKind.CLAIM_DENIAL
    evidence["identifier_profile"] = "digest-only"
    return AefEmissionRequest(
        legacy_seq=seq,
        receipt_type="lease",
        action="deny",
        actor_id=f"sha256:{claimant_digest}",
        subject={
            "task_id": f"sha256:{task_digest}",
            "holder": "undisclosed:legacy-minimized",
        },
        issued_at=issued_at,
        decision="deny",
        reason_code=_text(payload, "reason_code"),
        evidence=evidence,
    )


def _guard_denial(seq: int, issued_at: int, payload: dict[str, Any]) -> AefEmissionRequest:
    actor_digest = _digest(payload, "actor_sha256")
    call_digest = _digest(payload, "call_sha256")
    evidence = _digest_evidence(
        payload,
        "scope_sha256",
        "credential_principal_sha256",
        "recorder_sha256",
    )
    evidence["legacy_event_kind"] = EventKind.GUARD_DENIAL
    evidence["path_count"] = _nonnegative_int(payload, "path_count")
    return AefEmissionRequest(
        legacy_seq=seq,
        receipt_type="tool_call",
        action="guard_eval",
        actor_id=f"sha256:{actor_digest}",
        subject={
            "tool": _text(payload, "provider"),
            "guard": "claim-guard",
            "call_id": f"sha256:{call_digest}",
        },
        issued_at=issued_at,
        decision="deny",
        reason_code=_text(payload, "reason_code"),
        evidence=evidence,
    )


def _sandbox_run(seq: int, issued_at: int, payload: dict[str, Any]) -> AefEmissionRequest:
    subject: dict[str, object] = {
        "tool": _text(payload, "tool_id"),
        "guard": "wasm-sandbox",
        "call_id": f"legacy-event:{seq}",
        "inputs_digest": _digest_text(payload, "inputs_digest"),
        "output_digest": _digest_text(payload, "output_digest"),
        "exit": _text(payload, "exit"),
        "granted_capabilities": _text_list(
            payload.get("granted_capabilities"), "granted_capabilities"
        ),
    }
    evidence: dict[str, object] = {
        "legacy_event_kind": EventKind.SANDBOX_RUN,
        "content_digest": _digest_text(payload, "content_digest"),
        "fuel_used": _nonnegative_int(payload, "fuel_used"),
    }
    return AefEmissionRequest(
        legacy_seq=seq,
        receipt_type="tool_call",
        action="execute",
        actor_id="SynapseHub",
        subject=subject,
        issued_at=issued_at,
        evidence=evidence,
    )


def _federation(seq: int, issued_at: int, kind: str, payload: dict[str, Any]) -> AefEmissionRequest:
    namespace = _text(payload, "namespace")
    actor = _text(payload, "local_hub_id")
    raw_peers = (
        payload.get("contesting_hubs")
        if kind == EventKind.MULTIHUB_PARTITION
        else payload.get("previous_contesting_hubs")
    )
    peers = sorted(set(_text_list(raw_peers, "contesting_hubs")))
    if not peers:
        raise AefLegacyMappingError("legacy federation transition must name a peer")
    peer_domain = peers[0] if len(peers) == 1 else _peer_set_id(peers)
    subject: dict[str, object] = {
        "peer_domain": peer_domain,
        "namespace": namespace,
        "direction": "in",
    }
    evidence: dict[str, object] = {
        "legacy_event_kind": kind,
        "peer_domains": peers,
    }
    if kind == EventKind.MULTIHUB_PARTITION:
        return AefEmissionRequest(
            legacy_seq=seq,
            receipt_type="federation",
            action="partition_mark",
            actor_id=actor,
            subject=subject,
            issued_at=issued_at,
            decision="deny",
            reason_code="NAMESPACE_OWNERSHIP_CONFLICT",
            evidence=evidence,
        )
    refreshed = payload.get("observation_refreshed")
    if refreshed is not True:
        raise AefLegacyMappingError("legacy heal must prove a refreshed observation")
    evidence["ownership_observation_refreshed"] = True
    return AefEmissionRequest(
        legacy_seq=seq,
        receipt_type="federation",
        action="heal",
        actor_id=actor,
        subject=subject,
        issued_at=issued_at,
        evidence=evidence,
    )


def _peer_set_id(peers: list[str]) -> str:
    canonical = json.dumps(peers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "peer-set:sha256:" + hashlib.sha256(canonical).hexdigest()


def _digest_evidence(payload: dict[str, Any], *keys: str) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for key in keys:
        value = payload.get(key)
        if value is not None:
            evidence[key] = _digest_value(value, key)
    return evidence


def _digest_value(value: object, label: str) -> str:
    if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
        raise AefLegacyMappingError(f"legacy {label} must be lowercase SHA-256")
    return value


def _digest(payload: dict[str, Any], key: str) -> str:
    return _digest_value(payload.get(key), key)


def _digest_text(payload: dict[str, Any], key: str) -> str:
    value = _text(payload, key)
    if not value.startswith("sha256:") or _HEX_64.fullmatch(value[7:]) is None:
        raise AefLegacyMappingError(f"legacy {key} must be sha256:<lowercase hex>")
    return value


def _text(payload: dict[str, Any], key: str) -> str:
    return _bounded_text(payload.get(key), key)


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise AefLegacyMappingError(f"legacy {label} must be bounded non-empty text")
    return value


def _text_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 1024:
        raise AefLegacyMappingError(f"legacy {label} must be a bounded text list")
    return [_bounded_text(item, label) for item in value]


def _number(payload: dict[str, Any], key: str) -> int | float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise AefLegacyMappingError(f"legacy {key} must be numeric")
    return value


def _nonnegative_int(payload: dict[str, Any], key: str) -> int:
    return _nonnegative_value(payload.get(key), key)


def _nonnegative_value(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AefLegacyMappingError(f"legacy {label} must be a non-negative integer")
    return value
