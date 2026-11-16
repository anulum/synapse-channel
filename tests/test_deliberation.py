# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deliberation result + sealed export package

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


def _result(
    *,
    deliberation_id: str = "land-e23dcd5-2026-07-16",
    pattern: str = "land_council",
    thesis: str = "land the 2448 residual",
    resolution: str = "PASS",
) -> DeliberationResult:
    """Return one deliberation result with the named fields replaced."""
    return DeliberationResult(
        deliberation_id=deliberation_id,
        pattern=pattern,
        project="SYNAPSE-CHANNEL",
        thesis=thesis,
        resolution=resolution,
        actions=("non-force FF",),
        gate_checks=(GateCheck("G7_seal", "sealed", "receipt:abc"),),
        concluded_at="2026-07-16T01:43:00+02:00",
    )


def _package(
    *,
    result: DeliberationResult | None = None,
    license_tag: str = "oss-ok",
    retention_class: str = "standard",
    train_eligible: bool = False,
    redaction_status: str = "none",
) -> ExportPackage:
    """Return one export package with the named fields replaced."""
    return build_export_package(
        _result() if result is None else result,
        license_tag=license_tag,
        retention_class=retention_class,
        train_eligible=train_eligible,
        source=".coordination/…",
        redaction_status=redaction_status,
    )


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
            if field == "deliberation_id":
                _result(deliberation_id=value)
            elif field == "pattern":
                _result(pattern=value)
            elif field == "thesis":
                _result(thesis=value)
            else:
                _result(resolution=value)

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
        with pytest.raises(DeliberationError, match=match):
            if field == "license_tag":
                _package(license_tag=value)
            else:
                _package(retention_class=value)

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
        package = sealed["package"]
        assert isinstance(package, dict)
        result = package["result"]
        assert isinstance(result, dict)
        result["resolution"] = "BLOCK"
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
        verification = sealed["verification"]
        assert isinstance(verification, dict)
        merkle = verification["merkle"]
        assert isinstance(merkle, dict)
        merkle["scheme"] = "spoofed"
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
        assert "scheme is not recognised" in outcome.reason

    def test_unsigned_package_is_not_authentic(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        sealed = seal_export_package(_package(), key=key)
        verification = sealed["verification"]
        assert isinstance(verification, dict)
        del verification["merkle_signature"]
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
