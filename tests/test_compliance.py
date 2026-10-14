# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — framework-labelled observation API tests
"""Exercise the composed report and refusals against actual retained journals."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.compliance import build_report, render_report, report_to_json
from synapse_channel.core.persistence import EventStore


def test_framework_report_uses_one_selected_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "report.db"
    store = EventStore(path)
    store.append("finding", {"task_id": "T", "private": "not-printed"}, ts=1)
    store.append("claim", {"task_id": "T"}, ts=2)
    store.close()
    report = build_report(path, framework="eu-ai-act", now=20_000_000, through_seq=1)
    data = report_to_json(report)
    assert report.retention.event_count == report.export.merkle.tree_size == 1
    assert report.retention.reaches_floor
    assert data["articles_addressed"] == ["12(1)-(2)", "19(1)", "26(6)"]
    assert "not-printed" not in str(data)
    assert "no compliance verdict" in render_report(report)


def test_invalid_framework_and_clock_refuse_before_creating_storage(tmp_path: Path) -> None:
    path = tmp_path / "absent.db"
    with pytest.raises(ValueError, match="unsupported"):
        build_report(path, framework="iso-secret-sentinel", now=0)
    with pytest.raises(ValueError, match="finite"):
        build_report(path, framework="eu-ai-act", now=True)
    assert not path.exists()
