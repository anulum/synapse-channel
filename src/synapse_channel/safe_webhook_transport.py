# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — SSRF-resistant outbound HTTP transport for webhook delivery
"""DNS-rebinding-resistant outbound HTTP(S) transport.

An outbound webhook is a server-side request to an operator-supplied URL, so it
is a classic server-side request forgery (SSRF) surface. Validating the target
host and then handing the *hostname* to a separate HTTP client re-resolves DNS at
connect time, leaving a time-of-check/time-of-use window: the name can resolve to
a permitted public address during validation and to a loopback, private, or
cloud-metadata address when the socket is actually opened.

This module closes that window. It resolves each target exactly once, admits only
globally routable destinations under a positive policy (every category that is not
public — loopback, private, link-local, carrier-grade NAT, multicast, reserved,
and unspecified — is refused, including IPv4-mapped IPv6 forms), and then pins the
connection to the validated address while still presenting the original hostname
for the HTTP ``Host`` header, TLS SNI, and certificate verification. Redirects run
through the same pinned, re-validated path, environment proxies are disabled so a
rogue ``*_proxy`` variable cannot redirect delivery, and the response body is read
under a fixed bound so a hostile receiver cannot exhaust process memory.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import urllib.request
from http.client import HTTPMessage
from typing import IO
from urllib.error import URLError
from urllib.parse import urljoin, urlparse

LOCAL_TARGET_ERROR = "pushNotificationConfig.webhookUrl must not target local networks"
"""Deny message raised when a webhook target is not a globally routable address."""

WEBHOOK_MAX_RESPONSE_BYTES = 64 * 1024
"""Upper bound on the discarded webhook response body, in bytes."""


def is_public_address(raw_address: str) -> bool:
    """Return whether ``raw_address`` is a globally routable destination.

    The check is a positive policy: only globally routable unicast addresses are
    accepted. Loopback, private, link-local, carrier-grade NAT, multicast,
    reserved, and unspecified addresses are all rejected, and an IPv4-mapped IPv6
    address is unwrapped so it cannot smuggle a private IPv4 target past the
    filter.

    Parameters
    ----------
    raw_address : str
        Numeric IPv4 or IPv6 address, optionally carrying an IPv6 zone suffix.

    Returns
    -------
    bool
        ``True`` only when the address is globally routable.
    """
    try:
        address: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
            raw_address.split("%", 1)[0]
        )
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    ):
        return False
    return address.is_global


def resolve_pinned_endpoint(hostname: str, port: int, *, allow_local: bool) -> str:
    """Resolve ``hostname`` once and return one address to pin the connection to.

    Every resolved address is inspected. Unless ``allow_local`` is set, a single
    non-public answer fails the whole resolution closed, so a name that maps to
    both a public and a private address — the shape of a rebinding attack — is
    refused rather than silently connected to its public answer.

    Parameters
    ----------
    hostname : str
        Target host to resolve.
    port : int
        Target port, resolved together with the host so service records match.
    allow_local : bool
        When true, skip the public-address policy (test and loopback receivers).

    Returns
    -------
    str
        The first resolved address; the caller pins the connection to it.

    Raises
    ------
    urllib.error.URLError
        If the host cannot be resolved or resolves to a non-public address while
        ``allow_local`` is false.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise URLError(f"could not resolve webhook target {hostname}: {exc}") from exc
    addresses = [str(info[4][0]) for info in infos if info[4]]
    if not addresses:
        raise URLError(f"could not resolve webhook target {hostname}")
    if not allow_local:
        for address in addresses:
            if not is_public_address(address):
                raise URLError(LOCAL_TARGET_ERROR)
    return addresses[0]


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that resolves once and connects to the pinned address."""

    def __init__(self, host: str, *, allow_local: bool = False, timeout: float) -> None:
        super().__init__(host, timeout=timeout)
        self._allow_local = allow_local

    def connect(self) -> None:
        """Open the socket to the validated address, not a re-resolved name."""
        pinned = resolve_pinned_endpoint(self.host, self.port, allow_local=self._allow_local)
        self.sock = socket.create_connection((pinned, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a validated address with hostname-based TLS."""

    def __init__(
        self, host: str, *, allow_local: bool = False, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, timeout=timeout, context=context)
        self._allow_local = allow_local
        self._pinned_context = context

    def connect(self) -> None:
        """Connect to the pinned address and verify TLS against the hostname."""
        pinned = resolve_pinned_endpoint(self.host, self.port, allow_local=self._allow_local)
        sock = socket.create_connection((pinned, self.port), self.timeout)
        self.sock = self._pinned_context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    """urllib handler that routes plain HTTP through a pinned connection."""

    def __init__(self, *, allow_local: bool) -> None:
        super().__init__()
        self._allow_local = allow_local

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        """Open ``req`` through a pinned, re-validated HTTP connection."""

        def build(host: str, *, timeout: float) -> _PinnedHTTPConnection:
            return _PinnedHTTPConnection(host, allow_local=self._allow_local, timeout=timeout)

        # do_open's typeshed stub types the factory as a class; urllib accepts any
        # connection factory callable, so the argument type is deliberately widened.
        return self.do_open(build, req)  # type: ignore[arg-type]


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    """urllib handler that routes HTTPS through a pinned connection."""

    def __init__(self, *, allow_local: bool, context: ssl.SSLContext) -> None:
        super().__init__(context=context)
        self._allow_local = allow_local
        self._ssl_context = context

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        """Open ``req`` through a pinned, re-validated HTTPS connection."""

        def build(host: str, *, timeout: float, context: ssl.SSLContext) -> _PinnedHTTPSConnection:
            return _PinnedHTTPSConnection(
                host, allow_local=self._allow_local, timeout=timeout, context=context
            )

        # do_open's typeshed stub types the factory as a class; urllib accepts any
        # connection factory callable, so the argument type is deliberately widened.
        return self.do_open(build, req, context=self._ssl_context)  # type: ignore[arg-type]


