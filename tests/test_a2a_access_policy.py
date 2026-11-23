# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — characterisation of the A2A access boundary

from __future__ import annotations

from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _bridge(**kwargs: Any) -> A2ABridge:
    authorities = kwargs.pop("allowed_authorities", ("example.test",))
    return A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        allowed_authorities=authorities,
        **kwargs,
    )


def _status(bridge: A2ABridge, path: str, headers: dict[str, str]) -> int:
    status, _body = HandlerHarness("GET", path, bridge=bridge, headers=headers).run()
    return status


def test_constructor_normalises_origins_and_authorities() -> None:
    """The constructor stores one normalised origin and one normalised authority."""
    bridge = _bridge(
        allowed_origins=(" HTTPS://App.Example ",),
        allowed_authorities=("Example.TEST",),
    )
    assert bridge.allowed_origins == ("https://app.example",)
    assert bridge.allowed_authorities == ("example.test",)
    assert bridge.auth_token is None


def test_origins_without_authorities_are_refused() -> None:
    """An Origin allow-list still requires a trusted endpoint authority."""
    message = "an Origin allow-list requires a trusted endpoint authority"
    with pytest.raises(ValueError, match=message):
        _bridge(allowed_origins=("https://app.example",), allowed_authorities=())


def test_http_boundary_checks_host_origin_and_token() -> None:
    """The real handler reads the bridge boundary on the request."""
    bridge = _bridge(
        auth_token="secret",
        allowed_origins=("https://app.example",),
    )
    assert _status(bridge, "/.well-known/agent-card.json", {"Host": "other.test"}) == 403
    assert (
        _status(
            bridge,
            "/.well-known/agent-card.json",
            {"Host": "example.test", "Origin": "https://evil.example"},
        )
        == 403
    )
    assert (
        _status(
            bridge,
            "/.well-known/agent-card.json",
            {"Host": "example.test", "Origin": "https://app.example"},
        )
        == 200
    )
    assert _status(bridge, "/tasks", {"Host": "example.test"}) == 401
    assert (
        _status(
            bridge,
            "/tasks",
            {"Host": "example.test", "Authorization": "Bearer secret"},
        )
        == 200
    )


def test_an_authority_assignment_takes_effect_on_the_next_request() -> None:
    """A later authority assignment is stored as given and used by the next request."""
    bridge = _bridge()
    assert _status(bridge, "/.well-known/agent-card.json", {"Host": "example.test"}) == 200

    bridge.allowed_authorities = ("EXAMPLE.TEST",)

    assert bridge.allowed_authorities == ("EXAMPLE.TEST",)
    assert _status(bridge, "/.well-known/agent-card.json", {"Host": "example.test"}) == 403
    assert _status(bridge, "/.well-known/agent-card.json", {"Host": "EXAMPLE.TEST"}) == 403


def test_a_later_empty_authority_assignment_does_not_refuse() -> None:
    """An empty authority assignment after construction is stored and does not raise."""
    bridge = _bridge(allowed_origins=("https://app.example",))
    bridge.allowed_authorities = ()
    assert bridge.allowed_authorities == ()


def test_auth_token_and_allowed_origins_are_read_only() -> None:
    """Those two names return the constructed values and have no setter."""
    bridge = _bridge(auth_token="secret", allowed_origins=("https://app.example",))
    assert bridge.auth_token == "secret"
    assert bridge.allowed_origins == ("https://app.example",)
    holder: Any = bridge
    with pytest.raises(AttributeError):
        holder.auth_token = "other"
    with pytest.raises(AttributeError):
        holder.allowed_origins = ()
