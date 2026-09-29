# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — refuse an exposed or multi-seat hub without provisioned identity
"""Refuse a hub that others can reach, or that declares several seats, without identity.

A connect token says only that a client may join; it does not say which name the
client may register as. On a single-owner loopback hub that is acceptable, and the
hub states it with the open-loopback notice. Once the hub binds off loopback, or the
operator declares a multi-seat profile, any holder of the shared token could register
as any seat, so the hub requires provisioned identity: ``--identity-trust`` with
``--require-identity-binding``, so each name must be proven by a key enrolled for it.

Proportionate like the at-rest guard (:mod:`synapse_channel.core.at_rest_guard`): a
loopback hub with no multi-seat declaration is unaffected. The operator satisfies
the guard by enrolling each seat's key (``synapse identity machine-key --trust`` or
``synapse identity keygen --trust``) and passing both flags, or accepts the risk
explicitly with ``--insecure-unbound-identity``.
"""

from __future__ import annotations

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.hub_exposure import is_loopback_host


class UnboundIdentityError(SynapseError, RuntimeError):
    """Raised when an exposed or multi-seat hub would start without provisioned identity."""

    code = "unbound_identity"


_REMEDY = (
    "enrol each seat's key (synapse identity machine-key --sender NAME --trust FILE, or "
    "synapse identity keygen --trust FILE) and pass --identity-trust FILE with "
    "--require-identity-binding"
)


def unbound_identity_problem(
    host: str,
    *,
    declared_multi_seat: bool,
    identity_bound: bool,
) -> str | None:
    """Return why a hub on ``host`` needs provisioned identity, or ``None``.

    Parameters
    ----------
    host : str
        The bind host.
    declared_multi_seat : bool
        Whether the operator declared a multi-seat profile (``--expect-multi-seat``,
        an exposed bridge, role or identity material, or private directed routing).
    identity_bound : bool
        Whether registrations must prove their name against an identity trust bundle.

    Returns
    -------
    str or None
        The problem sentence, or ``None`` when identity is bound or the hub is a
        single-owner loopback hub.
    """
    if identity_bound:
        return None
    if not is_loopback_host(host):
        return (
            f"binds off-loopback host {host!r} without provisioned identity, so any holder "
            f"of the shared token can register as any seat; {_REMEDY}"
        )
    if declared_multi_seat:
        return (
            "declares a multi-seat profile without provisioned identity, so any client "
            f"that can connect can register as any seat; {_REMEDY}"
        )
    return None


def refusal_message(problem: str) -> str:
    """Return the start-up refusal for ``problem``, naming the explicit downgrade."""
    return (
        f"Refusing to start: Synapse Hub {problem}, or pass --insecure-unbound-identity "
        "to start anyway (not recommended)."
    )
