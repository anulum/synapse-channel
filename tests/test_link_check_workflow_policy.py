# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — link-check workflow contract
"""Keep public link checking pinned, bounded, and private-network safe."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "link-check.yml"


def test_link_check_covers_doc_changes_and_weekly_drift() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    event_block = text.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]
    permission_block = text.split("\npermissions:\n", 1)[1].split("\nconcurrency:\n", 1)[0]

    assert event_block.startswith("  push:\n")
    assert "\n  pull_request:" in event_block
    assert "\n  schedule:" in event_block
    assert "\n  workflow_dispatch:" in event_block
    assert 'cron: "30 5 * * 1"' in event_block
    assert '      - "*.md"' in event_block
    assert '      - "**/*.md"' in event_block
    assert permission_block.strip() == "contents: read"
    assert "issues: write" not in text
    assert "create-issue" not in text


def test_link_check_is_fail_visible_without_accepting_server_errors() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "--exclude-all-private" in text
    assert "--min-tls TLSv1_2" in text
    assert "--max-concurrency 32" in text
    assert "--max-redirects 10" in text
    assert "--max-retries 3" in text
    assert "--retry-wait-time 5" in text
    assert "--timeout 20" in text
    assert "--accept 200,204,206,301,302,303,307,308,403,429" in text
    assert not re.search(r"--accept [^\n]*(?:500|501|502|503|504|505)", text)
    assert "fail: true" in text
    assert "failIfEmpty: true" in text
    assert "jobSummary: true" in text
    assert "if: always()" in text
    assert "lychee/report.md" in text
    assert "if-no-files-found: error" in text


def test_link_workflow_uses_only_expected_sha_pinned_actions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    actions = re.findall(r"uses: ([^\s@]+)@([0-9a-f]{40})(?:\s|$)", text)

    assert actions == [
        ("actions/checkout", "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"),
        ("lycheeverse/lychee-action", "e7477775783ea5526144ba13e8db5eec57747ce8"),
        ("actions/upload-artifact", "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"),
    ]
