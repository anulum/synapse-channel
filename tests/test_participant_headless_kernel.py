# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the shared headless execution kernel
"""The headless kernel states the behaviour it owns for every provider."""

from synapse_channel.participants import headless_kernel


def test_kernel_module_states_the_behaviour_it_owns() -> None:
    """The shipped kernel states that it owns the behaviour that must be identical."""
    text = headless_kernel.__doc__ or ""
    assert "This kernel owns only the behaviour that must be identical" in text
    assert "behavior" not in text
