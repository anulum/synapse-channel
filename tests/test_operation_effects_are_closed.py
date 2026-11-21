# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — operation effects are a closed set of five software effects
"""No operation can be declared with a physical or equipment effect.

An operation record declares one of five software effects, each with a verifier
that reads its own system of record. Any other effect is refused, so nothing that
acts on physical equipment can be declared, verified or sealed until an equipment
effect with its own verifier is designed. The security policy and the wire
protocol state this, and name the five; this module keeps the code and those
pages in step.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.operation_records import (
    VERIFIER_FOR_EFFECT,
    OperationRecordError,
    decode_operation_record,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 64

# Each effect and the verifier whose records may speak for it. A new effect is a
# design change: it updates this table, docs/protocol.md and SECURITY.md together.
SOFTWARE_EFFECTS = {
    "file": "file_change",
    "git_publication": "git_publication",
    "deployment": "deployment",
    "migration": "database_migration",
    "component_state": "component_read",
}


def _declare(effect: str) -> dict[str, Any]:
    return {
        "record": "declare",
        "task_id": "T",
        "operation_id": "op-1",
        "epoch": 3,
        "effect": effect,
        "effect_target": {"path": "out/result.txt", "sha256": SHA},
    }


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    following = text.find("\n### ", start + len(heading))
    return text[start:] if following < 0 else text[start:following]


def test_the_effects_are_exactly_the_five_software_effects() -> None:
    """The closed set is the documented five, each bound to its verifier."""
    assert dict(VERIFIER_FOR_EFFECT) == SOFTWARE_EFFECTS


@pytest.mark.parametrize("effect", ["equipment", "physical", "actuator", "instrument", "robot"])
def test_a_physical_or_equipment_effect_cannot_be_declared(effect: str) -> None:
    """A declaration naming an effect outside the five is refused at decoding."""
    with pytest.raises(OperationRecordError, match="effect must be one of"):
        decode_operation_record(_declare(effect))


def test_the_security_policy_and_the_protocol_name_the_same_five() -> None:
    """Both pages name exactly the five effects the code admits."""
    policy = _section(
        (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8"),
        "### No physical-effect operations",
    )
    protocol = (REPO_ROOT / "docs" / "protocol.md").read_text(encoding="utf-8")
    listed = re.search(r"an `effect` \(([^)]*)\)", protocol)
    assert listed is not None
    for text in (policy.split("\n\n", 2)[1], listed.group(1)):
        assert set(re.findall(r"`([a-z_]+)`", text)) == set(SOFTWARE_EFFECTS)
