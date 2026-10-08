# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — dependency lock audit workflow policy
"""Require lock discovery and audit failures to reach the main CI gate."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"


def test_lock_audits_discover_tracked_python_and_node_inputs() -> None:
    """Both audit selectors cover actual maintained locks, including integration pins."""
    text = WORKFLOW.read_text(encoding="utf-8")
    for pattern, required in (
        ("requirements*.txt", "integrations/github-app/requirements-dev.txt"),
        ("package-lock.json", "integrations/pi/package-lock.json"),
    ):
        selector = f":(glob)**/{pattern}"
        tracked = (
            subprocess.check_output(["git", "ls-files", "-z", "--", selector], cwd=ROOT)
            .decode()
            .split("\0")
        )
        assert required in tracked
        assert f"git ls-files -z -- '{selector}'" in text
    assert 'test -s "$RUNNER_TEMP/python-locks.list"' in text
    assert 'test -s "$RUNNER_TEMP/node-locks.list"' in text


def test_lock_audits_are_required_and_cannot_hide_failed_checks() -> None:
    """Both whole-lock audits propagate failures into the required aggregate job."""
    text = WORKFLOW.read_text(encoding="utf-8")
    audits = text.split("\n  python-lock-audit:\n", 1)[1].split(
        "\n  # Single required status check", 1
    )[0]
    gate = text.split("\n  ci:\n", 1)[1]
    needs = gate.split("needs: [", 1)[1].split("]", 1)[0].split(", ")
    assert "python-lock-audit" in needs
    assert "node-lock-audit" in needs
    assert audits.count('exit "$audit_status"') == 2
    assert audits.count("audit_status=1") == 2
    assert audits.count("set -euo pipefail") == 2
    for weakening in ("continue-on-error", "|| true", "--ignore-vuln", "--audit-level", "--omit"):
        assert weakening not in audits


def test_lock_audit_commands_inspect_pins_without_package_installation() -> None:
    """Pin advisory scans to the committed complete locks and every dependency scope."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "audit_flags=(--disable-pip --require-hashes)" in text
    assert "audit_flags=()" in text
    assert "if grep -q -- '--hash=' \"$lock\"; then" in text
    assert 'python -m pip_audit "${audit_flags[@]}"' in text
    assert '-r "$lock" --desc --progress-spinner=off' in text
    assert "npm audit --package-lock-only" in text
    assert 'done < "$RUNNER_TEMP/python-locks.list"' in text
    assert 'done < "$RUNNER_TEMP/node-locks.list"' in text
