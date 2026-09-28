# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hub-qualified seat address parsing and forwarded sender names
"""Hub-qualified addresses decide which messages leave the hub, so the parser is strict."""

from __future__ import annotations

import pytest

from synapse_channel.core.hub_address import (
    HubQualifiedAddress,
    federated_sender,
    is_hub_qualified_name,
    is_single_seat,
    is_valid_hub_id,
    parse_hub_qualified,
)


@pytest.mark.parametrize(
    ("value", "seat", "hub_id"),
    [
        ("SYNAPSE-CHANNEL/agent-a7c2@laptop", "SYNAPSE-CHANNEL/agent-a7c2", "laptop"),
        ("agent@hub.example_1-2", "agent", "hub.example_1-2"),
        ("PROJ/seat@9hub", "PROJ/seat", "9hub"),
    ],
)
def test_hub_qualified_address_round_trips(value: str, seat: str, hub_id: str) -> None:
    """The last ``@`` splits a valid address, and rendering restores the wire form."""
    parsed = parse_hub_qualified(value)
    assert parsed == HubQualifiedAddress(seat=seat, hub_id=hub_id)
    assert str(parsed) == value


@pytest.mark.parametrize(
    "value",
    [
        "PROJ/seat",  # no hub
        "@laptop",  # no seat
        "PROJ/seat@",  # empty hub
        "PROJ/seat@-laptop",  # hub must start alphanumeric
        "PROJ/seat@lap top",  # space in hub
        "PROJ/seat@lap/top",  # slash in hub
        "PROJ/se@at@laptop",  # a second @ in the seat part
        " PROJ/seat@laptop",  # surrounding whitespace in the seat
        "PROJ/se\nat@laptop",  # control character in the seat
        "PROJ/seat@" + "h" * 65,  # hub id over 64 characters
        "s" * 257 + "@laptop",  # seat over 256 characters
        "all",
    ],
)
def test_parse_refuses_anything_that_is_not_a_hub_qualified_seat(value: str) -> None:
    """Malformed values stay local targets instead of being forwarded somewhere unexpected."""
    assert parse_hub_qualified(value) is None


def test_hub_id_bounds() -> None:
    """Hub ids are 1–64 characters from a small safe alphabet."""
    assert is_valid_hub_id("h" * 64)
    assert not is_valid_hub_id("h" * 65)
    assert not is_valid_hub_id("")
    assert not is_valid_hub_id("hub:8876")


def test_federated_sender_names_the_authenticated_origin() -> None:
    """A forwarded sender is always presented with the hub that forwarded it."""
    assert federated_sender("PROJ/alice", "laptop") == "PROJ/alice@laptop"
    parsed = parse_hub_qualified(federated_sender("PROJ/alice", "laptop"))
    assert parsed == HubQualifiedAddress(seat="PROJ/alice", hub_id="laptop")


@pytest.mark.parametrize(
    ("sender", "origin_hub"),
    [
        ("", "laptop"),
        ("PROJ/alice@elsewhere", "laptop"),  # a peer cannot chain another hub's name
        ("PROJ/alice", "bad hub"),
        ("PROJ/alice", ""),
    ],
)
def test_federated_sender_refuses_ambiguous_provenance(sender: str, origin_hub: str) -> None:
    """Provenance is never built from an empty, already-qualified or invalid identity."""
    with pytest.raises(ValueError):
        federated_sender(sender, origin_hub)


def test_every_at_name_is_hub_qualified_for_the_reservation_gate() -> None:
    """The registration gate reserves the whole ``@`` form, valid or not."""
    assert is_hub_qualified_name("PROJ/alice@laptop")
    assert is_hub_qualified_name("weird@@name")
    assert not is_hub_qualified_name("PROJ/alice")


@pytest.mark.parametrize(
    ("seat", "single"),
    [
        ("PROJ/bob", True),
        ("PROJ/coordinator", True),
        ("", False),
        (" PROJ/bob", False),
        ("all", False),
        ("PROJ/bob,OTHER/eve", False),
        ("PROJ/*", False),
        ("PROJ/b?b", False),
        ("PROJ/b[ob]", False),
    ],
)
def test_is_single_seat_refuses_lists_globs_and_audiences(seat: str, single: bool) -> None:
    """Only one exact seat or role may be addressed across hubs."""
    assert is_single_seat(seat) is single
