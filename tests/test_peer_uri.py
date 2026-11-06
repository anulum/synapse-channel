# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — peer URIs the WebSocket client cannot open are refused at startup
"""A message, claim or watch peer URI is refused when configured, not when first used."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from cli_processes_helpers import _hub_ns
from cli_processes_hub_helpers import _close_runner
from synapse_channel import cli_processes
from synapse_channel.core.message_forward_transport import parse_message_peers
from synapse_channel.core.multihub_claim_transport import parse_claim_peers
from synapse_channel.core.multihub_watch import parse_watch_peers
from synapse_channel.core.peer_uri import require_peer_uri

_SECRET = "s" * 12
_NEEDS_ESCAPING = "a character that needs escaping"

_UNUSABLE = [
    ("ws://hub-b:8876/route#part", "fragment identifier is meaningless"),
    ("ws:///route", "hostname isn't provided"),
    ("ws://hub-b:99999/", "Port out of range"),
    (f"ws://op:{_SECRET}\\x@hub-b/ws", _NEEDS_ESCAPING),
    (f"ws://op:{_SECRET}@hub-b/\x00", _NEEDS_ESCAPING),
    (f"ws://op:{_SECRET}@hub-b/a\u200bb", _NEEDS_ESCAPING),
    (f'ws://op:{_SECRET}@hub-b/it\'s-"q"', _NEEDS_ESCAPING),
]

_PARSERS: dict[str, Callable[[list[str]], object]] = {
    "--message-peer": parse_message_peers,
    "--claim-peer": parse_claim_peers,
    "--multihub-watch": parse_watch_peers,
}


@pytest.mark.parametrize("flag", sorted(_PARSERS))
@pytest.mark.parametrize(("uri", "reason"), _UNUSABLE)
def test_every_peer_parser_refuses_an_unusable_uri_without_echoing_it(
    flag: str, uri: str, reason: str
) -> None:
    """The refusal names the option, the peer and the reason, never the URI itself."""
    with pytest.raises(ValueError) as refused:
        _PARSERS[flag]([f"hub-b={uri}"])
    message = str(refused.value)
    assert message.startswith(f"{flag} URI for 'hub-b' is not a usable WebSocket URI: ")
    assert reason in message and _SECRET not in message


@pytest.mark.parametrize("flag", ["--claim-peer", "--multihub-watch"])
def test_claim_and_watch_peers_need_a_websocket_scheme(flag: str) -> None:
    """A claim or watch peer was not checked for its scheme at all; now it is."""
    with pytest.raises(ValueError, match="scheme isn't ws or wss"):
        _PARSERS[flag](["hub-b=http://hub-b:8876"])


@pytest.mark.parametrize(
    "uri",
    [
        "wss://op:secret@hub-b:443/ws?key=1",
        "ws://[::1]:8876",
        "ws://hub-b",
        "ws://hüb/",
        "ws://hub-b/it's",
    ],
)
def test_a_usable_uri_passes_unchanged(uri: str) -> None:
    """Userinfo, a query, IPv6, non-ASCII host names and one kind of quote stay acceptable."""
    require_peer_uri("--message-peer", "hub-b", uri)
    assert parse_message_peers([f"hub-b={uri}"])["hub-b"].uri == uri


@pytest.mark.parametrize(
    "options",
    [
        {"message_peer": ["hub-b=ws://hub-b/#part"]},
        {"namespace_owner": ["OWNED=syn-a"], "claim_peer": ["hub-b=ws://hub-b/#part"]},
        {"namespace_owner": ["OWNED=syn-a"], "multihub_watch": ["hub-b=ws://hub-b/#part"]},
    ],
)
def test_the_hub_refuses_to_start_with_an_unusable_peer_uri(
    options: dict[str, object], capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse hub`` exits 2 with the reason before it serves anything."""
    assert cli_processes._cmd_hub(_hub_ns(hub_id="syn-a", **options), runner=_close_runner) == 2
    assert "is not a usable WebSocket URI: fragment identifier is meaningless" in (
        capsys.readouterr().err
    )
