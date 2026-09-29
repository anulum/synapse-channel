# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multi-seat trust hub profile
"""Team-secure hub profile for multi-seat local fleets.

``synapse hub --team-secure`` is the multi-agent *trust* preset: it fails closed
unless connection identity is proven, role claims are granted, directed
messages are audience-routed, and every lease mutation names its fencing epoch.
It is deliberately lighter than ``--paranoid`` (which also demands TLS, ACL
enforcement, and per-message HMAC for exposed
production binds). Pair both when a multi-seat hub is also network-exposed.

The profile mutates the parsed hub namespace in place so existing per-flag plumbing
and tests keep working; operators may still pass the individual flags explicitly.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from synapse_channel.core.errors import SynapseError


class TeamSecureModeError(SynapseError, ValueError):
    """Raised when a hub cannot satisfy team-secure requirements."""

    code = "team_secure_mode"


@dataclass(frozen=True)
class TeamSecureHubReport:
    """Effective team-secure hub posture.

    Attributes
    ----------
    enforced : tuple[str, ...]
        Runtime settings the profile required or normalised before startup.
    recommended : tuple[str, ...]
        Hardening steps that strengthen the profile but are not mandatory for a
        loopback multi-seat fleet (so the switch stays usable without TLS or a
        full ACL policy).
    """

    enforced: tuple[str, ...]
    recommended: tuple[str, ...] = ()

    def stderr_lines(self) -> tuple[str, ...]:
        """Return human-readable report lines for the operator."""
        lines = [f"team-secure mode enforced: {', '.join(self.enforced)}"]
        if self.recommended:
            lines.append(f"team-secure mode recommended next: {', '.join(self.recommended)}")
        return tuple(lines)


def apply_team_secure_hub_profile(args: argparse.Namespace) -> TeamSecureHubReport | None:
    """Validate and normalise ``synapse hub --team-secure`` settings.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed hub arguments. The function enables the trust gates and refuses
        startup when required material is missing.

    Returns
    -------
    TeamSecureHubReport or None
        Report for operator output when the profile is enabled; ``None`` when
        the switch is off.

    Raises
    ------
    TeamSecureModeError
        If a required setting is absent.
    """
    if not bool(getattr(args, "team_secure", False)):
        return None

    if not getattr(args, "token", None):
        raise TeamSecureModeError(
            "team-secure mode requires --token or --token-file "
            "(identity and role grants are only as strong as the connect gate)"
        )
    identity_trust = str(getattr(args, "identity_trust", "") or "").strip()
    if not identity_trust:
        raise TeamSecureModeError(
            "team-secure mode requires --identity-trust "
            "(Ed25519 trust bundle for connection identity binding)"
        )
    role_grants = str(getattr(args, "role_grants", "") or "").strip()
    if not role_grants:
        raise TeamSecureModeError(
            "team-secure mode requires --role-grants "
            "(deny-by-default store for which identities may claim which roles)"
        )

    # Force the trust gates even if the operator omitted the individual switches.
    args.require_identity_binding = True
    args.require_role_claim = True
    args.private_directed_messages = True
    args.require_fencing_epoch = True

    enforced = [
        "hub token required",
        "identity binding required (--identity-trust + --require-identity-binding)",
        "role-claim grants required (--role-grants + --require-role-claim)",
        "private directed messages required",
        "lease mutations fenced by epoch (--require-fencing-epoch)",
    ]
    floors = _record_sequence_floors(args)
    if floors:
        enforced.append(floors)
    recommended: list[str] = []
    if not getattr(args, "message_auth_key", None) or not bool(
        getattr(args, "require_message_auth", False)
    ):
        recommended.append(
            "--message-auth-key and --require-message-auth "
            "(cryptographically bind the sender beyond the connect token)"
        )
    if (
        not bool(getattr(args, "require_acl", False))
        or not str(getattr(args, "acl_policy", "") or "").strip()
    ):
        recommended.append(
            "--require-acl with --acl-policy (authorise mutating verbs beyond connect + identity)"
        )
    if not getattr(args, "tls_certfile", None) or not getattr(args, "tls_keyfile", None):
        recommended.append(
            "--tls-certfile/--tls-keyfile or --paranoid "
            "(native WSS when the hub is off-loopback or multi-host)"
        )
    if not getattr(args, "db", None):
        recommended.append("--db (durable event log so leases and receipts survive restart)")

    return TeamSecureHubReport(
        enforced=tuple(enforced),
        recommended=tuple(recommended),
    )


def _record_sequence_floors(args: argparse.Namespace) -> str:
    """Turn durable sequence floors on in ``compat`` mode where they can run (K4-WF10).

    Floors need per-message authentication and a durable replay ledger (``--db`` or
    ``--message-auth-replay-db``); without both they stay off. An unset (``off``) mode
    becomes ``compat``, which records each key's high-water sequence without refusing
    anything. ``strict`` is left to the operator: the shipped client numbers frames
    from 1 in every process, so a strict floor refuses a restarted agent's first
    frames (reproduced) until clients keep their sequence across restarts.

    Returns
    -------
    str
        The report line, or ``""`` when floors cannot run.
    """
    if not bool(getattr(args, "require_message_auth", False)):
        return ""
    if not (getattr(args, "db", None) or getattr(args, "message_auth_replay_db", None)):
        return ""
    mode = str(getattr(args, "message_auth_sequence_floor_mode", "off") or "off")
    if mode == "off":
        mode = "compat"
        args.message_auth_sequence_floor_mode = mode
    return f"durable message-auth sequence floors recorded ({mode})"
