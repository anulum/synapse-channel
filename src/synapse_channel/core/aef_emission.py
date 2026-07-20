# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native Agent Evidence Format receipt emission
"""Emit a native, signed AEF receipt chain without rewriting legacy evidence.

The AEF log has its own sequence, canonical receipt bytes, and hash chain. It may
share a SQLite file with the historical event store, but its tables and Merkle
leaf format remain separate. Runtime dual-write routing is intentionally outside
this module: callers append the unchanged legacy event first, then pass its
durable sequence as ``legacy_seq`` when emitting the corresponding AEF receipt.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from types import TracebackType

from synapse_channel.core.aef_canonical import AefCanonicalizationError, canonical_json
from synapse_channel.core.aef_domain import AEF_RECEIPT_DOMAIN
from synapse_channel.core.aef_time import current_epoch_ms, validate_epoch_ms
from synapse_channel.core.aef_verdict import AefVerdictCode
from synapse_channel.core.aef_verification import (
    AefTrustedKey,
    AefTrustStore,
    receipt_id_for,
    verify_aef_receipt,
)
from synapse_channel.core.errors import SynapseError
from synapse_channel.core.persistence import BUSY_TIMEOUT_MS
from synapse_channel.core.persistence_sqlcipher import connect_event_store
from synapse_channel.core.receipt_signing import ReceiptSigningKey, receipt_key_id

_ALGORITHM = "ed25519"
_AEF_VERSION = "0.1"
_GENESIS_RECEIPT = "aef1:" + "0" * 64
_HEX_64 = re.compile(r"[0-9a-f]{64}")


class AefEmissionError(SynapseError, ValueError):
    """A native receipt could not be built or appended safely."""

    code = "aef_emission"


def derive_aef_log_id(hub_id: str, public_key: bytes) -> str:
    """Derive the v0.1 log id from hub identity, algorithm, and raw key."""
    if not isinstance(hub_id, str) or not hub_id:
        raise AefEmissionError("AEF hub id must be bounded non-empty text")
    try:
        hub_bytes = hub_id.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise AefEmissionError("AEF hub id must be Unicode scalar text") from exc
    if len(hub_bytes) > 4096:
        raise AefEmissionError("AEF hub id must be bounded non-empty text")
    if not isinstance(public_key, bytes) or len(public_key) != 32:
        raise AefEmissionError("AEF log id requires a raw 32-byte Ed25519 public key")
    prefix = b"aef-log:" + hub_bytes + f":{_ALGORITHM}:".encode("ascii")
    return hashlib.sha256(prefix + public_key).hexdigest()


def sign_aef_receipt(
    content: Mapping[str, object], *, signing_key: ReceiptSigningKey
) -> dict[str, object]:
    """Return one content-addressed AEF receipt signed by the hub key."""
    public_key = _public_key_bytes(signing_key)
    if receipt_key_id(public_key) != signing_key.key_id:
        raise AefEmissionError("AEF signing key id does not match its public key")
    receipt = dict(content)
    if "receipt_id" in receipt or "signature" in receipt:
        raise AefEmissionError("AEF receipt content must not predeclare identity or signature")
    receipt["receipt_id"] = receipt_id_for(receipt)
    signature_envelope: dict[str, object] = {
        "alg": _ALGORITHM,
        "domain": str(AEF_RECEIPT_DOMAIN),
        "key_id": signing_key.key_id,
    }
    receipt["signature"] = signature_envelope
    signature = signing_key.private_key.sign(AEF_RECEIPT_DOMAIN.preimage(canonical_json(receipt)))
    signature_envelope["value"] = base64.urlsafe_b64encode(signature).decode("ascii")
    return receipt


class AefReceiptLog:
    """FULL-synchronous append-only native AEF receipt chain."""

    def __init__(
        self,
        path: str | Path,
        *,
        hub_id: str,
        signing_key: ReceiptSigningKey,
        key_file: str | Path | None = None,
        key: bytes | None = None,
    ) -> None:
        self.path = str(path)
        self.hub_id = hub_id
        self.signing_key = signing_key
        public_key = _public_key_bytes(signing_key)
        if receipt_key_id(public_key) != signing_key.key_id:
            raise AefEmissionError("AEF signing key id does not match its public key")
        self.public_key = public_key
        self.log_id = derive_aef_log_id(hub_id, public_key)
        self._trust_store = AefTrustStore(
            keys={signing_key.key_id: AefTrustedKey(public_key)},
            logs={self.log_id: signing_key.key_id},
        )
        self._conn, self._encrypted = connect_event_store(self.path, key=key, key_file=key_file)
        self._lock = threading.Lock()
        try:
            self._configure()
        except BaseException:
            self._conn.close()
            raise

    @property
    def encrypted(self) -> bool:
        """Return whether the log opened through SQLCipher."""
        return self._encrypted

    def append(
        self,
        *,
        receipt_type: str,
        action: str,
        actor_id: str,
        subject: Mapping[str, object],
        issued_at: int | None = None,
        expires_at: int | None = None,
        decision: str | None = None,
        reason_code: str | None = None,
        evidence: Mapping[str, object] | None = None,
        legacy_seq: int | None = None,
        legacy_root: str | None = None,
        legacy_tree_size: int | None = None,
    ) -> dict[str, object]:
        """Build, validate, and atomically append one native receipt.

        ``legacy_root`` and ``legacy_tree_size`` are accepted only together on
        the first receipt. They bind, but never merge, the frozen legacy tree.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                tail = self._conn.execute(
                    "SELECT seq, receipt_id FROM aef_receipts ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq = 1 if tail is None else int(tail[0]) + 1
                prev_receipt = _GENESIS_RECEIPT if tail is None else str(tail[1])
                receipt = self._build_receipt(
                    seq=seq,
                    prev_receipt=prev_receipt,
                    receipt_type=receipt_type,
                    action=action,
                    actor_id=actor_id,
                    subject=subject,
                    issued_at=issued_at,
                    expires_at=expires_at,
                    decision=decision,
                    reason_code=reason_code,
                    evidence=evidence,
                    legacy_seq=legacy_seq,
                    legacy_root=legacy_root,
                    legacy_tree_size=legacy_tree_size,
                )
                canonical = canonical_json(receipt)
                self._conn.execute(
                    "INSERT INTO aef_receipts "
                    "(seq, receipt_id, legacy_seq, canonical_receipt) VALUES (?, ?, ?, ?)",
                    (seq, receipt["receipt_id"], legacy_seq, canonical),
                )
                self._conn.commit()
            except BaseException:
                with contextlib.suppress(BaseException):
                    self._conn.rollback()
                raise
        return receipt

    def read_all(self) -> tuple[dict[str, object], ...]:
        """Read and re-verify every native receipt in sequence order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, receipt_id, legacy_seq, canonical_receipt "
                "FROM aef_receipts ORDER BY seq"
            ).fetchall()
        return self._decode_rows(rows)

    def _decode_rows(self, rows: object) -> tuple[dict[str, object], ...]:
        if not isinstance(rows, list | tuple):
            raise AefEmissionError("stored AEF receipt rows are malformed")
        receipts: list[dict[str, object]] = []
        expected_prev = _GENESIS_RECEIPT
        for row in rows:
            value = self._decode_row(row, expected_prev=expected_prev)
            receipts.append(value)
            expected_prev = str(value["receipt_id"])
        return tuple(receipts)

    def _decode_row(self, row: object, *, expected_prev: str | None) -> dict[str, object]:
        if not isinstance(row, tuple) or len(row) != 4:
            raise AefEmissionError("stored AEF receipt row is malformed")
        seq, receipt_id, legacy_seq, raw_value = row
        raw = bytes(raw_value)
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or canonical_json(value) != raw:
                raise AefEmissionError("stored AEF receipt is not canonical")
        except (AefCanonicalizationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise AefEmissionError("stored AEF receipt is not canonical") from exc
        evidence = value.get("evidence")
        signed_legacy_seq = evidence.get("legacy_seq") if isinstance(evidence, dict) else None
        if (
            value.get("seq") != int(seq)
            or value.get("receipt_id") != str(receipt_id)
            or value.get("log_id") != self.log_id
            or (expected_prev is not None and value.get("prev_receipt") != expected_prev)
            or signed_legacy_seq != legacy_seq
        ):
            raise AefEmissionError("stored AEF receipt chain does not match its index")
        issued_at = value.get("issued_at")
        verdict = verify_aef_receipt(
            value,
            trust_store=self._trust_store,
            now_ms=issued_at if isinstance(issued_at, int) else 0,
        )
        if verdict.verdict is not AefVerdictCode.VALID:
            raise AefEmissionError(
                f"stored AEF receipt failed verification: {verdict.verdict.value}"
            )
        return value

    def count(self) -> int:
        """Return the number of native receipts in this log."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM aef_receipts").fetchone()
        return int(row[0])

    def receipt_for_legacy_seq(self, legacy_seq: int) -> dict[str, object] | None:
        """Return and verify the receipt already bound to ``legacy_seq``."""
        if isinstance(legacy_seq, bool) or not isinstance(legacy_seq, int) or legacy_seq < 1:
            raise AefEmissionError("legacy sequence must be a positive integer")
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, receipt_id, legacy_seq, canonical_receipt "
                "FROM aef_receipts WHERE legacy_seq = ?",
                (legacy_seq,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_row(row, expected_prev=None)

    def close(self) -> None:
        """Close the native log connection."""
        self._conn.close()

    def __enter__(self) -> AefReceiptLog:
        """Return this open log for a context manager."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the log when leaving a context manager."""
        self.close()

    def _configure(self) -> None:
        self._restrict(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS aef_log_metadata ("
            "singleton INTEGER PRIMARY KEY CHECK(singleton = 1), "
            "hub_id TEXT NOT NULL, log_id TEXT NOT NULL, key_id TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS aef_receipts ("
            "seq INTEGER PRIMARY KEY CHECK(seq >= 1), "
            "receipt_id TEXT NOT NULL UNIQUE, "
            "legacy_seq INTEGER UNIQUE, "
            "canonical_receipt BLOB NOT NULL)"
        )
        self._conn.execute(
            "INSERT OR IGNORE INTO aef_log_metadata "
            "(singleton, hub_id, log_id, key_id) VALUES (1, ?, ?, ?)",
            (self.hub_id, self.log_id, self.signing_key.key_id),
        )
        metadata = self._conn.execute(
            "SELECT hub_id, log_id, key_id FROM aef_log_metadata WHERE singleton = 1"
        ).fetchone()
        expected = (self.hub_id, self.log_id, self.signing_key.key_id)
        if metadata is None or tuple(str(value) for value in metadata) != expected:
            self._conn.rollback()
            raise AefEmissionError("AEF log metadata does not match the requested hub and key")
        self._conn.commit()
        self._restrict(f"{self.path}-wal")
        self._restrict(f"{self.path}-shm")

    def _build_receipt(
        self,
        *,
        seq: int,
        prev_receipt: str,
        receipt_type: str,
        action: str,
        actor_id: str,
        subject: Mapping[str, object],
        issued_at: int | None,
        expires_at: int | None,
        decision: str | None,
        reason_code: str | None,
        evidence: Mapping[str, object] | None,
        legacy_seq: int | None,
        legacy_root: str | None,
        legacy_tree_size: int | None,
    ) -> dict[str, object]:
        stamp = current_epoch_ms() if issued_at is None else validate_epoch_ms(issued_at)
        content: dict[str, object] = {
            "aef": _AEF_VERSION,
            "receipt_type": receipt_type,
            "action": action,
            "hub_id": self.hub_id,
            "log_id": self.log_id,
            "seq": seq,
            "issued_at": stamp,
            "actor": {"agent_id": actor_id},
            "subject": dict(subject),
            "prev_receipt": prev_receipt,
        }
        if expires_at is not None:
            content["expires_at"] = validate_epoch_ms(expires_at)
        if decision is not None:
            content["decision"] = decision
        if reason_code is not None:
            content["reason_code"] = reason_code
        evidence_fields = dict(evidence or {})
        if legacy_seq is None and "legacy_seq" in evidence_fields:
            raise AefEmissionError("legacy sequence evidence requires the indexed legacy_seq field")
        if legacy_seq is not None:
            if isinstance(legacy_seq, bool) or not isinstance(legacy_seq, int) or legacy_seq < 1:
                raise AefEmissionError("legacy sequence must be a positive integer")
            existing = evidence_fields.get("legacy_seq")
            if existing is not None and existing != legacy_seq:
                raise AefEmissionError("legacy sequence conflicts with supplied evidence")
            evidence_fields["legacy_seq"] = legacy_seq
        anchor_supplied = legacy_root is not None or legacy_tree_size is not None
        if not anchor_supplied and {
            "legacy_root",
            "legacy_tree_size",
        }.intersection(evidence_fields):
            raise AefEmissionError("legacy anchor evidence requires explicit genesis fields")
        if anchor_supplied:
            if seq != 1:
                raise AefEmissionError("legacy anchor is allowed only on the genesis receipt")
            if (
                not isinstance(legacy_root, str)
                or _HEX_64.fullmatch(legacy_root) is None
                or isinstance(legacy_tree_size, bool)
                or not isinstance(legacy_tree_size, int)
                or legacy_tree_size < 0
                or legacy_tree_size > (1 << 53) - 1
            ):
                raise AefEmissionError("legacy root and tree size must form a canonical anchor")
            if (
                evidence_fields.get("legacy_root", legacy_root) != legacy_root
                or evidence_fields.get("legacy_tree_size", legacy_tree_size) != legacy_tree_size
            ):
                raise AefEmissionError("legacy anchor conflicts with supplied evidence")
            evidence_fields["legacy_root"] = legacy_root
            evidence_fields["legacy_tree_size"] = legacy_tree_size
        if evidence_fields:
            content["evidence"] = evidence_fields
        receipt = sign_aef_receipt(content, signing_key=self.signing_key)
        verdict = verify_aef_receipt(
            receipt,
            trust_store=self._trust_store,
            now_ms=stamp,
        )
        if verdict.verdict is not AefVerdictCode.VALID:
            raise AefEmissionError(
                f"native AEF receipt failed self-verification: {verdict.verdict.value}"
            )
        return receipt

    @staticmethod
    def _restrict(path: str) -> None:
        if path.startswith(":memory:"):
            return
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def _public_key_bytes(signing_key: ReceiptSigningKey) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return signing_key.private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
