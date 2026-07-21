# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — recent Actions history workflow contract
"""Pin the history audit's bounded, fail-visible, read-only behavior."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "actions-history-audit.yml"


def test_history_audit_is_weekly_manual_and_read_only() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    event_block = text.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    permission_block = text.split("\npermissions:\n", 1)[1].split("\nconcurrency:\n", 1)[0]

    assert 'cron: "10 6 * * 1"' in event_block
    assert "\n  workflow_dispatch:" in event_block
    assert "\n  push:" not in event_block
    assert "\n  pull_request:" not in event_block
    assert permission_block.strip().splitlines() == ["actions: read", "  contents: read"]
    assert "cancel-in-progress: false" in text
    assert "timeout-minutes: 10" in text


def test_history_query_and_classifier_are_bounded_and_fail_visible() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--branch main" in text
    assert "--limit 500" in text
    assert (
        "--json databaseId,conclusion,status,workflowName,headSha,headBranch,createdAt,event"
        in text
    )
    assert "--input actions-history.json" in text
    assert "--exclude-workflow actions-history-audit" in text
    assert "continue-on-error" not in text
    assert "if: always()" in text
    assert "actions-history*.json" in text
    assert "if-no-files-found: error" in text
    assert "retention-days: 30" in text


def test_history_workflow_uses_only_expected_sha_pinned_actions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    actions = re.findall(r"uses: ([^\s@]+)@([0-9a-f]{40})(?:\s|$)", text)

    assert actions == [
        ("actions/checkout", "3d3c42e5aac5ba805825da76410c181273ba90b1"),
        ("actions/setup-python", "ece7cb06caefa5fff74198d8649806c4678c61a1"),
        ("actions/upload-artifact", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
    ]