class _SafePinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to http(s) targets and preserve the POST body."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Validate a redirect target's scheme before the pinned connect runs."""
        redirect_url = urljoin(req.full_url, newurl)
        scheme = urlparse(redirect_url).scheme
        if scheme not in {"http", "https"}:
            raise URLError("webhook redirect target must use http or https")
        if code in {307, 308}:
            return urllib.request.Request(
                redirect_url,
                data=req.data,
                headers=dict(req.headers),
                method=req.get_method(),
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_safe_opener(
    *, allow_local: bool, ca_file: str | None = None
) -> urllib.request.OpenerDirector:
    """Build an opener that pins connections and ignores environment proxies.

    Parameters
    ----------
    allow_local : bool
        When true, permit loopback and private targets (test and dev receivers).
    ca_file : str or None, optional
        PEM trust anchor for HTTPS receivers; the system trust store is used when
        it is ``None``.

    Returns
    -------
    urllib.request.OpenerDirector
        Opener whose HTTP, HTTPS, and redirect handlers all pin to a validated
        address and whose proxy handler is empty so ``*_proxy`` variables are
        ignored.
    """
    context = ssl.create_default_context(cafile=ca_file)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedHTTPHandler(allow_local=allow_local),
        _PinnedHTTPSHandler(allow_local=allow_local, context=context),
        _SafePinnedRedirectHandler(),
    )


def read_bounded(response: http.client.HTTPResponse) -> None:
    """Drain a discarded webhook response under a fixed byte bound.

    Parameters
    ----------
    response : http.client.HTTPResponse
        Open response whose body is not needed; at most
        :data:`WEBHOOK_MAX_RESPONSE_BYTES` are read so a hostile receiver cannot
        stream an unbounded body into memory.
    """
    response.read(WEBHOOK_MAX_RESPONSE_BYTES)
