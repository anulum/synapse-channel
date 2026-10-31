# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — crash-and-recovery demonstration
"""The recovery demo runs on a real hub and fails loudly when a safety property breaks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import __version__
from synapse_channel.demo_recovery import (
    RECOVERY_STEP_TITLES,
    RecoveryResult,
    RecoveryStep,
    _require,
    run_installed_recovery_demo,
)


@pytest.mark.real_hub
def test_the_installed_recovery_demo_observes_every_safety_property(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real hub refuses, holds, expires, refuses the old owner and verifies as narrated."""
    evidence = run_installed_recovery_demo(tmp_path / "out")
    record = json.loads(evidence.read_text(encoding="utf-8"))
    assert evidence == tmp_path / "out" / "recovery-demo.json"
    assert record["completed"] is True
    assert record["version"] == __version__
    assert [step["key"] for step in record["steps"]] == list(RECOVERY_STEP_TITLES)
    assert [step["title"] for step in record["steps"]] == list(RECOVERY_STEP_TITLES.values())
    details = {step["key"]: step["detail"] for step in record["steps"]}
    assert "file scope conflicts" in details["02"]
    assert "is owned by CLAUDE, not CODEX" in details["03"]
    assert "already claimed by CLAUDE" in details["05"]
    assert "is owned by CODEX, not CLAUDE" in details["07"]
    assert record["first_epoch"] == 1
    assert record["recovered_epoch"] == 2
    assert record["partial_result"] == "not_verified"
    assert record["final_result"] == "verified"
    assert record["receipt"]["epistemic_status"] == "unverified"
    printed = capsys.readouterr().out
    assert printed.count("•") == len(RECOVERY_STEP_TITLES)


def test_an_incomplete_result_is_never_completed() -> None:
    """Missing steps, equal epochs or an unverified result keep ``completed`` false."""
    empty = RecoveryResult()
    assert empty.completed is False
    assert empty.to_dict()["completed"] is False
    full = RecoveryResult(
        steps=[RecoveryStep(key, title, "observed") for key, title in RECOVERY_STEP_TITLES.items()],
        first_epoch=1,
        recovered_epoch=2,
        partial_result="not_verified",
        final_result="verified",
        receipt={"epistemic_status": "unverified"},
    )
    assert full.completed is True
    changes: tuple[dict[str, object], ...] = (
        {"recovered_epoch": 1},
        {"partial_result": "verified"},
        {"final_result": "not_verified"},
        {"receipt": {}},
        {"steps": full.steps[:-1]},
    )
    for change in changes:
        fields: dict[str, Any] = {**full.__dict__, **change}
        broken = RecoveryResult(**fields)
        assert broken.completed is False


def test_a_broken_safety_property_stops_the_demo() -> None:
    """``_require`` raises with the stated failure and passes silently otherwise."""
    _require(True, "unused")
    with pytest.raises(RuntimeError, match="a live lease was taken over"):
        _require(False, "a live lease was taken over")
