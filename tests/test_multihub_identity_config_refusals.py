# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-hub client identity and pin configuration refusals
"""A partial client identity, a missing pin or unusable PEM material never configures a peer."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.multihub_claim_transport import parse_claim_peers
from synapse_channel.core.multihub_transport import MultiHubFetchError, pinned_connector
from synapse_channel.core.multihub_watch import MultiHubWatch

PIN = "sha256:" + "a" * 64
PAIRED = "^multi-hub client certificate and private key must be configured together$"


@pytest.mark.parametrize(("certificate", "key"), [("client.pem", None), (None, "client.key")])
def test_half_a_client_identity_is_refused_for_claims_and_watch(
    certificate: str | None, key: str | None
) -> None:
    """A certificate without its key, or a key without its certificate, is never used."""
    with pytest.raises(ValueError, match=PAIRED):
        parse_claim_peers(
            ["hub-a=ws://hub-a"], client_certificate_file=certificate, client_key_file=key
        )
    with pytest.raises(ValueError, match=PAIRED):
        MultiHubWatch(
            peers={"hub-a": "ws://hub-a"},
            local_id="local",
            client_certificate_file=certificate,
            client_key_file=key,
        )


def test_client_identity_needs_a_pin_for_every_peer() -> None:
    """Presenting a client certificate is allowed only to pinned peers."""
    with pytest.raises(
        ValueError, match="^multi-hub client identity requires --claim-peer-pin for hub 'hub-a'$"
    ):
        parse_claim_peers(
            ["hub-a=ws://hub-a"],
            client_certificate_file="client.pem",
            client_key_file="client.key",
        )
    with pytest.raises(ValueError, match="for every watched peer; missing: hub-b$"):
        MultiHubWatch(
            peers={"hub-a": "ws://hub-a", "hub-b": "ws://hub-b"},
            local_id="local",
            pins={"hub-a": PIN},
            client_certificate_file="client.pem",
            client_key_file="client.key",
        )


def test_a_pin_for_an_unconfigured_claim_peer_is_refused() -> None:
    """Every --claim-peer-pin must name a hub that --claim-peer configures."""
    with pytest.raises(
        ValueError, match="^--claim-peer-pin names hubs not configured by --claim-peer: ghost$"
    ):
        parse_claim_peers(["hub-a=ws://hub-a"], pins={"hub-a": PIN, "ghost": PIN})


def test_unusable_client_pem_fails_before_any_socket(tmp_path: Path) -> None:
    """Material that is not a certificate and key is a fetch error, raised at build time."""
    material = tmp_path / "client.pem"
    material.write_text("not PEM material\n", encoding="utf-8")
    material.chmod(0o600)
    with pytest.raises(MultiHubFetchError, match="^could not load multi-hub client identity"):
        pinned_connector(PIN, client_certificate_file=str(material), client_key_file=str(material))
