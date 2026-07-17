# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for golden-demo runtime boundaries

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.release_verification import collect_git_state
from synapse_channel.demo_runtime import (
    _run_git,
    _seed_workspace,
    _validated_hub_receipt,
)


def test_seed_workspace_is_a_clean_main_branch_repository(tmp_path: Path) -> None:
    """The runtime creates the real Git boundary the mutation guard evaluates."""
    _seed_workspace(tmp_path)

    state = collect_git_state(tmp_path)
    assert _run_git(tmp_path, "branch", "--show-current") == "main"
    assert state.head
    assert state.tree
    assert state.changed_files == []
    assert (tmp_path / "src/shared.py").is_file()
    assert (tmp_path / "tests/test_shared.py").is_file()


def test_git_failure_and_hub_receipt_validation_fail_closed(tmp_path: Path) -> None:
    """Runtime boundary failures remain explicit rather than becoming success."""
    with pytest.raises(RuntimeError, match="not a git repository"):
        _run_git(tmp_path, "status", "--short")
    with pytest.raises(RuntimeError, match="did not return a release receipt"):
        _validated_hub_receipt(None)
    with pytest.raises(RuntimeError, match="unverified evidence"):
        _validated_hub_receipt({"epistemic_status": "unsupported"})
    # F4: the demo runtime accepts ONLY the honest ``unverified`` grade for a
    # caller-supplied hub receipt. A hub that returns ``supported`` for evidence
    # it never verified is fail-closed as an over-claim, not trusted.
    with pytest.raises(RuntimeError, match="unverified evidence"):
        _validated_hub_receipt({"epistemic_status": "supported"})
    unverified = {"epistemic_status": "unverified", "task_id": "T1"}
    assert _validated_hub_receipt(unverified) is unverified
