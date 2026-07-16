# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for golden-demo evidence artifacts

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from synapse_channel.core.release_verification import VerifiedReleaseReceipt
from synapse_channel.dashboard import DashboardSnapshot
from synapse_channel.demo_artifacts import write_demo_artifacts
from synapse_channel.demo_scenario import DemoStep, GoldenDemoResult
from synapse_channel.file_claim_guard import GuardVerdict


def _supported_receipt() -> VerifiedReleaseReceipt:
    """Return a complete supported receipt fixture with observed metadata."""
    return cast(
        VerifiedReleaseReceipt,
        {
            "task_id": "DEMO-CLAUDE",
            "owner": "CODEX",
            "released": True,
            "evidence": ["command: unittest exit=0"],
            "artifacts": ["src/shared.py sha256=abc size=1"],
            "known_failures": [],
            "changed_files": ["src/shared.py"],
            "generated_artifacts": [],
            "approvals": [],
            "epistemic_status": "supported",
            "epistemic_reasons": ["positive evidence present", "fresh evidence present"],
            "confidence": "observed",
            "freshness_seconds": 0.0,
            "verification": {
                "commands": [],
                "artifacts": [],
                "changed_files": ["src/shared.py"],
                "git_head": "a" * 40,
                "git_tree": "b" * 40,
                "timestamp": 1.0,
            },
        },
    )


def test_writer_emits_stable_json_and_visual_story(tmp_path: Path) -> None:
    """The evidence document and dashboard retain the safety milestones."""
    step = DemoStep("04", "CONFLICT REFUSED", "overlapping claim denied")
    snapshot = DashboardSnapshot(
        online_agents=["CLAUDE", "CODEX"],
        state={"active_claims": []},
        board={
            "tasks": [],
            "ready": [],
            "progress": [
                {
                    "author": "CODEX",
                    "kind": "note",
                    "task_id": "DEMO-CLAUDE",
                    "text": step.progress_text(),
                }
            ],
        },
        manifest=[],
    )
    result = GoldenDemoResult(
        steps=(step,),
        guard_before_handoff=GuardVerdict(False, "denied"),
        guard_after_handoff=GuardVerdict(True),
        receipt=_supported_receipt(),
        dashboard=snapshot,
        narration=("conflict refused",),
    )

    artifacts = write_demo_artifacts(result, tmp_path / "artifacts")

    evidence = json.loads(artifacts.evidence_json.read_text(encoding="utf-8"))
    dashboard = artifacts.dashboard_html.read_text(encoding="utf-8")
    assert evidence["completed"] is True
    assert evidence["steps"][0]["title"] == "CONFLICT REFUSED"
    assert "CONFLICT REFUSED" in dashboard
