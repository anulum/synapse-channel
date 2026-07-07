# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL - unit tests for the claim handler's ttl_seconds coercion

"""A claim's ``ttl_seconds`` is untrusted client input.

A non-numeric, non-finite, or double-overflowing value must fall back to the hub's
default lease duration rather than raise out of the frame handler (a bare ``float``
raises ``OverflowError`` on a huge integer) or plant a non-finite lease expiry — an
``inf`` lease could never be taken over (a permanent task lock) and a ``nan`` one
would read as instantly expired.
"""

from __future__ import annotations

import math

import pytest

from synapse_channel.core.handlers.leasing import apply_claim
from synapse_channel.core.hub import SynapseHub


@pytest.mark.parametrize(
    "bad_ttl",
    [float("inf"), float("-inf"), float("nan"), 10**400, "not-a-number", [1, 2], {}, True],
)
def test_an_unusable_ttl_yields_a_finite_default_lease(bad_ttl: object) -> None:
    hub = SynapseHub()
    result = apply_claim(hub, "A", {"task_id": "T", "ttl_seconds": bad_ttl})
    assert result.ok
    assert result.claim is not None
    # The lease falls back to the hub default, so its expiry is finite and in the future.
    assert math.isfinite(result.claim.lease_expires_at)
    assert result.claim.lease_expires_at > result.claim.claimed_at


def test_a_finite_numeric_ttl_is_used() -> None:
    hub = SynapseHub()
    result = apply_claim(hub, "A", {"task_id": "T", "ttl_seconds": 120})
    assert result.ok
    assert result.claim is not None
    assert math.isfinite(result.claim.lease_expires_at)
    # 120s is above the floor, so the lease sits ~120s past the claim instant.
    assert result.claim.lease_expires_at == pytest.approx(result.claim.claimed_at + 120.0, abs=1.0)


def test_a_missing_ttl_uses_the_default_lease() -> None:
    hub = SynapseHub()
    result = apply_claim(hub, "A", {"task_id": "T"})
    assert result.ok
    assert result.claim is not None
    assert math.isfinite(result.claim.lease_expires_at)
