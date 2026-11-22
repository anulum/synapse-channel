# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — per-name session attributes of a live connection
"""Roles, wake capability, protocol version and delivery session of one name."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from synapse_channel.core.delivery_modes import MODES, QUALITIES, DeliveryRefusal
from synapse_channel.core.wake_capability import WAKE_UNKNOWN, normalize_wake_capability


@dataclass(frozen=True)
class DeliverySession:
    """Capabilities bound to one live, named socket and opaque incarnation."""

    incarnation: str
    capabilities: dict[str, str]


class AgentSessionAttributes:
    """Own the four per-name records a live socket advertises.

    The maps themselves are the objects callers already hold. ``clear`` removes
    one name from all four. The registry decides when a socket is still the
    owner, and only then calls ``clear``.
    """

    def __init__(self) -> None:
        self.roles: dict[str, tuple[str, ...]] = {}
        self.wake_capabilities: dict[str, str] = {}
        self.delivery_sessions: dict[str, DeliverySession] = {}
        self.protocol_versions: dict[str, int] = {}

    def clear(self, name: str) -> None:
        """Drop every per-name record of ``name``."""
        self.roles.pop(name, None)
        self.wake_capabilities.pop(name, None)
        self.delivery_sessions.pop(name, None)
        self.protocol_versions.pop(name, None)

    def set_roles(self, name: str, roles: tuple[str, ...]) -> None:
        """Bind the roles ``name`` answers to, or clear them when empty."""
        if roles:
            self.roles[name] = roles
        else:
            self.roles.pop(name, None)

    def roles_of(self, name: str) -> tuple[str, ...]:
        """Return the roles ``name`` currently answers to."""
        return self.roles.get(name, ())

    def set_wake_capability(self, name: str, capability: str) -> None:
        """Bind ``name`` to a declared receiver wake capability."""
        normalized = normalize_wake_capability(capability)
        if normalized == WAKE_UNKNOWN:
            self.wake_capabilities.pop(name, None)
        else:
            self.wake_capabilities[name] = normalized

    def wake_capability_of(self, name: str) -> str:
        """Return ``name``'s declared wake capability, or ``unknown``."""
        return self.wake_capabilities.get(name, WAKE_UNKNOWN)

    def bind_protocol_version(self, name: str, version: int | None) -> int:
        """Store the advertised wire version, treating a missing one as legacy."""
        effective = version if version is not None and version >= 1 else 1
        self.protocol_versions[name] = effective
        return effective

    def protocol_version_of(self, name: str) -> int:
        """Return the bound peer version, or legacy version one when absent."""
        return self.protocol_versions.get(name, 1)

    def bind_delivery_session(
        self, name: str, *, token: object, capabilities: object, hub_id: str
    ) -> DeliverySession:
        """Bind a verified version-three session. The token itself is not stored."""
        if (
            not isinstance(token, str)
            or len(token) != 64
            or any(char not in "0123456789abcdef" for char in token)
        ):
            raise DeliveryRefusal("invalid_shape", "delivery session token is malformed")
        if not isinstance(capabilities, dict) or len(capabilities) > len(MODES):
            raise DeliveryRefusal("invalid_shape", "delivery capabilities are malformed")
        if any(
            not isinstance(mode, str)
            or not isinstance(quality, str)
            or mode not in MODES
            or quality not in QUALITIES
            for mode, quality in capabilities.items()
        ):
            raise DeliveryRefusal("invalid_shape", "delivery capability is unsupported")
        digest = hashlib.sha256(f"{hub_id}\0{name}\0{token}".encode()).hexdigest()
        session = DeliverySession(digest, dict(capabilities))
        self.delivery_sessions[name] = session
        return session

    def delivery_session(self, name: str) -> DeliverySession | None:
        """Return a copy of the current delivery contract, or ``None``."""
        session = self.delivery_sessions.get(name)
        if session is None:
            return None
        return DeliverySession(session.incarnation, dict(session.capabilities))
