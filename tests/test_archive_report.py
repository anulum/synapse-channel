# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for compact archive HTML reports

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.archive_report import (
    ArchiveReportOptions,
    render_archive_report,
    write_archive_report,
)
from synapse_channel.core.compaction import CompactionResult
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore


def _store(tmp_path: Path) -> EventStore:
    return EventStore(tmp_path / "events.db")


def test_render_archive_report_escapes_operator_visible_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "BUILD<script>",
            "title": "Ship <release>",
            "status": "open",
            "created_by": "planner",
            "created_at": 1.0,
            "updated_at": 1.0,
        },
        ts=1.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "BUILD<script>",
            "author": "agent<&>",
            "kind": "assessment",
            "text": "release receipt: evidence=pytest <passed>; confidence=high",
            "posted_at": 2.0,
        },
        ts=2.0,
    )
    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(checkpoints_removed=1, findings_removed=0, floor_seq=2),
        options=ArchiveReportOptions(
            source_path=store.path,
            generated_at=3.0,
            max_items=20,
        ),
    )
    store.close()

    assert "SYNAPSE archive report" in html
    assert "BUILD&lt;script&gt;" in html
    assert "Ship &lt;release&gt;" in html
    assert "agent&lt;&amp;&gt;" in html
    assert "pytest &lt;passed&gt;" in html
    assert "<script>" not in html


def test_render_archive_report_summarises_counts_receipts_and_recent_timeline(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append(
        EventKind.CLAIM,
        {
            "task_id": "T1",
            "owner": "ALPHA",
            "status": "claimed",
            "claimed_at": 10.0,
            "lease_expires_at": 1000.0,
            "paths": ["src"],
        },
        ts=10.0,
    )
    store.append(EventKind.RELEASE, {"task_id": "T1"}, ts=11.0)
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "T1",
            "author": "ALPHA",
            "kind": "assessment",
            "text": "release receipt: evidence=ruff check; changed_files=src/x.py",
            "posted_at": 12.0,
        },
        ts=12.0,
    )
    store.append(EventKind.CHAT, {"sender": "ALPHA", "payload": "not in timeline"}, ts=13.0)

    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(checkpoints_removed=0, findings_removed=2, floor_seq=4),
        options=ArchiveReportOptions(
            source_path=store.path,
            generated_at=20.0,
            max_items=10,
        ),
    )
    store.close()

    assert "<dt>Total events before compaction</dt><dd>4</dd>" in html
    assert "<td>claim</td><td>1</td>" in html
    assert "<td>chat</td><td>1</td>" in html
    assert "removed 0 checkpoint(s), 2 finding(s)" in html
    assert "release receipt: evidence=ruff check; changed_files=src/x.py" in html
    assert "claimed by ALPHA; status=claimed; paths=1" in html
    assert "released" in html
    assert "not in timeline" not in html


def test_render_archive_report_covers_coordination_event_variants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    claim_payload = {
        "task_id": "T1",
        "owner": "ALPHA",
        "status": "working",
        "claimed_at": 10.0,
        "lease_expires_at": 1000.0,
        "paths": ["src", "tests"],
        "git": {"branch": "feature/report", "base": "main"},
        "checkpoint": "cursor=9",
    }
    store.append(EventKind.TASK_UPDATE, claim_payload, ts=10.0)
    store.append(
        EventKind.CLAIM,
        {**claim_payload, "task_id": "T0", "git": {"branch": "", "base": ""}},
        ts=10.5,
    )
    store.append(EventKind.CHECKPOINT, claim_payload, ts=11.0)
    store.append(EventKind.HANDOFF, {**claim_payload, "owner": "BETA"}, ts=12.0)
    store.append(
        EventKind.RESOURCE,
        {"agent": "GPU", "kind": "compute", "name": "cuda", "capacity": 2},
        ts=13.0,
    )
    store.append(
        EventKind.LEDGER_TASK,
        {
            "task_id": "T2",
            "title": "",
            "status": "",
            "suggested_owner": "BETA",
            "created_by": "planner",
            "created_at": 14.0,
            "updated_at": 14.0,
        },
        ts=14.0,
    )
    store.append(
        EventKind.LEDGER_TASK,
        {
            "title": "missing id is ignored by board task table",
            "status": "open",
            "created_by": "planner",
            "created_at": 15.0,
            "updated_at": 15.0,
        },
        ts=15.0,
    )
    store.append(
        EventKind.LEDGER_PROGRESS,
        {
            "task_id": "T2",
            "author": "",
            "kind": "",
            "text": "ordinary progress, not a receipt",
            "posted_at": 16.0,
        },
        ts=16.0,
    )
    store.append("future_coordination", {"task_id": "T3"}, ts=17.0)

    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(checkpoints_removed=0, findings_removed=0, floor_seq=7),
        options=ArchiveReportOptions(
            source_path=store.path,
            generated_at=20.0,
            max_items=20,
        ),
    )
    store.close()

    assert "updated by ALPHA; status=working; paths=2; branch=feature/report; base=main" in html
    assert "checkpointed by ALPHA" in html
    assert "checkpoint=cursor=9" in html
    assert "handed off by BETA" in html
    assert "GPU offers compute/cuda; capacity=2" in html
    assert "(untitled); status=open; suggested_owner=BETA" in html
    assert "note by ?: ordinary progress, not a receipt" in html
    assert "future_coordination" in html
    assert "No entries." in html


