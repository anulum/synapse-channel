# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for release receipt normalisation

from __future__ import annotations

from pathlib import Path

from synapse_channel.core.receipts import (
    DEFAULT_RELEASE_EVIDENCE_FRESHNESS_SECONDS,
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
        "epistemic_reasons": [
            "positive evidence present",
            "fresh evidence present but unverified",
        ],
        "epistemic_status": "unverified",
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
        "approvals=reviewed-by=owner; confidence=medium; freshness_seconds=0.0; "
        "epistemic_status=unverified; "
        "epistemic_reasons=positive evidence present, fresh evidence present but unverified"
    )


def test_release_receipt_without_evidence_has_no_board_note_content() -> None:
    receipt = build_release_receipt(task_id="T1", owner="ALPHA")

    assert not release_receipt_has_evidence(receipt)
    assert receipt["epistemic_status"] == "unsupported"
    assert receipt["epistemic_reasons"] == [
        "no positive evidence, artifact, changed file, generated artifact, or approval"
    ]
    assert format_release_receipt_note(receipt) == (
        "release receipt: epistemic_status=unsupported; "
        "epistemic_reasons=no positive evidence, artifact, changed file, generated artifact, "
        "or approval"
    )


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
        == "release receipt: known_failures=mkdocs pending on unrelated branch; "
        "epistemic_status=degraded; "
        "epistemic_reasons=known failures declared, "
        "no positive evidence, artifact, changed file, generated artifact, or approval"
    )


def test_stale_positive_evidence_is_reported_as_stale() -> None:
    receipt = build_release_receipt(
        task_id="T1",
        owner="ALPHA",
        evidence=["pytest tests/test_release_receipts.py -q"],
        freshness_seconds=DEFAULT_RELEASE_EVIDENCE_FRESHNESS_SECONDS + 1.0,
    )

    assert receipt["epistemic_status"] == "stale"
    assert receipt["epistemic_reasons"] == [
        "positive evidence present",
        "evidence age exceeds 3600 seconds",
    ]


def test_positive_evidence_without_freshness_is_advisory() -> None:
    receipt = build_release_receipt(
        task_id="T1",
        owner="ALPHA",
        evidence=["pytest tests/test_release_receipts.py -q"],
    )

    assert receipt["epistemic_status"] == "needs_freshness"
    assert receipt["epistemic_reasons"] == [
        "positive evidence present",
        "freshness_seconds missing",
    ]


def test_release_receipt_docs_describe_advisory_epistemic_status() -> None:
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join(
        [
            (root / "README.md").read_text(encoding="utf-8"),
            (root / "docs" / "cli.md").read_text(encoding="utf-8"),
            (root / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
        ]
    )

    assert "epistemic_status" in combined
    assert "epistemic_reasons" in combined
    assert "advisory" in combined
    assert "does not certify" in combined


def test_forged_hub_release_evidence_is_graded_unverified_not_supported() -> None:
    """F4 regression: caller-supplied release evidence is never ``supported``.

    A release frame carries only caller-supplied strings; the hub runs no checks
    and verifies no signature, so fabricated evidence (a fake digest, a "ci: green"
    line, an invented approver) must grade ``unverified`` — never ``supported``.
    Grading presence as ``supported`` would let a forged release launder
    fabricated evidence into a trusted verdict.
    """
    forged = build_release_receipt(
        task_id="T-FORGE",
        owner="attacker",
        evidence=["ci: green"],
        artifacts=["dist/fabricated-artifact.whl digest=<forged>"],
        approvals=["approved-by=alice"],
        freshness_seconds=1.0,
    )

    assert forged["epistemic_status"] == "unverified"
    assert forged["epistemic_status"] != "supported"
    assert "fresh evidence present but unverified" in forged["epistemic_reasons"]
    assert "epistemic_status=unverified" in format_release_receipt_note(forged)


def test_receipt_routing_trust_follows_the_verified_grade() -> None:
    """F4 regression: routing trust follows the graded receipt, end to end.

    The full journey — ``build_release_receipt`` builds the dict,
    ``format_release_receipt_note`` renders the board note, and the routing
    filter ``_is_positive_receipt`` reads that note — must gate on the epistemic
    grade, not on evidence presence:

    * an ``unverified`` (forged) receipt is NOT positive routing evidence — a
      forged release must never launder fabricated evidence into routing trust;
    * a ``disputed`` receipt (a commitment signature was present but failed
      verification) is likewise excluded;
    * a genuinely ``supported`` receipt still routes as positive, so the guard
      excludes forgeries without over-blocking honest releases.

    The forged case runs the builder organically (caller-supplied evidence
    always grades ``unverified``). No source path emits ``supported`` or
    ``disputed`` from ``build_release_receipt`` itself — ``supported`` is applied
    only by the signing path and ``disputed`` only by a failed-verification path
    — so those two grades are asserted through the REAL formatter with the
    documented grade set on the receipt, exercising the same
    format-note -> filter wiring the routing layer relies on.
    """
    from synapse_channel.core.capability_observations import _is_positive_receipt

    forged = build_release_receipt(
        task_id="T-FORGE",
        owner="attacker",
        evidence=["ci: green"],
        approvals=["approved-by=alice"],
        freshness_seconds=1.0,
    )
    assert forged["epistemic_status"] == "unverified"
    forged_note = format_release_receipt_note(forged)
    assert _is_positive_receipt({"kind": "assessment", "text": forged_note}) is False

    disputed_note = format_release_receipt_note({**forged, "epistemic_status": "disputed"})
    assert _is_positive_receipt({"kind": "assessment", "text": disputed_note}) is False

    supported = build_release_receipt(
        task_id="T-OK",
        owner="release",
        evidence=["pytest -q"],
        freshness_seconds=1.0,
    )
    supported_note = format_release_receipt_note({**supported, "epistemic_status": "supported"})
    assert _is_positive_receipt({"kind": "assessment", "text": supported_note}) is True
