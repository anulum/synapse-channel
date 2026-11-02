# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — HTTP authority parsing refuses unparsable hosts and ports
"""A URL whose port or bracketed host cannot be parsed never yields an origin."""

from __future__ import annotations

import pytest

from synapse_channel.core.http_authority import endpoint_authorities, normalise_url_origin

UNPARSABLE = ["http://host:abc/x", "http://[::1/x", "https://host:99999/", "http://host:-1/"]


@pytest.mark.parametrize("url", UNPARSABLE)
def test_unparsable_origin_is_refused(url: str) -> None:
    """A non-numeric or out-of-range port, or an unclosed IPv6 bracket, has no origin."""
    with pytest.raises(
        ValueError, match="^URL must identify one exact HTTP\\(S\\) origin$"
    ) as caught:
        normalise_url_origin(url)
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("url", UNPARSABLE)
def test_unparsable_endpoint_has_no_authority(url: str) -> None:
    """The endpoint form reports its own bounded message for the same inputs."""
    with pytest.raises(
        ValueError, match="^endpoint URL must identify one HTTP\\(S\\) authority$"
    ) as caught:
        endpoint_authorities(url)
    assert isinstance(caught.value.__cause__, ValueError)
