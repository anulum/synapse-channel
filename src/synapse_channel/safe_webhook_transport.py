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

from synapse_channel.core.http_authority import normalise_url_origin

LOCAL_TARGET_ERROR = "pushNotificationConfig.webhookUrl must not target local networks"
"""Deny message raised when a webhook target is not a globally routable address."""

WEBHOOK_MAX_RESPONSE_BYTES = 64 * 1024
"""Upper bound on the discarded webhook response body, in bytes."""

WEBHOOK_MAX_REDIRECTS = 5
"""Maximum redirects admitted for one webhook delivery."""

SENSITIVE_WEBHOOK_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "cookie2"})
"""Credential-bearing headers whose authority is bound to one exact origin."""

REDIRECT_DOWNGRADE_ERROR = "webhook redirects must not downgrade HTTPS to HTTP"
AUTH_REDIRECT_STATUS_ERROR = "authenticated webhook redirects require status 307 or 308"
AUTH_REDIRECT_ORIGIN_ERROR = "sensitive webhook headers must not cross origins"
REDIRECT_TARGET_ERROR = "webhook redirect target must identify one exact HTTP(S) origin"
REDIRECT_LIMIT_ERROR = "webhook redirect limit exceeded"


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


def resolve_pinned_endpoints(hostname: str, port: int, *, allow_local: bool) -> list[str]:
    """Resolve ``hostname`` once and return every address to pin the connection to.

    Every resolved address is inspected. Unless ``allow_local`` is set, a single
    non-public answer fails the whole resolution closed, so a name that maps to
    both a public and a private address — the shape of a rebinding attack — is
    refused rather than silently connected to its public answer. The full list is
    returned in resolver order so the caller can try each validated answer in turn
    without re-resolving, which keeps a dual-stack host reachable while every
    attempt is still confined to an address checked at resolution time.

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
    list[str]
        The resolved addresses in resolver order; the caller pins to them.

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
    return addresses


def _open_pinned_socket(addresses: list[str], port: int, timeout: float | None) -> socket.socket:
    """Connect to the first reachable pinned address, trying each in order.

    None of the addresses is re-resolved, so the socket can only reach an answer
    that was validated at resolution time even when the caller falls back across a
    dual-stack host.

    Parameters
    ----------
    addresses : list of str
        Non-empty list of validated addresses in preference order.
    port : int
        Target port.
    timeout : float or None
        Per-attempt socket timeout; ``None`` blocks.

    Returns
    -------
    socket.socket
        The first connection that opened successfully.

    Raises
    ------
    OSError
        The last connection error when no pinned address was reachable.
    """
    last_error: OSError = ConnectionError("no pinned webhook address was reachable")
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout)
        except OSError as exc:
            last_error = exc
    raise last_error


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection that resolves once and connects to the pinned address."""

    def __init__(self, host: str, *, allow_local: bool = False, timeout: float) -> None:
        super().__init__(host, timeout=timeout)
        self._allow_local = allow_local

    def connect(self) -> None:
        """Open the socket to a validated address, not a re-resolved name."""
        addresses = resolve_pinned_endpoints(self.host, self.port, allow_local=self._allow_local)
        self.sock = _open_pinned_socket(addresses, self.port, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a validated address with hostname-based TLS."""

    def __init__(
        self, host: str, *, allow_local: bool = False, timeout: float, context: ssl.SSLContext
    ) -> None:
        super().__init__(host, timeout=timeout, context=context)
        self._allow_local = allow_local
        self._pinned_context = context

    def connect(self) -> None:
        """Connect to a validated address and verify TLS against the hostname."""
        addresses = resolve_pinned_endpoints(self.host, self.port, allow_local=self._allow_local)
        sock = _open_pinned_socket(addresses, self.port, self.timeout)
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
    """Apply credential-origin policy before every pinned redirect connect."""

    max_redirections = WEBHOOK_MAX_REDIRECTS
    max_repeats = min(urllib.request.HTTPRedirectHandler.max_repeats, WEBHOOK_MAX_REDIRECTS)

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Validate redirect transport, origin, status, and sensitive headers."""
        redirect_url = urljoin(req.full_url, newurl)
        redirect_history = getattr(req, "redirect_dict", {})
        if isinstance(redirect_history, dict) and (
            redirect_history.get(redirect_url, 0) >= self.max_repeats
            or len(redirect_history) >= self.max_redirections
        ):
            raise URLError(REDIRECT_LIMIT_ERROR)
        try:
            source_origin = normalise_url_origin(req.full_url)
            redirect_origin = normalise_url_origin(redirect_url)
        except ValueError as exc:
            raise URLError(REDIRECT_TARGET_ERROR) from exc

        source_scheme = urlparse(source_origin).scheme
        redirect_scheme = urlparse(redirect_origin).scheme
        if source_scheme == "https" and redirect_scheme == "http":
            raise URLError(REDIRECT_DOWNGRADE_ERROR)

        header_items = req.header_items()
        has_sensitive_headers = any(
            name.casefold() in SENSITIVE_WEBHOOK_HEADERS for name, _value in header_items
        )
        if has_sensitive_headers:
            if code not in {307, 308}:
                raise URLError(AUTH_REDIRECT_STATUS_ERROR)
            if source_origin != redirect_origin:
                raise URLError(AUTH_REDIRECT_ORIGIN_ERROR)

        if code in {307, 308}:
            return urllib.request.Request(
                redirect_url,
                data=req.data,
                headers=dict(header_items),
                method=req.get_method(),
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def describe_webhook_redirect_policy() -> dict[str, object]:
    """Return the fixed credential-custody policy used by webhook delivery."""
    return {
        "https_downgrade": "deny",
        "sensitive_headers": sorted(SENSITIVE_WEBHOOK_HEADERS),
        "authenticated_statuses": [307, 308],
        "authenticated_origin": "exact",
        "max_redirects": WEBHOOK_MAX_REDIRECTS,
    }


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
