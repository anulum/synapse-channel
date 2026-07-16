# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — audit-council Finding sub-schema (AOT-D3)

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.deliberation import (
    DISPOSITIONS,
    SEVERITIES,
    DeliberationError,
    DeliberationResult,
    Finding,
    build_export_package,
    seal_export_package,
    verify_sealed_package,
)
from synapse_channel.core.receipt_signing import (
    ReceiptSigningKey,
    generate_receipt_signing_key,
    load_receipt_signing_key,
)


def _finding(**overrides: str) -> Finding:
    base = {
        "finding_id": "SEC-01",
        "severity": "high",
        "summary": "loopback read left open",
        "location": "dashboard.py:210",
        "evidence": "sha:abc",
        "disposition": "confirmed",
    }
    base.update(overrides)
    return Finding(**base)


def _audit_result(*findings: Finding) -> DeliberationResult:
    return DeliberationResult(
        deliberation_id="audit-0266472-2026-07-16",
        pattern="audit_council",
        project="SYNAPSE-CHANNEL",
        thesis="review the read-gate surface",
        resolution="2 confirmed, 1 dismissed",
        findings=tuple(findings),
    )


@pytest.fixture
def signing(tmp_path: Path) -> tuple[ReceiptSigningKey, dict[str, bytes]]:
    verkey = generate_receipt_signing_key(tmp_path / "receipt-key")
    signkey = load_receipt_signing_key(tmp_path / "receipt-key")
    return signkey, {verkey.key_id: verkey.public_key}


class TestFinding:
    def test_as_dict_round_trips_in_fixed_order(self) -> None:
        assert list(_finding().as_dict()) == [
            "finding_id",
            "severity",
            "summary",
            "location",
            "evidence",
            "disposition",
        ]

    def test_defaults_are_open_and_empty(self) -> None:
        finding = Finding("F1", "low", "a nit")
        assert finding.location == ""
        assert finding.evidence == ""
        assert finding.disposition == "open"

    def test_empty_id_is_refused(self) -> None:
        with pytest.raises(DeliberationError, match="finding_id"):
            _finding(finding_id="   ")

    def test_empty_summary_is_refused(self) -> None:
        with pytest.raises(DeliberationError, match="summary"):
            _finding(summary="")

    def test_unknown_severity_is_refused(self) -> None:
        with pytest.raises(DeliberationError, match="unknown severity"):
            _finding(severity="apocalyptic")

    def test_unknown_disposition_is_refused(self) -> None:
        with pytest.raises(DeliberationError, match="unknown disposition"):
            _finding(disposition="maybe")

    @pytest.mark.parametrize("severity", sorted(SEVERITIES))
    def test_every_known_severity_is_accepted(self, severity: str) -> None:
        assert _finding(severity=severity).severity == severity

    @pytest.mark.parametrize("disposition", sorted(DISPOSITIONS))
    def test_every_known_disposition_is_accepted(self, disposition: str) -> None:
        assert _finding(disposition=disposition).disposition == disposition


class TestFindingsInResult:
    def test_findings_appear_in_canonical_content(self) -> None:
        content = _audit_result(_finding(), _finding(finding_id="SEC-02")).canonical_content()
        assert [f["finding_id"] for f in content["findings"]] == ["SEC-01", "SEC-02"]

    def test_default_result_has_no_findings(self) -> None:
        assert _audit_result().canonical_content()["findings"] == []

    def test_sealed_audit_package_with_findings_verifies(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        package = build_export_package(
            _audit_result(_finding()), license_tag="internal-ops", retention_class="long"
        )
        sealed = seal_export_package(package, key=key)
        assert verify_sealed_package(sealed, trusted_keys=trusted).ok is True

    def test_tampering_a_finding_breaks_the_seal(
        self, signing: tuple[ReceiptSigningKey, dict[str, bytes]]
    ) -> None:
        key, trusted = signing
        package = build_export_package(
            _audit_result(_finding(disposition="confirmed")),
            license_tag="internal-ops",
            retention_class="long",
        )
        sealed = seal_export_package(package, key=key)
        # Flip a confirmed finding to dismissed after signing.
        sealed["package"]["result"]["findings"][0]["disposition"] = "dismissed"  # type: ignore[index]
        outcome = verify_sealed_package(sealed, trusted_keys=trusted)
        assert outcome.ok is False
        assert outcome.content_bound is False
