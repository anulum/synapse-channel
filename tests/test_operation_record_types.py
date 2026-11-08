# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outcome and verification records decoded in the core
"""Decode outcome reports and verification records exactly, refuse everything else."""

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel import execution_outcome, result_verification
from synapse_channel.core.execution_outcome import (
    OUTCOME_SCHEMA,
    ExecutionOutcome,
    OutcomeState,
    outcome_from_json,
)
from synapse_channel.core.result_verification_record import (
    SCHEMA,
    ResultVerification,
    verification_from_json,
)


def _outcome() -> ExecutionOutcome:
    return ExecutionOutcome(
        "op-1",
        "worker_process",
        OutcomeState.CONFIRMED_FAILED,
        "provider_process_exited",
        external_effect=OutcomeState.UNKNOWN,
        stop_requested=True,
        stop_confirmed=True,
    )


def _verification() -> ResultVerification:
    return ResultVerification(
        "op-1",
        "file_change",
        "confirmed_done",
        "observed",
        "verified",
        "content_matches",
        {"sha256": "a" * 64},
    )


def test_edge_modules_re_export_the_core_types() -> None:
    """The edges keep their import paths; the objects are the core's."""
    assert execution_outcome.ExecutionOutcome is ExecutionOutcome
    assert execution_outcome.OutcomeState is OutcomeState
    assert execution_outcome.OUTCOME_SCHEMA == OUTCOME_SCHEMA
    assert execution_outcome.outcome_from_json is outcome_from_json
    assert result_verification.ResultVerification is ResultVerification
    assert result_verification.SCHEMA == SCHEMA
    assert result_verification.verification_from_json is verification_from_json


def test_an_outcome_report_round_trips_and_ignores_derived_fields() -> None:
    """A producer's report decodes to the same record; extras add nothing."""
    report: dict[str, Any] = dict(_outcome().to_json())
    report["returncode"] = -9
    report["launcher"] = {"pid": 1}
    assert report["recovery"] == "none"
    assert outcome_from_json(report) == _outcome()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "other"}, "not a synapse.execution-outcome.v1"),
        ({"operation_id": 7}, "operation_id must be text"),
        ({"observation": None}, "observation must be text"),
        ({"state": "finished"}, "not one of the five states"),
        ({"state": 3}, "state must be text"),
        ({"external_effect": "maybe"}, "not one of the five states"),
        ({"boundary": "socket"}, "unknown execution outcome boundary"),
        ({"stop_requested": 1}, "stop_requested must be a boolean"),
        ({"stop_confirmed": "yes"}, "stop_confirmed must be a boolean"),
        ({"effects_undone": None}, "effects_undone must be a boolean"),
        ({"stop_requested": False}, "stop confirmation requires"),
        ({"observation": "line\nbreak"}, "control characters"),
        ({"operation_id": ""}, "bounded text"),
    ],
)
def test_an_outcome_report_with_a_bad_field_is_refused(
    change: dict[str, object], message: str
) -> None:
    """Each malformed field is refused by the parser or by the record's own rules."""
    report = {**_outcome().to_json(), **change}
    with pytest.raises(ValueError, match=message):
        outcome_from_json(report)


def test_an_outcome_report_must_be_an_object() -> None:
    """A list or a string is not a report."""
    documents: list[object] = [[], "report", None]
    for document in documents:
        with pytest.raises(ValueError, match="must be a JSON object"):
            outcome_from_json(document)


def test_a_verification_record_round_trips() -> None:
    """A verifier's record decodes to the same record."""
    assert verification_from_json(_verification().to_json()) == _verification()


def test_a_verification_record_without_evidence_has_empty_evidence() -> None:
    """Evidence is optional and defaults to empty."""
    record = dict(_verification().to_json())
    del record["evidence"]
    assert verification_from_json(record).evidence == {}


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": "other"}, "not a synapse.result-verification.v1"),
        ({"verifier": ""}, "verifier must be non-empty text"),
        ({"result": 1}, "result must be non-empty text"),
        ({"effect": "seen"}, "unknown effect or result state"),
        ({"result": "passed"}, "unknown effect or result state"),
        ({"effect": "absent"}, "verified only from an observed effect"),
        ({"evidence": ["sha256"]}, "evidence must map text to text"),
        ({"evidence": {"sha256": 5}}, "evidence must map text to text"),
    ],
)
def test_a_verification_record_with_a_bad_field_is_refused(
    change: dict[str, object], message: str
) -> None:
    """Each malformed field is refused by the parser or by the record's own rules."""
    record = {**_verification().to_json(), **change}
    with pytest.raises(ValueError, match=message):
        verification_from_json(record)


def test_a_verification_record_must_be_an_object() -> None:
    """A list or a string is not a record."""
    documents: list[object] = [[], "record", None]
    for document in documents:
        with pytest.raises(ValueError, match="must be a JSON object"):
            verification_from_json(document)
