# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — AOT deliberation result + sealed export package (AOT-D1)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.core.deliberation import (
    AOT_EXPORT_VERSION,
    DeliberationError,
    DeliberationResult,
    ExportPackage,
    GateCheck,
    build_export_package,
    content_commitment,
    seal_export_package,
    verify_sealed_package,
)
from synapse_channel.core.receipt_signing import (
    ReceiptSigningKey,
    generate_receipt_signing_key,
    load_receipt_signing_key,
)


def _result(**overrides: object) -> DeliberationResult:
    base: dict[str, object] = {
        "deliberation_id": "land-e23dcd5-2026-07-16",
        "pattern": "land_council",
        "project": "SYNAPSE-CHANNEL",
        "thesis": "land the 2448 residual",
        "resolution": "PASS",
        "actions": ("non-force FF",),
        "gate_checks": (GateCheck("G7_seal", "sealed", "receipt:abc"),),
        "concluded_at": "2026-07-16T01:43:00+02:00",
    }
    base.update(overrides)
    return DeliberationResult(**base)  # type: ignore[arg-type]


def _package(**overrides: object) -> ExportPackage:
    # ``result`` travels in the overrides bag so a dynamic ``**{field: value}``
    # unpack cannot collide with a typed positional parameter under strict mypy.
    result = overrides.pop("result", None)
    if not isinstance(result, DeliberationResult):
        result = _result()
    base: dict[str, object] = {
        "license_tag": "oss-ok",
        "retention_class": "standard",
        "source": ".coordination/…",
    }
    base.update(overrides)
    return build_export_package(result, **base)  # type: ignore[arg-type]


@pytest.fixture
def signing(tmp_path: Path) -> tuple[ReceiptSigningKey, dict[str, bytes]]:
    verkey = generate_receipt_signing_key(tmp_path / "receipt-key")
    signkey = load_receipt_signing_key(tmp_path / "receipt-key")
    return signkey, {verkey.key_id: verkey.public_key}


class TestGateCheck:
    def test_as_dict_round_trips_the_fields(self) -> None:
        assert GateCheck("G1_secret", "pass", "floor").as_dict() == {
            "gate": "G1_secret",
            "status": "pass",
            "evidence": "floor",
        }

    def test_evidence_defaults_empty(self) -> None:
        assert GateCheck("G3_memory", "n/a").as_dict()["evidence"] == ""


class TestDeliberationResult:
    def test_valid_result_exposes_canonical_content_in_fixed_order(self) -> None:
        content = _result().canonical_content()
        assert list(content) == [
            "deliberation_id",
            "pattern",
            "project",
            "thesis",
            "resolution",
            "objections",
            "actions",
            "claims_needed",
            "open_questions",
            "evidence_links",
            "gate_checks",
            "findings",
            "concluded_at",
            "source_clock",
        ]
        assert content["gate_checks"] == [
            {"gate": "G7_seal", "status": "sealed", "evidence": "receipt:abc"}
        ]
        assert content["findings"] == []

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("deliberation_id", "  ", "deliberation_id"),
            ("pattern", "gossip", "unknown pattern"),
            ("thesis", "", "thesis"),
            ("resolution", "   ", "resolution"),
        ],
    )
    def test_malformed_result_is_refused(self, field: str, value: str, match: str) -> None:
        with pytest.raises(DeliberationError, match=match):
            _result(**{field: value})

    @pytest.mark.parametrize("pattern", ["land_council", "audit_council", "research_council"])
    def test_every_known_pattern_is_accepted(self, pattern: str) -> None:
        assert _result(pattern=pattern).pattern == pattern


class TestExportPackage:
    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [
            ("license_tag", "public", "unknown license_tag"),
            ("retention_class", "forever", "unknown retention_class"),
        ],
    )
    def test_malformed_tags_are_refused(self, field: str, value: str, match: str) -> None:
        overrides: dict[str, object] = {field: value}
        with pytest.raises(DeliberationError, match=match):
            _package(**overrides)

    def test_train_eligible_fails_closed_without_passing_redaction(self) -> None:
        package = _package(train_eligible=True, redaction_status="none")
        assert package.train_eligible is False

    def test_train_eligible_holds_only_with_passing_redaction(self) -> None:
        package = _package(train_eligible=True, redaction_status="pass")
        assert package.train_eligible is True

    def test_train_eligible_fails_closed_on_failed_redaction(self) -> None:
        package = _package(train_eligible=True, redaction_status="fail")
        assert package.train_eligible is False

    def test_canonical_content_stamps_the_version(self) -> None:
        assert _package().canonical_content()["aot_version"] == AOT_EXPORT_VERSION


class TestContentCommitment:
    def test_commitment_is_deterministic(self) -> None:
        assert content_commitment(_package()) == content_commitment(_package())

    def test_commitment_changes_with_content(self) -> None:
        one = content_commitment(_package())
        other = content_commitment(_package(result=_result(resolution="BLOCK")))
        assert one["root"] != other["root"]

    def test_commitment_declares_scheme_and_algorithm(self) -> None:
        commitment = content_commitment(_package())
        assert commitment["scheme"] == "aot-deliberation-content-v0"
        assert commitment["algorithm"] == "sha256"


class TestSealAndVerify:
    def test_seal_produces_a_receipt_shaped_document(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, _trusted = signing
        sealed = seal_export_package(_package(), key=key)
        assert sealed["aot_version"] == AOT_EXPORT_VERSION
        assert "package" in sealed
        verification = sealed["verification"]
        assert isinstance(verification, dict)
        assert "merkle" in verification
        assert "merkle_signature" in verification

    def test_sealed_package_verifies(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is True
        assert outcome.content_bound is True
        assert outcome.signature.status == "pass"

    def test_verification_survives_a_json_round_trip(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        reloaded = json.loads(json.dumps(sealed))
        assert verify_sealed_package(reloaded, trusted_keys=trusted).ok is True

    def test_tampered_body_fails_content_binding(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        # Alter the body after signing; the signature over the commitment still
        # verifies, but the committed root no longer matches the body.
        sealed["package"]["result"]["resolution"] = "BLOCK"  # type: ignore[index]
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
        assert outcome.content_bound is False
        assert "does not match" in outcome.reason

    def test_untrusted_key_fails_signature(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]], tmp_path: Path
    ) -> None:
        key, _trusted = signing
        sealed = seal_export_package(_package(), key=key)
        other_ver = generate_receipt_signing_key(tmp_path / "other-key")
        outcome = verify_sealed_package(
            sealed, trusted_keys={other_ver.key_id: other_ver.public_key}
        )
        assert outcome.ok is False
        assert outcome.signature.status == "fail"

    def test_missing_commitment_fails_closed(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        _key, trusted = signing
        outcome = verify_sealed_package(
            {"aot_version": AOT_EXPORT_VERSION, "package": {}}, trusted_keys=trusted
        )
        assert outcome.ok is False
        assert "missing its commitment" in outcome.reason

    def test_unrecognised_scheme_fails_closed(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        sealed["verification"]["merkle"]["scheme"] = "spoofed"  # type: ignore[index]
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
        assert "scheme is not recognised" in outcome.reason

    def test_unsigned_package_is_not_authentic(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        del sealed["verification"]["merkle_signature"]  # type: ignore[attr-defined]
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
