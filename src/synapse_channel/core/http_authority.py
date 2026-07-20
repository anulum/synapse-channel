# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared exact HTTP Origin and Host authority normalisation
"""Validate concrete HTTP origins and authorities without widening trust."""

from __future__ import annotations

from urllib.parse import urlsplit


def normalise_origin(value: str) -> str:
    """Validate and normalise one concrete HTTP(S) web origin."""
    candidate = value.strip()
    if candidate.lower() == "null":
        raise ValueError("opaque 'null' origins cannot be allow-listed")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Origin must be one exact HTTP(S) origin") from exc
    if (
        _has_unsafe_authority_chars(candidate)
        or parsed.netloc.endswith(":")
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Origin must be one exact HTTP(S) origin")
    authority = _format_authority(parsed.hostname, port)
    return f"{parsed.scheme.lower()}://{authority}"


def normalise_authority(value: str) -> str:
    """Validate and normalise one HTTP Host authority without widening it."""
    candidate = value.strip()
    try:
        parsed = urlsplit(f"//{candidate}")
        port = parsed.port
    except ValueError as exc:
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
    return _format_authority(parsed.hostname, port)


def endpoint_authorities(endpoint_url: str) -> tuple[str, ...]:
    """Return exact Host authorities admitted by one advertised endpoint URL."""
    try:
        parsed = urlsplit(endpoint_url.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint URL must identify one HTTP(S) authority") from exc
    if (
        _has_unsafe_authority_chars(parsed.netloc)
        or parsed.netloc.endswith(":")
        or parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("endpoint URL must identify one HTTP(S) authority")
    authority = _format_authority(parsed.hostname, port)
    if port is not None:
        return (authority,)
    default_port = 80 if parsed.scheme.lower() == "http" else 443
    return (authority, f"{authority}:{default_port}")


def _format_authority(hostname: str, port: int | None) -> str:
    """Return a lower-case DNS/IPv4/IPv6 authority with an optional port."""
    host = hostname.rstrip(".").lower()
    if not host:
        raise ValueError("authority host must not be empty")
    rendered = f"[{host}]" if ":" in host else host
    return rendered if port is None else f"{rendered}:{port}"


def _has_unsafe_authority_chars(value: str) -> bool:
    """Return whether an authority-bearing value contains delimiter ambiguity."""
    return any(
        character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ) or any(character in value for character in (",", "\\"))


__all__ = ["endpoint_authorities", "normalise_authority", "normalise_origin"]
