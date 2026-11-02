# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wake-capability token normalisation and labels
"""Normalise declared wake capabilities and label every outcome for operators."""

from __future__ import annotations

import pytest

from synapse_channel.core.wake_capability import (
    WAKE_DIRECT,
    WAKE_PANE_BRIDGE,
    WAKE_PASSIVE,
    WAKE_UNKNOWN,
    normalize_wake_capability,
    wake_capability_label,
)


@pytest.mark.parametrize("value", [None, 1, True, ["direct"], b"direct"])
def test_a_non_text_declaration_falls_back(value: object) -> None:
    """Only text can name a capability; anything else takes the fallback."""
    assert normalize_wake_capability(value) == WAKE_UNKNOWN
    assert normalize_wake_capability(value, default=WAKE_PASSIVE) == WAKE_PASSIVE
    assert normalize_wake_capability(value, default="bogus") == WAKE_UNKNOWN


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (" Direct ", "direct agent"),
        (WAKE_PASSIVE, "passive receiver"),
        ("Pane-Bridge", "pane bridge"),
        (WAKE_UNKNOWN, "unknown wake capability"),
        ("telepathy", "unknown wake capability"),
        (None, "unknown wake capability"),
    ],
)
def test_every_capability_has_an_operator_label(value: object, label: str) -> None:
    """Known tokens are folded and labelled; anything else is reported as unknown."""
    assert wake_capability_label(value) == label
    assert normalize_wake_capability("PANE-bridge") == WAKE_PANE_BRIDGE
    assert normalize_wake_capability(" direct ") == WAKE_DIRECT
