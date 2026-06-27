# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for release receipt normalisation

from __future__ import annotations

from synapse_channel.core.receipts import (
    MAX_RELEASE_RECEIPT_ITEM_CHARS,
    MAX_RELEASE_RECEIPT_ITEMS,
    build_release_receipt,
    clean_receipt_items,
    format_release_receipt_note,
    release_receipt_has_evidence,
)


def test_clean_receipt_items_bounds_count_and_text() -> None:
    raw = ["", "  first  ", "x" * (MAX_RELEASE_RECEIPT_ITEM_CHARS + 5)]
    raw.extend(str(index) for index in range(MAX_RELEASE_RECEIPT_ITEMS + 10))

    cleaned = clean_receipt_items(raw)

    assert cleaned[0] == "first"
    assert cleaned[1] == "x" * MAX_RELEASE_RECEIPT_ITEM_CHARS
    assert len(cleaned) == MAX_RELEASE_RECEIPT_ITEMS


def test_build_release_receipt_normalises_optional_fields() -> None:
    receipt = build_release_receipt(
        task_id=" T1 ",
        owner=" ALPHA ",
        evidence="pytest -q",
        artifacts=[" coverage.xml "],
        known_failures=None,
        changed_files=(" src/a.py ",),
        generated_artifacts=[" docs/_generated/capability_manifest.json "],
        approvals=[" reviewed-by=owner "],
        confidence=" medium ",
        freshness_seconds="-4",
    )

    assert receipt == {
        "approvals": ["reviewed-by=owner"],
        "artifacts": ["coverage.xml"],
        "changed_files": ["src/a.py"],
        "confidence": "medium",
        "evidence": ["pytest -q"],
        "freshness_seconds": 0.0,
        "generated_artifacts": ["docs/_generated/capability_manifest.json"],
        "known_failures": [],
        "owner": "ALPHA",
        "released": True,
        "task_id": "T1",
    }
    assert release_receipt_has_evidence(receipt)
    assert format_release_receipt_note(receipt) == (
        "release receipt: evidence=pytest -q; artifacts=coverage.xml; "
        "changed_files=src/a.py; "
        "generated_artifacts=docs/_generated/capability_manifest.json; "
        "approvals=reviewed-by=owner; confidence=medium; freshness_seconds=0.0"
    )


def test_release_receipt_without_evidence_has_no_board_note_content() -> None:
    receipt = build_release_receipt(task_id="T1", owner="ALPHA")

    assert not release_receipt_has_evidence(receipt)
    assert format_release_receipt_note(receipt) == "release receipt: "


def test_invalid_freshness_is_ignored() -> None:
    receipt = build_release_receipt(
        task_id="T1",
        owner="ALPHA",
        freshness_seconds=object(),
    )

    assert "freshness_seconds" not in receipt


def test_known_failure_only_receipt_formats_note() -> None:
    receipt = build_release_receipt(
        task_id="T1",
        owner="ALPHA",
        known_failures=["mkdocs pending on unrelated branch"],
    )

    assert release_receipt_has_evidence(receipt)
    assert (
        format_release_receipt_note(receipt)
        == "release receipt: known_failures=mkdocs pending on unrelated branch"
    )
