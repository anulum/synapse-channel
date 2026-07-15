# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard Host-header boundary unit tests

"""Unit tests for the dashboard DNS-rebinding Host boundary."""

from __future__ import annotations

import pytest

from synapse_channel.dashboard_host_guard import (
    allowed_host_authorities,
    host_allowed,
    is_unspecified_host,
)


def test_loopback_and_bind_authorities_are_always_admitted() -> None:
    """The loopback names and the concrete bind host are admitted at the port."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert "localhost:8765" in allowed
    assert "127.0.0.1:8765" in allowed
    assert "[::1]:8765" in allowed


def test_wildcard_bind_host_contributes_no_bind_authority() -> None:
    """A ``0.0.0.0`` bind is a dead authority, so only loopback is derived."""
    allowed = allowed_host_authorities("0.0.0.0", 9000)  # nosec B104
    assert "0.0.0.0:9000" not in allowed
    assert "0.0.0.0" not in allowed
    assert "localhost:9000" in allowed


def test_ipv6_wildcard_bind_host_contributes_no_bind_authority() -> None:
    """A ``::`` bind is likewise a dead authority."""
    allowed = allowed_host_authorities("::", 9000)
    assert "[::]:9000" not in allowed
    assert "[::]" not in allowed
    assert "[::1]:9000" in allowed


def test_concrete_non_loopback_bind_admits_its_own_authority() -> None:
    """A concrete LAN bind admits the address a client actually presents."""
    allowed = allowed_host_authorities("192.168.1.50", 8765)
    assert "192.168.1.50:8765" in allowed
    assert "192.168.1.50" in allowed


@pytest.mark.parametrize("wildcard", ["0.0.0.0", "::", "[::]", " 0.0.0.0 "])
def test_is_unspecified_host_detects_wildcards(wildcard: str) -> None:
    """The wildcard IPv4 and IPv6 bind addresses are recognised."""
    assert is_unspecified_host(wildcard) is True


@pytest.mark.parametrize("concrete", ["127.0.0.1", "::1", "localhost", "192.168.1.50", ""])
def test_is_unspecified_host_rejects_concrete_and_named_hosts(concrete: str) -> None:
    """A concrete address or a host name is not the wildcard bind."""
    assert is_unspecified_host(concrete) is False


def test_ipv6_bind_host_is_bracketed() -> None:
    """An IPv6 bind host is bracketed into a valid authority."""
    allowed = allowed_host_authorities("::1", 8000)
    assert "[::1]:8000" in allowed


def test_bindings_are_scoped_to_the_served_port() -> None:
    """An admitted host at one port is not admitted at another."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert "127.0.0.1:9999" not in allowed
    assert "localhost:9999" not in allowed


def test_port_less_extra_host_admits_bare_and_port_paired_forms() -> None:
    """A port-less operator host admits both the proxy and direct-hit shapes."""
    allowed = allowed_host_authorities("127.0.0.1", 8765, ("dash.internal",))
    assert "dash.internal" in allowed
    assert "dash.internal:8765" in allowed


def test_extra_host_with_explicit_port_is_honoured_exactly() -> None:
    """An operator host carrying a port admits only that exact authority."""
    allowed = allowed_host_authorities("127.0.0.1", 8765, ("proxy.example:443",))
    assert "proxy.example:443" in allowed
    assert "proxy.example:8765" not in allowed
    assert "proxy.example" not in allowed


def test_extra_ipv6_host_is_admitted() -> None:
    """A bracketed IPv6 operator host is admitted at the served port and bare."""
    allowed = allowed_host_authorities("127.0.0.1", 8765, ("[2001:db8::1]",))
    assert "[2001:db8::1]:8765" in allowed
    assert "[2001:db8::1]" in allowed


def test_extra_host_uppercase_is_normalised() -> None:
    """An operator host is compared case-insensitively after normalisation."""
    allowed = allowed_host_authorities("127.0.0.1", 8765, ("Dash.Example:8443",))
    assert "dash.example:8443" in allowed


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_extra_host_is_ignored(blank: str) -> None:
    """A blank operator host contributes nothing rather than a bad authority."""
    baseline = allowed_host_authorities("127.0.0.1", 8765)
    assert allowed_host_authorities("127.0.0.1", 8765, (blank,)) == baseline


@pytest.mark.parametrize("bad", ["bad host", "http://x", "host:", "a,b", "host:abc", "host:99999"])
def test_malformed_extra_host_raises_at_build_time(bad: str) -> None:
    """A malformed operator host fails loudly so startup surfaces the error."""
    with pytest.raises(ValueError):
        allowed_host_authorities("127.0.0.1", 8765, (bad,))


def test_host_allowed_accepts_a_listed_authority() -> None:
    """A request whose Host names an admitted authority passes."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed("127.0.0.1:8765", allowed) is True
    assert host_allowed("localhost:8765", allowed) is True


@pytest.mark.parametrize("bare", ["localhost", "127.0.0.1", "[::1]"])
def test_host_allowed_accepts_a_port_less_loopback_authority(bare: str) -> None:
    """A lenient client that drops the port on a loopback host still passes."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed(bare, allowed) is True


def test_host_allowed_is_case_insensitive_on_host() -> None:
    """A Host differing only in case still matches its authority."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed("LOCALHOST:8765", allowed) is True


def test_host_allowed_refuses_a_rebinding_authority() -> None:
    """An attacker-chosen Host is refused — the rebinding case."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed("attacker.example:8765", allowed) is False


def test_host_allowed_admits_a_trailing_dot_loopback_name() -> None:
    """A trailing-dot FQDN form of a loopback name normalises into the set.

    The shared authority normaliser strips a trailing root dot, so ``localhost.``
    collapses to ``localhost``. This is safe: an attacker cannot control how the
    loopback names resolve, and the loopback read path segregates nothing.
    """
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed("localhost.:8765", allowed) is True


def test_host_allowed_refuses_a_matching_host_on_the_wrong_port() -> None:
    """A loopback host on a foreign port is refused."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed("127.0.0.1:9999", allowed) is False


@pytest.mark.parametrize("absent", [None, "", "   "])
def test_host_allowed_fails_closed_on_absent_host(absent: str | None) -> None:
    """An absent or blank Host header is refused so the boundary fails closed."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed(absent, allowed) is False


@pytest.mark.parametrize("malformed", ["bad host", "a,b", "host with spaces:8765"])
def test_host_allowed_fails_closed_on_malformed_host(malformed: str) -> None:
    """A malformed Host header is refused rather than raising."""
    allowed = allowed_host_authorities("127.0.0.1", 8765)
    assert host_allowed(malformed, allowed) is False
