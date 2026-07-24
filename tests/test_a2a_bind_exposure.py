# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unit tests for A2A bind exposure matrix
"""Pure bind-matrix coverage for :mod:`synapse_channel.a2a_bind_exposure`."""

from __future__ import annotations

import pytest

from synapse_channel.a2a_bind_exposure import a2a_bind_problems, is_loopback_host


@pytest.mark.parametrize(
    "host",
    ("127.0.0.1", "localhost", "LOCALHOST", "::1", " 127.0.0.1 "),
)
def test_loopback_hosts_never_report_problems(host: str) -> None:
    """Loopback binds stay open for local development with or without bearer."""
    assert a2a_bind_problems(host, bearer_auth=False) == []
    assert a2a_bind_problems(host, bearer_auth=True) == []
    assert a2a_bind_problems(host, bearer_auth=True, tls_active=False) == []
    assert a2a_bind_problems(host, bearer_auth=True, tls_active=True) == []
    assert is_loopback_host(host)


def test_non_loopback_without_bearer_refuses() -> None:
    """Off-loopback open HTTP is the original refuse posture."""
    problems = a2a_bind_problems("0.0.0.0", bearer_auth=False)
    assert len(problems) == 1
    assert "without --bearer-auth" in problems[0]
    assert "0.0.0.0" in problems[0]
    assert "insecure-off-loopback" in problems[0]


def test_non_loopback_with_bearer_over_plaintext_refuses() -> None:
    """Bearer on cleartext HTTP off loopback is the new R4-parity refuse."""
    problems = a2a_bind_problems("0.0.0.0", bearer_auth=True, tls_active=False)
    assert len(problems) == 1
    assert "plaintext HTTP" in problems[0]
    assert "bearer" in problems[0].lower()
    assert "0.0.0.0" in problems[0]
    assert "insecure-off-loopback" in problems[0]


def test_non_loopback_with_bearer_and_tls_is_clear() -> None:
    """tls_active clears only the plaintext-bearer problem (future native TLS)."""
    assert a2a_bind_problems("0.0.0.0", bearer_auth=True, tls_active=True) == []


def test_non_loopback_without_bearer_ignores_tls_flag() -> None:
    """TLS does not make an unauthenticated off-loopback bind safe."""
    problems = a2a_bind_problems("192.168.1.10", bearer_auth=False, tls_active=True)
    assert len(problems) == 1
    assert "without --bearer-auth" in problems[0]


def test_lan_host_bearer_plaintext_same_as_bind_all() -> None:
    """Any non-loopback host name triggers the plaintext-bearer check."""
    problems = a2a_bind_problems("10.0.0.5", bearer_auth=True)
    assert len(problems) == 1
    assert "plaintext HTTP" in problems[0]
