# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — test hubs get a port that is free on both loopback families
"""A hub bound to ``localhost`` listens on 127.0.0.1 and ::1, so both must be free."""

from __future__ import annotations

import socket

from hub_e2e_helpers import free_port


def _ipv6_loopback_available() -> bool:
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as probe:
            probe.bind(("::1", 0))
    except OSError:
        return False
    return True


def test_returned_port_binds_on_both_loopback_families() -> None:
    """Every returned port can be bound at once on 127.0.0.1 and, when present, on ::1."""
    dual = _ipv6_loopback_available()
    for _ in range(25):
        port = free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ipv4:
            ipv4.bind(("127.0.0.1", port))
            if dual:
                with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as ipv6:
                    ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    ipv6.bind(("::1", port))