def test_render_archive_report_notes_truncated_sections(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for seq in range(4):
        store.append(EventKind.RELEASE, {"task_id": f"TASK-{seq}"}, ts=float(seq))

    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(checkpoints_removed=0, findings_removed=0, floor_seq=4),
        options=ArchiveReportOptions(
            source_path=store.path,
            generated_at=10.0,
            max_items=2,
        ),
    )
    store.close()

    assert "showing latest 2 of 4 coordination event(s)" in html
    assert "TASK-0" not in html
    assert "TASK-2" in html
    assert "TASK-3" in html


def test_render_archive_report_preserves_only_safe_corrupt_row_evidence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seq = store.append(EventKind.CLAIM, {"task_id": "T1"})
    secret = "private-raw-event-content"
    store._conn.execute("UPDATE events SET payload = ? WHERE seq = ?", (secret, seq))
    store._conn.commit()
    marker = store.corrupt_rows()[0]

    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(
            checkpoints_removed=0,
            findings_removed=0,
            floor_seq=seq,
            corrupt_rows_removed=1,
        ),
        options=ArchiveReportOptions(source_path=store.path, generated_at=20.0),
    )
    store.close()

    assert "removed 0 checkpoint(s), 0 finding(s), 1 corrupt row(s)" in html
    assert "original_kind=claim" in html
    assert "reasons=invalid_json" in html
    assert marker.payload_sha256 in html
    assert secret not in html


def test_predelete_archive_labels_compaction_as_planned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(EventKind.CHAT, {"payload": "safe"})
    html = render_archive_report(
        store.read_all(),
        result=CompactionResult(checkpoints_removed=0, findings_removed=0, floor_seq=1),
        options=ArchiveReportOptions(
            source_path=store.path,
            generated_at=2.0,
            compaction_completed=False,
        ),
    )
    store.close()

    assert "<dt>Planned compaction</dt>" in html
    assert "<dt>Compaction result</dt>" not in html


def test_write_archive_report_replaces_file_and_restricts_permissions(tmp_path: Path) -> None:
    target = tmp_path / "reports" / "compact.html"
    write_archive_report(target, "<!doctype html><html><body>first</body></html>")
    write_archive_report(target, "<!doctype html><html><body>second</body></html>")

    assert target.read_text(encoding="utf-8").endswith("second</body></html>")
    from synapse_channel.core.secure_path import assert_owner_only_file_path

    assert_owner_only_file_path(target, purpose="archive report")


def test_write_archive_report_cleans_temporary_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "compact.html"

    def fail_replace(src: str | Path, dst: str | Path) -> None:
        raise OSError(f"refuse replace {src} -> {dst}")

    monkeypatch.setattr("synapse_channel.core.archive_report.os.replace", fail_replace)

    with pytest.raises(OSError, match="refuse replace"):
        write_archive_report(target, "<!doctype html><html></html>")

    assert not target.exists()
    assert list(tmp_path.glob("compact.html.*.tmp")) == []
