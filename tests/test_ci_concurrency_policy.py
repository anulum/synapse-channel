# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — main-head CI completion policy
"""Pin the no-cancel boundary for every main-branch commit."""

from __future__ import annotations

from pathlib import Path

CI_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_each_push_head_has_a_unique_ci_concurrency_group() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert (
        "group: ${{ github.workflow }}-${{ github.event_name == 'push' && "
        "github.sha || github.ref }}" in workflow
    )
    assert "cancel-in-progress: ${{ github.event_name != 'push' }}" in workflow


def test_ci_cannot_restore_ref_wide_push_cancellation() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "group: ${{ github.workflow }}-${{ github.ref }}" not in workflow
    assert "cancel-in-progress: true" not in workflow
