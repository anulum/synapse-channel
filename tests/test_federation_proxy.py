# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation proxy path policy tests
"""Test the federation proxy path classifier."""

from __future__ import annotations

from synapse_channel.core.federation_proxy import (
    classify_federation_proxy_path,
    normalise_federation_path_mode,
)


def test_direct_mtls_preserves_federation_pinning_boundary() -> None:
    """Direct WSS/mTLS keeps the hub certificate visible to the peer."""
    verdict = classify_federation_proxy_path("direct-mtls")

    assert verdict.status == "pass"
    assert "preserves the peer certificate pin" in verdict.detail
    assert verdict.remedy == ""


def test_tls_passthrough_preserves_hub_certificate_boundary() -> None:
    """TCP/TLS passthrough keeps socket-level mTLS at the hub."""
    verdict = classify_federation_proxy_path("tls-passthrough")

    assert verdict.status == "pass"
    assert "hub TLS certificate" in verdict.detail
    assert "client certificates" in verdict.detail


def test_tailnet_path_is_accepted_as_private_wan_transport() -> None:
    """Tailnet paths remain valid when paired with token and pin ceremonies."""
    verdict = classify_federation_proxy_path("tailnet")

    assert verdict.status == "pass"
    assert "off the public internet" in verdict.detail
    assert "token and certificate-pin ceremony" in verdict.detail


def test_tls_terminating_proxy_fails_for_socket_level_federation_pinning() -> None:
    """TLS termination changes the certificate and client-cert trust boundary."""
    verdict = classify_federation_proxy_path("tls-terminating-proxy")

    assert verdict.status == "fail"
    assert "proxy certificate" in verdict.detail
    assert "do not reach the hub" in verdict.detail
    assert "do not treat plain TLS termination" in verdict.remedy


def test_normalise_federation_path_mode_accepts_operator_aliases() -> None:
    """Common operator spellings collapse to supported modes."""
    assert normalise_federation_path_mode("direct") == "direct-mtls"
    assert normalise_federation_path_mode("TLS_PASS_THROUGH") == "tls-passthrough"
    assert normalise_federation_path_mode("caddy") == "tls-terminating-proxy"
    assert normalise_federation_path_mode("tailnet") == "tailnet"
    assert normalise_federation_path_mode("unknown") is None
