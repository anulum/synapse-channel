# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parse the operator claim-forwarding route map (--claim-peer)
"""Parse ``--claim-peer HUB_ID=URI`` values into the claim-forwarding route map.

The library and serving sides of claim forwarding already exist; this parser is
the operator CLI seam that populates ``claim_peers`` so a hub started with
``synapse hub --claim-peer`` forwards a remote-owned claim to its owner. The map
it produces is exactly the ``{owning_hub_id: ClaimForwardPeer}`` shape the
forwarding path consumes (see ``test_hub_claim_forwarding.py``), so these tests
pin the parse contract and its parity with that consumer.
"""

from __future__ import annotations

import pytest

from synapse_channel.core.multihub_claim_transport import ClaimForwardPeer, parse_claim_peers


def test_parses_a_single_route() -> None:
    assert parse_claim_peers(["syn-owner=ws://owner:8876"]) == {
        "syn-owner": ClaimForwardPeer(uri="ws://owner:8876", token=None)
    }


def test_parses_several_routes_and_applies_the_shared_token() -> None:
    peers = parse_claim_peers(["syn-a=ws://a:8876", "syn-b=wss://b:8876"], token="secret")
    assert peers == {
        "syn-a": ClaimForwardPeer(uri="ws://a:8876", token="secret"),
        "syn-b": ClaimForwardPeer(uri="wss://b:8876", token="secret"),
    }


def test_strips_surrounding_whitespace() -> None:
    assert parse_claim_peers([" syn-a = ws://a:8876 "]) == {
        "syn-a": ClaimForwardPeer(uri="ws://a:8876", token=None)
    }


def test_empty_input_is_an_empty_map() -> None:
    assert parse_claim_peers([]) == {}


@pytest.mark.parametrize("value", ["no-separator", "=ws://a", "syn-a=", "  =  "])
def test_malformed_value_is_refused(value: str) -> None:
    with pytest.raises(ValueError, match="HUB_ID=URI"):
        parse_claim_peers([value])


def test_a_repeated_hub_id_is_refused() -> None:
    with pytest.raises(ValueError, match="names hub 'syn-a' twice"):
        parse_claim_peers(["syn-a=ws://a:8876", "syn-a=ws://other:8876"])


def test_output_matches_the_hand_built_forwarding_map() -> None:
    # The parser output is byte-for-byte the map test_hub_claim_forwarding.py builds
    # by hand for `_edge_hub`, so the CLI seam produces the exact live claim_peers
    # the forwarding path is proven to consume — no separate socket test needed.
    hand_built = {"syn-owner": ClaimForwardPeer(uri="ws://owner/")}
    assert parse_claim_peers(["syn-owner=ws://owner/"]) == hand_built
