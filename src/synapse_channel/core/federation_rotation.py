# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — rotate a domain's own federation bundle with a grace window
"""Rotate a domain's own federation bundle: fresh expiry, new key material, a grace window.

A federated trust bundle (:mod:`synapse_channel.core.federation`) carries a domain's signing
key ids, certificate pins, and an expiry. Trust material is a lifecycle, not a one-off: keys
are re-issued, certificates roll, and an expiry has to be pushed forward before it lapses.
Doing that by hand-editing the bundle JSON is where an operator drops a still-valid key or
lets a fingerprint change land on peers with no warning.

This module is the *policy* of a rotation, pure and I/O-free — it owns no crypto and mints no
keys (a domain generates its Ed25519 keys and enrols its certificate pins through the tooling
that already manages them, and hands the ids here). :func:`rotate_bundle` takes the current
bundle, the fresh material the operator is introducing, and the material they are retiring, and
returns the next bundle plus a summary of exactly what changed. Its one deliberate behaviour is
the **grace window**: added key material is *unioned* with the existing set rather than
replacing it, so a rotation that introduces a new signing key keeps the old one valid until a
later rotation explicitly retires it — a peer that has not yet re-fetched the bundle keeps
verifying through the whole window. Retiring material that the bundle does not hold, or a
non-positive lifetime, is refused fail-closed so a typo can never silently widen or empty the
policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.federation import FederationPeer

DEFAULT_ROTATION_LIFETIME_DAYS = 90.0
"""Default fresh lifetime a rotation grants, in days, when the operator names none."""

SECONDS_PER_DAY = 86400.0
"""One day of bundle lifetime, in the bundle's epoch seconds."""


class FederationRotationError(SynapseError, ValueError):
    """Raised when a rotation is not well-formed.

    A non-positive lifetime, or retiring a signing key or certificate pin the bundle does not
    hold, is a mistake the rotation refuses rather than apply — an operator typo must never
    silently empty the policy or drop the wrong material.
    """

    code = "federation_rotation"


@dataclass(frozen=True)
class SetChange:
    """How one credential set changed across a rotation: what was added, kept, and retired.

    Attributes
    ----------
    added : tuple[str, ...]
        Material introduced this rotation, sorted; empty when none was added.
    retained : tuple[str, ...]
        Material carried forward from the prior bundle (the grace window), sorted.
    retired : tuple[str, ...]
        Material dropped this rotation, sorted; empty when none was retired.
    """

    added: tuple[str, ...]
    retained: tuple[str, ...]
    retired: tuple[str, ...]


@dataclass(frozen=True)
class RotationSummary:
    """What a rotation changed: the signing-key and pin sets, and the expiry move.

    Attributes
    ----------
    signing_keys : SetChange
        How the accepted signing key ids changed.
    certificate_pins : SetChange
        How the accepted certificate pins changed.
    previous_expires_at : float or None
        The bundle's expiry before the rotation; ``None`` when it never expired.
    expires_at : float
        The fresh expiry the rotation set.
    """

    signing_keys: SetChange
    certificate_pins: SetChange
    previous_expires_at: float | None
    expires_at: float


def _rotate_set(
    current: frozenset[str],
    add: Iterable[str],
    retire: Iterable[str],
    *,
    label: str,
) -> tuple[frozenset[str], SetChange]:
    """Union ``add`` into ``current`` and drop ``retire``, reporting the change.

    Retiring an element ``current`` does not hold is refused — it means the operator named
    the wrong id. An id in both ``add`` and ``retire`` is retired (the explicit drop wins),
    so a mistaken pairing empties rather than silently keeps it.
    """
    add_set = frozenset(add)
    retire_set = frozenset(retire)
    absent = retire_set - current
    if absent:
        names = ", ".join(sorted(absent))
        raise FederationRotationError(f"cannot retire {label}(s) the bundle does not hold: {names}")
    result = (current | add_set) - retire_set
    change = SetChange(
        added=tuple(sorted(add_set - current)),
        retained=tuple(sorted(current - retire_set)),
        retired=tuple(sorted(retire_set)),
    )
    return result, change


def rotate_bundle(
    peer: FederationPeer,
    *,
    now: float,
    lifetime_seconds: float,
    add_signing_keys: Iterable[str] = (),
    add_pins: Iterable[str] = (),
    retire_signing_keys: Iterable[str] = (),
    retire_pins: Iterable[str] = (),
) -> tuple[FederationPeer, RotationSummary]:
    """Return the next bundle and a summary of the rotation, without touching disk.

    The rotation pushes the expiry to ``now + lifetime_seconds``, unions the added signing
    keys and certificate pins with the existing sets (the grace window — old material stays
    valid), and drops exactly the retired material. Every other field (domain, namespaces,
    scope grants, revoked flag) is carried through unchanged. The bundle's own crypto is not
    this function's concern: ``add_*`` values are ids the operator has already generated and
    enrolled elsewhere.

    Parameters
    ----------
    peer : FederationPeer
        The domain's current own bundle.
    now : float
        POSIX time the fresh lifetime is measured from.
    lifetime_seconds : float
        How long the rotated bundle is valid; must be positive.
    add_signing_keys, add_pins : iterable of str, optional
        Fresh signing key ids and certificate pins to introduce (kept alongside the old).
    retire_signing_keys, retire_pins : iterable of str, optional
        Signing key ids and certificate pins to drop; each must be present in the bundle.

    Returns
    -------
    tuple[FederationPeer, RotationSummary]
        The rotated bundle and the record of what changed.

    Raises
    ------
    FederationRotationError
        If ``lifetime_seconds`` is not positive, or a retired id is not in the bundle.
    """
    if lifetime_seconds <= 0:
        raise FederationRotationError("rotation lifetime must be a positive number of seconds")
    new_keys, key_change = _rotate_set(
        peer.signing_key_ids, add_signing_keys, retire_signing_keys, label="signing key"
    )
    new_pins, pin_change = _rotate_set(
        peer.certificate_pins, add_pins, retire_pins, label="certificate pin"
    )
    expires_at = now + lifetime_seconds
    rotated = replace(
        peer,
        signing_key_ids=new_keys,
        certificate_pins=new_pins,
        expires_at=expires_at,
    )
    summary = RotationSummary(
        signing_keys=key_change,
        certificate_pins=pin_change,
        previous_expires_at=peer.expires_at,
        expires_at=expires_at,
    )
    return rotated, summary
