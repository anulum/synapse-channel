# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated real-boundary tests for the golden demo scenario

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from synapse_channel.core.release_verification import VerifiedReleaseReceipt
from synapse_channel.demo import _free_port, run_coordination_demo
from synapse_channel.demo_scenario import (
    _require_allowed,
    _require_clean_receipt,
    _require_completed,
    _require_denied,
    _require_separate_claims,
)
from synapse_channel.file_claim_guard import GuardVerdict


async def test_scenario_enforces_then_transfers_mutation_authority(tmp_path: Path) -> None:
    """The real hub denies Codex before handoff and allows it after handoff."""
    result = await run_coordination_demo(
        _free_port(),
        workspace=tmp_path / "workspace",
    )

    assert result.completed is True
    assert result.guard_before_handoff.allowed is False
    assert "ownership" in result.guard_before_handoff.reason
    assert result.guard_after_handoff.allowed is True
    assert result.receipt["epistemic_status"] == "supported"
    assert result.receipt["known_failures"] == []
    verification = result.receipt["verification"]
    assert [item["exit_code"] for item in verification["commands"]] == [0, 0]
    assert len(verification["artifacts"]) == 2
    progress = result.dashboard.board["progress"]
    rendered = "\n".join(str(note["text"]) for note in progress)
    for marker in (
        "SEPARATE CLAIMS",
        "CONFLICT REFUSED",
        "MUTATION DENIED",
        "HANDOFF",
        "VERIFIED RECEIPT",
    ):
        assert marker in rendered

    _require_completed(result)
    failed_step = replace(result.steps[0], status="failed")
    assert replace(result, steps=(failed_step, *result.steps[1:])).completed is False
    assert (
        replace(
            result,
            guard_before_handoff=GuardVerdict(True),
        ).completed
        is False
    )
    assert (
        replace(
            result,
            guard_after_handoff=GuardVerdict(False, "not transferred"),
        ).completed
        is False
    )
    unsupported = cast(VerifiedReleaseReceipt, dict(result.receipt))
    unsupported["epistemic_status"] = "unsupported"
    assert replace(result, receipt=unsupported).completed is False
    failed_receipt = cast(VerifiedReleaseReceipt, dict(result.receipt))
    failed_receipt["known_failures"] = ["verification failed"]
    assert replace(result, receipt=failed_receipt).completed is False
    with pytest.raises(RuntimeError, match="without satisfying every invariant"):
        _require_completed(replace(result, receipt=failed_receipt))


def test_scenario_invariant_helpers_cover_success_and_failure() -> None:
    """Each scenario gate reports its own precise failure boundary."""
    allowed = GuardVerdict(True)
    denied = GuardVerdict(False, "claim denied")
    _require_separate_claims(allowed, allowed)
    with pytest.raises(RuntimeError, match="separate claim ownership"):
        _require_separate_claims(denied, allowed)
    with pytest.raises(RuntimeError, match="separate claim ownership"):
        _require_separate_claims(allowed, denied)
    _require_denied(denied)
    with pytest.raises(RuntimeError, match="unsafe Codex mutation"):
        _require_denied(allowed)
    _require_allowed(allowed)
    with pytest.raises(RuntimeError, match="claim denied"):
        _require_allowed(denied)

    clean = cast(
        VerifiedReleaseReceipt,
        {"known_failures": []},
    )
    failed = cast(
        VerifiedReleaseReceipt,
        {"known_failures": ["unittest exit=1"]},
    )
    _require_clean_receipt(clean)
    with pytest.raises(RuntimeError, match="unittest exit=1"):
        _require_clean_receipt(failed)
