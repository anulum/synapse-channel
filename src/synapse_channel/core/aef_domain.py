# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Agent Evidence Format domain separation
"""Canonical versioned domains for Agent Evidence Format signatures.

AEF v0.1 uses ``aef:<purpose>:v<major>.<minor>`` and one NUL separator before
the canonical payload.  Legacy Synapse signatures keep their historical domain
bytes; changing them would invalidate already-issued evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from synapse_channel.core.aef_canonical import IJSON_MAX_INTEGER

AEF_VERSION_MAJOR = 0
"""Major format version assigned by the AEF v0.1 profile."""

AEF_VERSION_MINOR = 1
"""Minor format version assigned by the AEF v0.1 profile."""

MAX_AEF_PURPOSE_LENGTH = 64
"""Maximum ASCII characters accepted in a core or extension purpose token."""

_DOMAIN_PATTERN = re.compile(
    r"aef:(?P<purpose>[a-z][a-z0-9]*(?:-[a-z0-9]+)*):"
    r"v(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)"
)
_PURPOSE_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_PREIMAGE_SEPARATOR = b"\x00"
_MAX_VERSION_TEXT = str(IJSON_MAX_INTEGER)


class AefDomainError(ValueError):
    """Raised when an AEF signature domain or preimage is malformed."""


@dataclass(frozen=True, slots=True)
class AefDomain:
    """Parsed canonical AEF signature domain.

    Parameters
    ----------
    purpose:
        Lowercase ASCII token. Hyphens may separate non-empty alphanumeric
        components; underscores, uppercase text, and consecutive hyphens are
        forbidden.
    major, minor:
        Non-boolean I-JSON integers. Canonical text never contains leading
        zeroes.
    """

    purpose: str
    major: int
    minor: int

    def __post_init__(self) -> None:
        """Reject any noncanonical purpose or version component."""
        _require_purpose(self.purpose)
        _require_version_component("major", self.major)
        _require_version_component("minor", self.minor)

    def __str__(self) -> str:
        """Return the canonical ASCII domain carried in signature metadata."""
        return f"aef:{self.purpose}:v{self.major}.{self.minor}"

    def preimage(self, payload: bytes) -> bytes:
        """Prefix canonical payload bytes with this domain and one NUL byte."""
        if not isinstance(payload, bytes):
            raise AefDomainError("AEF signature payload must be bytes")
        return str(self).encode("ascii") + _PREIMAGE_SEPARATOR + payload


def parse_aef_domain(raw: object) -> AefDomain:
    """Parse one canonical AEF domain string.

    The parser rejects alternate spellings rather than normalising them.  That
    makes a version or purpose mismatch fail before cryptographic verification
    and prevents cross-purpose or cross-version signature reuse.
    """
    if not isinstance(raw, str):
        raise AefDomainError("AEF signature domain must be text")
    match = _DOMAIN_PATTERN.fullmatch(raw)
    if match is None:
        raise AefDomainError("invalid AEF signature domain")
    purpose = match.group("purpose")
    _require_purpose(purpose)
    major = _parse_version_component("major", match.group("major"))
    minor = _parse_version_component("minor", match.group("minor"))
    return AefDomain(purpose, major, minor)


def aef_signature_preimage(domain: AefDomain | str, payload: bytes) -> bytes:
    """Return ``ASCII(domain) || NUL || payload`` after strict domain parsing."""
    parsed = parse_aef_domain(domain) if isinstance(domain, str) else domain
    if not isinstance(parsed, AefDomain):
        raise AefDomainError("AEF signature domain must be text or AefDomain")
    return parsed.preimage(payload)


def _require_purpose(purpose: object) -> None:
    if not isinstance(purpose, str):
        raise AefDomainError("AEF domain purpose must be text")
    if len(purpose) > MAX_AEF_PURPOSE_LENGTH:
        raise AefDomainError("AEF domain purpose exceeds the 64-character limit")
    if _PURPOSE_PATTERN.fullmatch(purpose) is None:
        raise AefDomainError("invalid AEF domain purpose token")


def _require_version_component(name: str, value: object) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > IJSON_MAX_INTEGER
    ):
        raise AefDomainError(f"AEF domain {name} version must be an I-JSON non-negative integer")


def _parse_version_component(name: str, raw: str) -> int:
    if len(raw) > len(_MAX_VERSION_TEXT) or (
        len(raw) == len(_MAX_VERSION_TEXT) and raw > _MAX_VERSION_TEXT
    ):
        raise AefDomainError(f"AEF domain {name} version must be an I-JSON non-negative integer")
    return int(raw)


AEF_RECEIPT_DOMAIN = AefDomain("receipt", AEF_VERSION_MAJOR, AEF_VERSION_MINOR)
"""Domain assigned to AEF v0.1 receipt signatures."""

AEF_STH_DOMAIN = AefDomain("sth", AEF_VERSION_MAJOR, AEF_VERSION_MINOR)
"""Domain assigned to AEF v0.1 signed tree heads."""

AEF_WITNESS_COSIGN_DOMAIN = AefDomain("witness-cosig", AEF_VERSION_MAJOR, AEF_VERSION_MINOR)
"""Domain assigned to AEF v0.1 witness cosignatures."""

AEF_LEGACY_EVENT_DOMAIN = AefDomain("legacy-event", AEF_VERSION_MAJOR, AEF_VERSION_MINOR)
"""Transitional domain for events that still use the legacy frame serializer."""
