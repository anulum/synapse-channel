# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared exact HTTP Origin and Host authority normalisation
"""Validate concrete HTTP origins and authorities without widening trust."""

from __future__ import annotations

from urllib.parse import SplitResult, urlsplit


def normalise_origin(value: str) -> str:
    """Validate and normalise one concrete HTTP(S) web origin."""
    candidate = value.strip()
    if candidate.lower() == "null":
        raise ValueError("opaque 'null' origins cannot be allow-listed")
    parsed, hostname, port = _parse_http_url(
        candidate,
        error="Origin must be one exact HTTP(S) origin",
    )
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Origin must be one exact HTTP(S) origin")
    try:
        authority = _format_authority(hostname, port)
    except ValueError as exc:
        raise ValueError("Origin must be one exact HTTP(S) origin") from exc
    return f"{parsed.scheme.lower()}://{authority}"


def normalise_authority(value: str) -> str:
    """Validate and normalise one HTTP Host authority without widening it."""
    candidate = value.strip()
    try:
        parsed = urlsplit(f"//{candidate}")
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Host must be one exact host[:port] authority") from exc
    if (
        not candidate
        or _has_unsafe_authority_chars(candidate)
        or candidate.endswith(":")
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Host must be one exact host[:port] authority")
    try:
        return _format_authority(parsed.hostname, port)
    except ValueError as exc:
        raise ValueError("Host must be one exact host[:port] authority") from exc


def endpoint_authorities(endpoint_url: str) -> tuple[str, ...]:
    """Return exact Host authorities admitted by one advertised endpoint URL."""
    parsed, hostname, port = _parse_http_url(
        endpoint_url.strip(),
        error="endpoint URL must identify one HTTP(S) authority",
    )
    try:
        authority = _format_authority(hostname, port)
    except ValueError as exc:
        raise ValueError("endpoint URL must identify one HTTP(S) authority") from exc
    if port is not None:
        return (authority,)
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    return (authority, f"{authority}:{default_port}")


def normalise_url_origin(value: str) -> str:
    """Return one full HTTP(S) URL's canonical origin with an effective port.

    Paths, queries, and fragments are intentionally ignored because they are not
    part of origin identity. Credentials, ambiguous authority syntax, malformed
    ports, and non-HTTP schemes fail closed.
    """
    parsed, hostname, port = _parse_http_url(
        value.strip(),
        error="URL must identify one exact HTTP(S) origin",
    )
    effective_port = port
    if effective_port is None:
        effective_port = 80 if parsed.scheme.lower() == "http" else 443
    try:
        authority = _format_authority(hostname, effective_port)
    except ValueError as exc:
        raise ValueError("URL must identify one exact HTTP(S) origin") from exc
    return f"{parsed.scheme.lower()}://{authority}"


def _parse_http_url(value: str, *, error: str) -> tuple[SplitResult, str, int | None]:
    """Parse and validate the authority-bearing portion of one HTTP(S) URL."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise ValueError(error) from exc
    if (
        _has_unsafe_authority_chars(parsed.netloc)
        or parsed.netloc.endswith(":")
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(error)
    return parsed, parsed.hostname, port


def _format_authority(hostname: str, port: int | None) -> str:
    """Return a lower-case DNS/IPv4/IPv6 authority with an optional port."""
    host = hostname.rstrip(".").lower()
    if not host:
        raise ValueError("authority host must not be empty")
    if ":" in host:
        rendered = f"[{host}]"
    else:
        try:
            rendered = host.encode("ascii").decode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError("authority host must use canonical ASCII or punycode") from exc
    return rendered if port is None else f"{rendered}:{port}"


def _has_unsafe_authority_chars(value: str) -> bool:
    """Return whether an authority-bearing value contains delimiter ambiguity."""
    return any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ) or any(character in value for character in (",", "\\", "%"))


__all__ = [
    "endpoint_authorities",
    "normalise_authority",
    "normalise_origin",
    "normalise_url_origin",
]
