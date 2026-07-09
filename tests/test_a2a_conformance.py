# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A conformance matrix

from __future__ import annotations

import json

from synapse_channel.a2a_conformance import (
    CONFORMANCE_ROWS,
    NORMATIVE_SOURCE_URL,
    SPEC_VERSION,
    SPECIFICATION_URL,
    STATUS_MEANINGS,
    conformance_report,
    conformance_rows,
    render_conformance_markdown,
)


def test_conformance_rows_have_stable_unique_keys_and_valid_statuses() -> None:
    keys = [(row.area, row.item) for row in CONFORMANCE_ROWS]

    assert len(keys) == len(set(keys))
    # Every row status is a known label; unused labels (e.g. external) may remain
    # in STATUS_MEANINGS for operator-facing docs until a row needs them again.
    assert {row.status for row in CONFORMANCE_ROWS} <= set(STATUS_MEANINGS)
    assert all(row.evidence for row in CONFORMANCE_ROWS)
    assert all(row.spec_reference.startswith("A2A 1.0.0") for row in CONFORMANCE_ROWS)


def test_conformance_report_is_json_serialisable_and_source_bound() -> None:
    report = conformance_report()

    assert report["spec_version"] == SPEC_VERSION
    assert report["specification_url"] == SPECIFICATION_URL
    assert report["normative_source_url"] == NORMATIVE_SOURCE_URL
    assert json.loads(json.dumps(report)) == report


def test_status_filter_returns_only_matching_rows() -> None:
    partial = conformance_rows(status="partial")

    assert partial
    assert {row.status for row in partial} == {"partial"}
    assert any(row.item == "Send Message" for row in partial)


def test_markdown_renderer_escapes_table_cells() -> None:
    rendered = render_conformance_markdown(status="partial")

    assert f"A2A conformance matrix (spec {SPEC_VERSION})" in rendered
    assert "POST\\|GET\\|DELETE /tasks/{id}/pushNotificationConfigs" in rendered
    assert "| operation | Send Message | partial |" in rendered


def test_matrix_keeps_external_validation_gates_visible() -> None:
    """Third-party public-network interop stays external; local HTTP client is partial."""
    external_items = {row.item for row in conformance_rows(status="external")}
    # Local independent HTTP client trace upgraded Independent interoperability to partial.
    assert "Independent interoperability" not in external_items
    partial_items = {row.item for row in conformance_rows(status="partial")}
    assert "Independent interoperability" in partial_items


def test_matrix_records_real_webhook_receiver_progress_as_partial() -> None:
    partial_items = {row.item for row in conformance_rows(status="partial")}

    assert "Real webhook receiver" in partial_items
    assert "Deployment threat model" in partial_items
