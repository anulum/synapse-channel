# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — compute-pool restriction and idle-cost shapes
"""Refuse compute-pool restriction lists and idle costs of the wrong shape."""

from __future__ import annotations

import pytest

from synapse_channel.core.entitlements import EntitlementError, validate_event


def _compute_pool(**overrides: object) -> dict[str, object]:
    return {
        "event_id": "p-compute",
        "kind": "pool",
        "recorded_at": "2026-09-19T10:00:00Z",
        "source": "operator:account-owner",
        "confidence": "operator",
        "pool_id": "compute",
        "account_id": "account-1",
        "unit": "gpu_seconds",
        "resource_kind": "gpu_time",
        "capabilities": ["run"],
        "data_classes": ["internal"],
        "eligible_projects": ["SYNAPSE-CHANNEL"],
        **overrides,
    }


@pytest.mark.parametrize(
    "capabilities", ["run", [], [f"cap-{index}" for index in range(33)], ("run",)]
)
def test_restriction_list_must_be_a_bounded_non_empty_list(capabilities: object) -> None:
    """A string, an empty list, 33 entries or a tuple are not a restriction list."""
    with pytest.raises(
        EntitlementError,
        match="^capabilities must be a non-empty list of at most 32 identifiers$",
    ):
        validate_event(_compute_pool(capabilities=capabilities))


@pytest.mark.parametrize(
    "idle_cost",
    [1, "0.25 CHF", ["0.25", "CHF"], {"amount_per_hour": "0.25"}, {"currency": "CHF", "x": "1"}],
)
def test_idle_cost_must_be_exactly_amount_and_currency(idle_cost: object) -> None:
    """Anything but a mapping with exactly amount_per_hour and currency is refused."""
    with pytest.raises(EntitlementError, match="^idle_cost requires amount_per_hour and currency$"):
        validate_event(_compute_pool(idle_cost=idle_cost))
    assert validate_event(_compute_pool())["capabilities"] == ["run"]
