# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — documented paid-provider profile lookup
"""Return each documented provider profile and refuse an unknown name."""

from __future__ import annotations

import pytest

from synapse_channel.client.provider_profiles import PROFILES, provider_profile


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_documented_profile_is_returned_by_name(name: str) -> None:
    """The lookup returns the exact registered profile, keyed by its own name."""
    profile = provider_profile(name)
    assert profile is PROFILES[name]
    assert profile.name == name
    # Paid providers are reached over TLS; only the local Ollama runtime is plain loopback.
    assert profile.base_url.startswith(("https://", "http://localhost:"))


@pytest.mark.parametrize("name", ["", "Anthropic", "openai-compatible", "unknown"])
def test_unknown_provider_is_refused_explicitly(name: str) -> None:
    """A name outside the documented table is a ValueError, not a fallback profile."""
    with pytest.raises(ValueError, match=f"^unknown model provider: {name}$") as caught:
        provider_profile(name)
    assert isinstance(caught.value.__cause__, KeyError)
