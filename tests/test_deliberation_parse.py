# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — export_package_from_mapping parser regressions (AOT-D1)

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.deliberation import (
    DeliberationError,
    ExportPackage,
    export_package_from_mapping,
)


def _spec(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "deliberation_id": "research-aot-2026-07-16",
        "pattern": "research_council",
        "project": "SYNAPSE-CHANNEL",
        "thesis": "AOT as world-class internal capability",
        "resolution": "build it under the bar",
        "objections": ["market lens is secondary"],
        "actions": ["build the fidelity loop"],
        "gate_checks": [{"gate": "G7_seal", "status": "sealed", "evidence": "receipt:x"}],
        "license_tag": "internal-ops",
        "retention_class": "long",
    }
    base.update(overrides)
    return base


def test_valid_mapping_parses_into_a_package() -> None:
    package = export_package_from_mapping(_spec())
    assert isinstance(package, ExportPackage)
    assert package.result.deliberation_id == "research-aot-2026-07-16"
    assert package.result.objections == ("market lens is secondary",)
    assert package.result.gate_checks[0].gate == "G7_seal"
    assert package.license_tag == "internal-ops"


def test_non_mapping_is_refused() -> None:
    with pytest.raises(DeliberationError, match="must be a JSON object"):
        export_package_from_mapping(["not", "a", "mapping"])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "deliberation_id",
        "pattern",
        "project",
        "thesis",
        "resolution",
        "license_tag",
        "retention_class",
    ],
)
def test_missing_required_field_is_refused(field: str) -> None:
    spec = _spec()
    del spec[field]
    with pytest.raises(DeliberationError):
        export_package_from_mapping(spec)


def test_list_field_of_wrong_type_is_refused() -> None:
    with pytest.raises(DeliberationError, match="objections"):
        export_package_from_mapping(_spec(objections="not-a-list"))


def test_list_field_with_non_string_item_is_refused() -> None:
    with pytest.raises(DeliberationError, match="actions"):
        export_package_from_mapping(_spec(actions=["ok", 3]))


def test_gate_checks_must_be_a_list() -> None:
    with pytest.raises(DeliberationError, match="gate_checks"):
        export_package_from_mapping(_spec(gate_checks="G7"))


def test_gate_check_entry_must_be_an_object() -> None:
    with pytest.raises(DeliberationError, match="gate/status"):
        export_package_from_mapping(_spec(gate_checks=["G7"]))


def test_gate_check_fields_must_be_strings() -> None:
    with pytest.raises(DeliberationError, match="gate_check"):
        export_package_from_mapping(_spec(gate_checks=[{"gate": "G7", "status": 1}]))


def test_train_eligible_must_be_a_boolean() -> None:
    with pytest.raises(DeliberationError, match="train_eligible"):
        export_package_from_mapping(_spec(train_eligible="yes"))


def test_train_eligible_fails_closed_without_passing_redaction() -> None:
    package = export_package_from_mapping(_spec(train_eligible=True, redaction_status="none"))
    assert package.train_eligible is False


def test_train_eligible_holds_with_passing_redaction() -> None:
    package = export_package_from_mapping(_spec(train_eligible=True, redaction_status="pass"))
    assert package.train_eligible is True


def test_optional_fields_default_when_absent() -> None:
    package = export_package_from_mapping(_spec())
    assert package.result.claims_needed == ()
    assert package.result.concluded_at == ""
    assert package.redaction_status == "none"
    assert package.source == ""


def test_unknown_pattern_is_refused_by_schema_validation() -> None:
    with pytest.raises(DeliberationError, match="unknown pattern"):
        export_package_from_mapping(_spec(pattern="chat"))


def test_non_string_concluded_at_is_refused() -> None:
    with pytest.raises(DeliberationError, match="concluded_at"):
        export_package_from_mapping(_spec(concluded_at=12345))
