# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — OpenCode endpoints with an invalid port are refused at configuration time
"""An out-of-range or non-numeric port fails when the endpoint is configured, not on first use."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.participants.opencode_api import OpenCodeApiParticipant
from synapse_channel.participants.opencode_auth import OpenCodeAuthError, validate_endpoint

_REASON = "^OpenCode endpoint port must be an integer from 0 to 65535\\.$"


@pytest.mark.parametrize(
    "endpoint",
    ["http://127.0.0.1:65536", "http://127.0.0.1:99999", "https://example.test:http"],
)
def test_an_invalid_port_is_refused_by_the_endpoint_policy(endpoint: str) -> None:
    """The policy names the port before any participant or request exists."""
    with pytest.raises(OpenCodeAuthError, match=_REASON):
        validate_endpoint(endpoint, allow_insecure_http=True)


def test_the_highest_port_is_still_accepted() -> None:
    """65535 is a valid port, so the boundary itself passes unchanged."""
    assert validate_endpoint("http://127.0.0.1:65535/") == "http://127.0.0.1:65535"


def test_the_api_participant_refuses_an_invalid_port_when_configured(tmp_path: Path) -> None:
    """Configuration fails fast instead of reporting the endpoint unavailable later."""
    with pytest.raises(OpenCodeAuthError, match=_REASON):
        OpenCodeApiParticipant(
            "seat/api",
            directory=tmp_path,
            endpoint="http://127.0.0.1:65536",
            allow_insecure_http=True,
        )
