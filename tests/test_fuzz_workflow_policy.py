# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — policy contract for scheduled fuzzing
"""Keep the scheduled fuzz lane bounded, pinned, and independent of PR CI."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "fuzz.yml"


def test_fuzz_workflow_is_scheduled_and_manual_only() -> None:
    """The heavier property budget stays off the push and pull-request path."""
    text = WORKFLOW.read_text(encoding="utf-8")
    event_block = text.split("\non:\n", 1)[1].split("\npermissions:\n", 1)[0]

    assert 'cron: "17 4 * * 1"' in event_block
    assert "\n  workflow_dispatch:" in event_block
    assert "\n  push:" not in event_block
    assert "\n  pull_request:" not in event_block
    assert "contents: read" in text
    assert "timeout-minutes: 20" in text
    assert "continue-on-error" not in text


def test_fuzz_workflow_pins_tools_budget_and_production_targets() -> None:
    """The workflow uses pinned actions and the exact wire/persistence targets."""
    text = WORKFLOW.read_text(encoding="utf-8")
    action_revisions = re.findall(r"uses: [^\s@]+@([0-9a-f]{40})(?:\s|$)", text)

    assert len(action_revisions) == 2
    assert "--require-hashes -r .github/requirements/requirements-dev.txt" in text
    assert 'SYNAPSE_FUZZ_EXAMPLES: "1000"' in text
    assert "python tools/fuzz_protocol_decode.py --smoke" in text
    assert "tests/test_fuzz_wire_inputs.py" in text
    assert "tests/test_fuzz_persistence_inputs.py" in text
