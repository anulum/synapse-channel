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

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "link-check.yml"

_CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
_LYCHEE_SHA = "e7477775783ea5526144ba13e8db5eec57747ce8"
_UPLOAD_SHA = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"
_ACCEPT = "200,204,206,301,302,303,307,308,403,429"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_link_check_covers_doc_changes_and_weekly_drift() -> None:
    text = _workflow_text()
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
    text = _workflow_text()

    assert "--exclude-all-private" in text
    assert "--min-tls TLSv1_2" in text
    assert "--max-concurrency 32" in text
    assert "--max-redirects 10" in text
    assert "--max-retries 3" in text
    assert "--retry-wait-time 5" in text
    assert "--timeout 20" in text
    assert f"--accept {_ACCEPT}" in text
    assert not re.search(r"--accept [^\n]*(?:500|501|502|503|504|505)", text)
    assert "fail: true" in text
    assert "failIfEmpty: true" in text
    assert "jobSummary: true" in text
    assert "if-no-files-found: error" in text
    assert "timeout-minutes: 30" in text


def test_link_check_uses_exact_two_attempt_gate_with_conditional_retry() -> None:
    """Exactly one optional full retry after first failure; never more."""
    text = _workflow_text()

    attempt_1 = text.count("id: lychee_attempt_1")
    attempt_2 = text.count("id: lychee_attempt_2")
    assert attempt_1 == 1
    assert attempt_2 == 1
    assert text.count("lycheeverse/lychee-action@") == 2

    # First attempt continues only so the bounded retry can run.
    first_block = text.split("id: lychee_attempt_1", 1)[1].split("id: lychee_attempt_2", 1)[0]
    assert "continue-on-error: true" in first_block

    # Second attempt is conditional on first failure and must not continue-on-error.
    second_header = text.split("name: Check tracked Markdown links (attempt 2)", 1)[1]
    second_step = second_header.split("\n      - name:", 1)[0]
    assert "if: steps.lychee_attempt_1.outcome == 'failure'" in second_step
    assert "continue-on-error" not in second_step
    assert "fail: true" in second_step

    # No third lychee attempt.
    assert "lychee_attempt_3" not in text
    assert "attempt 3" not in text.lower()


def test_link_check_persistent_failure_is_visible_and_fails_job() -> None:
    text = _workflow_text()

    assert "Fail when link check did not succeed within two attempts" in text
    assert "steps.lychee_attempt_1.outcome == 'failure'" in text
    assert "steps.lychee_attempt_2.outcome != 'success'" in text
    assert "exit 1" in text
    assert "::error::" in text or "Link check failed after the bounded two-attempt gate" in text


def test_link_check_uploads_distinct_attempt_reports(tmp_path: Path) -> None:
    """Actual report paths stay outside the next attempt's Markdown input."""
    text = _workflow_text()

    workspace = tmp_path / "checkout"
    workspace.mkdir()
    document = workspace / "README.md"
    document.write_text("[valid](https://example.com/)\n", encoding="utf-8")
    runner_temp = tmp_path / "runner-temp"
    workflow = yaml.safe_load(text)
    steps = workflow["jobs"]["public-docs"]["steps"]
    outputs = [step["with"]["output"] for step in steps if "lycheeverse/" in step.get("uses", "")]
    uploads = [
        step["with"]["path"] for step in steps if "actions/upload-artifact@" in step.get("uses", "")
    ]
    assert len(outputs) == 2 and len(set(outputs)) == 2 and outputs == uploads
    for output in outputs:
        report = Path(output.replace("${{ runner.temp }}", str(runner_temp)))
        assert report.is_relative_to(runner_temp) and not report.is_relative_to(workspace)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("[previous failure](https://unavailable.invalid/)\n", encoding="utf-8")
    assert list(workspace.glob("**/*.md")) == [document]
    assert "name: link-check-report-attempt-1" in text
    assert "name: link-check-report-attempt-2" in text
    # No ambiguous shared report identity.
    assert "output: lychee/report.md" not in text
    assert "name: link-check-report\n" not in text
    assert text.count("if: always()") >= 1
    assert "if: always() && steps.lychee_attempt_1.outcome == 'failure'" in text


def test_link_check_keeps_narrow_badge_image_excludes_only() -> None:
    text = _workflow_text()

    assert "--exclude '^https?://img\\.shields\\.io/'" in text
    assert "--exclude '^https?://api\\.securityscorecards\\.dev/'" in text
    # No broader external-link suppression (no wildcard https exclude).
    assert "--exclude '^https?://'" not in text
    assert "--offline" not in text


def test_link_workflow_uses_only_expected_sha_pinned_actions() -> None:
    text = _workflow_text()
    actions = re.findall(r"uses: ([^\s@]+)@([0-9a-f]{40})(?:\s|$)", text)

    assert actions == [
        ("actions/checkout", _CHECKOUT_SHA),
        ("lycheeverse/lychee-action", _LYCHEE_SHA),
        ("actions/upload-artifact", _UPLOAD_SHA),
        ("lycheeverse/lychee-action", _LYCHEE_SHA),
        ("actions/upload-artifact", _UPLOAD_SHA),
    ]
    # Exactly five uses lines: checkout + 2×lychee + 2×upload.
    assert len(actions) == 5
