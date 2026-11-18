# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the application receipt's own version and its refused directory
"""The application receipt moves to ``synapse-setup-receipt.v2``; other setup documents stay v1.

A v2 receipt always carries ``refusal``: null on success, or the refused directory with its
reason, mode and one remedy. The verifier reads a v1 success under v1's fields and a v2 success
with a null refusal; it never reads a refusal as success, and no other setup reader accepts the
receipt's marker on its own document.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from synapse_channel.cli_setup import _print_receipt, _refusal_line
from synapse_channel.setup_authorization import validate_setup_authorization, validate_setup_plan
from synapse_channel.setup_contract import (
    SETUP_RECEIPT_SCHEMA_VERSION,
    SETUP_SCHEMA_VERSION,
    document_digest,
    setup_schema,
)
from synapse_channel.setup_planner import build_setup_plan
from synapse_channel.setup_profiles import get_setup_profile
from synapse_channel.setup_verification import (
    SetupVerificationError,
    validate_application_receipt,
    validate_verification_authorization,
    validate_verification_plan,
)
from test_setup_verification import inspection, setup_documents

WRITABLE = {
    "path": "/home/someone/.local",
    "reason": "group_or_world_writable",
    "mode": "0775",
    "remedy": "remove_group_other_write",
}
FOREIGN = {"path": "/srv/shared", "reason": "foreign_owner", "mode": "0775", "remedy": "none"}
NOT_A_DIRECTORY = {
    "path": "/home/someone/synapse",
    "reason": "not_a_directory",
    "mode": "0640",
    "remedy": "none",
}


def _resigned(receipt: dict[str, object]) -> dict[str, object]:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    return {**unsigned, "receipt_digest": document_digest(unsigned)}


def _v2(receipt: dict[str, object], refusal: dict[str, str] | None = None) -> dict[str, object]:
    return _resigned(
        {**receipt, "schema_version": SETUP_RECEIPT_SCHEMA_VERSION, "refusal": refusal}
    )


def test_the_receipt_has_its_own_version_and_the_contract_stays_v1() -> None:
    assert (SETUP_SCHEMA_VERSION, SETUP_RECEIPT_SCHEMA_VERSION) == (
        "synapse-setup.v1",
        "synapse-setup-receipt.v2",
    )


def test_the_verifier_reads_a_v1_success_and_a_v2_success_and_both_are_schema_valid() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    plan, authorization, receipt, _plan, _authorization = setup_documents()
    assert receipt["schema_version"] == SETUP_SCHEMA_VERSION
    assert "refusal" not in receipt
    second = _v2(receipt)
    assert validate_application_receipt(plan, authorization, receipt) == receipt
    assert validate_application_receipt(plan, authorization, second) == second
    for document in (receipt, second):
        jsonschema.validate(document, setup_schema())


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": SETUP_RECEIPT_SCHEMA_VERSION},
        {"refusal": None},
        {"schema_version": "synapse-setup.v2", "refusal": None},
        {"schema_version": SETUP_RECEIPT_SCHEMA_VERSION, "refusal": WRITABLE},
        {"schema_version": SETUP_RECEIPT_SCHEMA_VERSION, "refusal": FOREIGN},
    ],
)
def test_the_verifier_refuses_mixed_versions_and_never_reads_a_refusal_as_success(
    changes: dict[str, object],
) -> None:
    plan, authorization, receipt, _plan, _authorization = setup_documents()
    changed = _resigned({**receipt, **changes})
    with pytest.raises(SetupVerificationError, match="invalid_application_receipt"):
        validate_application_receipt(plan, authorization, changed)


def test_the_schema_refuses_a_success_with_a_refusal_and_a_remedy_beyond_its_reason() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    _plan_document, _authorization_document, receipt, _plan, _authorization = setup_documents()
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_v2(receipt, WRITABLE), setup_schema())
    recovered = {
        **receipt,
        "outcome": "recovered",
        "ledger_state": "recovered",
        "recovery": "complete",
        "effect_receipt_digest": "a" * 64,
        "effects": [],
    }
    for refusal in (WRITABLE, FOREIGN, NOT_A_DIRECTORY):
        jsonschema.validate(_v2(recovered, refusal), setup_schema())
    for bad in (
        {**FOREIGN, "remedy": "remove_group_other_write"},
        {**WRITABLE, "remedy": "none"},
        {**WRITABLE, "path": "~/.local"},
        {**WRITABLE, "mode": "775"},
        {**WRITABLE, "command": "chmod go-w /home/someone/.local"},
    ):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(_v2(recovered, bad), setup_schema())


def test_no_other_setup_reader_accepts_the_receipts_marker_on_its_own_document() -> None:
    plan, authorization, _receipt, verification_plan, verification_authorization = setup_documents()
    profile = get_setup_profile("local-single-user")
    assert profile is not None
    marked = {"schema_version": SETUP_RECEIPT_SCHEMA_VERSION}
    with pytest.raises(ValueError):
        build_setup_plan(profile, {**inspection(waiter="fail"), **marked})
    with pytest.raises(ValueError):
        validate_setup_plan({**plan, **marked})
    issued_at = cast(int, authorization["issued_at"])
    with pytest.raises(ValueError):
        validate_setup_authorization(plan, {**authorization, **marked}, now=issued_at)
    with pytest.raises(ValueError):
        validate_verification_plan({**verification_plan, **marked})
    with pytest.raises(ValueError):
        validate_verification_authorization(
            verification_plan,
            {**verification_authorization, **marked},
            now=cast(int, verification_authorization["issued_at"]),
        )


@pytest.mark.parametrize(
    ("refusal", "line"),
    [
        (
            WRITABLE,
            "refused: /home/someone/.local is writable by group or others (mode 0775); "
            "fix: chmod go-w -- /home/someone/.local",
        ),
        (
            FOREIGN,
            "refused: /srv/shared belongs to another user (mode 0775); setup changes nothing there",
        ),
        (
            NOT_A_DIRECTORY,
            "refused: /home/someone/synapse is not a plain directory (mode 0640); "
            "setup changes nothing there",
        ),
    ],
)
def test_the_command_line_names_the_directory_and_offers_a_fix_only_where_one_is_safe(
    refusal: dict[str, str], line: str
) -> None:
    assert _refusal_line(cast(dict[str, object], refusal)) == line


def test_the_fix_quotes_the_path_and_makes_terminal_controls_visible() -> None:
    awkward = {**WRITABLE, "path": "/home/some one/.local\x1b[2J"}
    line = _refusal_line(cast(dict[str, object], awkward))
    assert "\x1b" not in line
    assert "chmod go-w -- '/home/some one/.local" in line


@pytest.mark.parametrize("as_json", [True, False])
def test_a_receipt_with_a_refusal_prints_one_stderr_line_in_both_modes(
    as_json: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    receipt: dict[str, Any] = {
        "document_kind": "application_receipt",
        "profile": "local-single-user",
        "profile_version": 1,
        "outcome": "recovered",
        "receipt_digest": "b" * 64,
        "ledger_state": "recovered",
        "refusal": WRITABLE,
    }
    _print_receipt(receipt, as_json=as_json)
    captured = capsys.readouterr()
    assert captured.err.splitlines() == [_refusal_line(cast(dict[str, object], WRITABLE))]
    assert ("refusal" in captured.out) is as_json
    _print_receipt({**receipt, "refusal": None}, as_json=as_json)
    assert capsys.readouterr().err == ""
