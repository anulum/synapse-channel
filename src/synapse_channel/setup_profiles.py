# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — package-owned machine setup profiles
"""Immutable setup profiles used by the machine-readable setup CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from synapse_channel.setup_contract import SETUP_SCHEMA_VERSION, SetupRequirement

SetupProfileId = Literal["local-single-user"]


@dataclass(frozen=True, slots=True)
class SetupProfile:
    """One versioned, package-owned setup target."""

    profile_id: SetupProfileId
    version: int
    summary: str
    scope: str
    requirements: tuple[SetupRequirement, ...]


_LOCAL_SINGLE_USER = SetupProfile(
    profile_id="local-single-user",
    version=1,
    summary="One user, one loopback Synapse hub, and one durable waiter for the resolved identity.",
    scope=(
        "Discovery plus explicitly authorized package-owned Linux systemd-user service effects; "
        "package, interpreter, platform, and identity changes remain unsupported."
    ),
    requirements=(
        SetupRequirement(
            "package",
            "The synapse-channel package is importable and versioned.",
            True,
            "installed Python package metadata",
            "Install or upgrade synapse-channel in the environment used by the agent.",
        ),
        SetupRequirement(
            "python",
            "Python satisfies the package's supported interpreter floor.",
            True,
            "running Python interpreter",
            "Use Python 3.10 or newer.",
        ),
        SetupRequirement(
            "platform",
            "The operating system is one of the documented host families.",
            True,
            "Python platform API",
            "Use Linux, macOS, or Windows, or provide a compatible host adapter.",
        ),
        SetupRequirement(
            "executable",
            "The synapse console entry point is discoverable on PATH.",
            True,
            "executable search path",
            "Activate the package environment or add its scripts directory to PATH.",
        ),
        SetupRequirement(
            "identity",
            "A deterministic project and agent identity can be resolved.",
            True,
            "Synapse identity resolver",
            "Set SYN_PROJECT and SYN_IDENTITY or configure the repository identity.",
        ),
        SetupRequirement(
            "hub",
            "The selected loopback hub answers a live roster probe.",
            True,
            "authenticated read-only hub probe",
            "Start the local hub or correct SYNAPSE_URI; use SYNAPSE_TOKEN for secured hubs.",
        ),
        SetupRequirement(
            "waiter",
            "The resolved identity has a live durable -rx waiter.",
            True,
            "hub roster snapshot",
            "Arm the identity waiter or start its managed user service.",
        ),
        SetupRequirement(
            "service_manager",
            "A supported persistent service manager is available when persistence is desired.",
            False,
            "host executable search path",
            "Use systemd user services on Linux or run hub and waiter explicitly in foreground.",
        ),
    ),
)

_PROFILES: dict[SetupProfileId, SetupProfile] = {
    _LOCAL_SINGLE_USER.profile_id: _LOCAL_SINGLE_USER,
}


def available_setup_profiles() -> tuple[SetupProfileId, ...]:
    """Return setup profile identifiers in stable display order."""
    return tuple(_PROFILES)


def get_setup_profile(profile: str) -> SetupProfile | None:
    """Return a registered profile, or ``None`` for an unknown identifier."""
    if profile not in _PROFILES:
        return None
    return _PROFILES[profile]


def build_setup_spec(profile: SetupProfile) -> dict[str, object]:
    """Build the deterministic machine-readable specification for ``profile``."""
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "document_kind": "spec",
        "profile": profile.profile_id,
        "profile_version": profile.version,
        "summary": profile.summary,
        "scope": profile.scope,
        "read_only": True,
        "supported_operations": ["spec", "inspect", "plan", "authorize", "apply"],
        "requirements": [requirement.as_dict() for requirement in profile.requirements],
    }
