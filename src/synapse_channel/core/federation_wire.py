# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec and fingerprints for the federation-bundle exchange
"""Wire codec and fingerprints for the federation-bundle exchange.

Peering two Synapse domains needs each operator to hold the other domain's bundle
material — its ``domain_id``, signing key ids, and certificate pins. Until now that
material moved as an out-of-band file copy; the exchange lane replaces the *transport*
with a first-class pull over the hub's websocket surface while keeping the *trust*
decision exactly where the design doc puts it (`docs/federated-trust-model.md`): an
operator imports a fetched bundle only after comparing fingerprints out-of-band, the
SSH-known-hosts ceremony. There is no trust-on-first-use — a fetched bundle is untrusted
bytes until a human confirms it.

This module is the one place that names the shapes of that exchange, so the serving half
(:mod:`synapse_channel.core.handlers.federation_offer`) and the fetching half
(:mod:`synapse_channel.core.federation_fetch`) agree on the format without importing each
other. The offered material *is* the peer-bundle mapping the existing import ceremony
already reads (:func:`~synapse_channel.core.federation_store.peer_from_dict`), so a
fetched bundle feeds ``synapse federation import`` unchanged.

The codec is **pure** — no network, no clock, no hub dependency — and decoding is
defensive because an offer arrives from another host: a malformed body raises
:class:`FederationWireError` rather than yielding a half-built peering, so the fetching
half fails the pull and imports nothing.

The **fingerprint** is the ceremony's comparison object. It is computed over the whole
canonical bundle (every field, normalised and sorted), not just the key material, so an
in-path alteration of *any* policy content — an added namespace or scope grant as much as
a swapped signing key — changes the fingerprint both operators read to each other. Both
halves of the ceremony call the same functions here, so the offering and the importing
operator are guaranteed to be comparing the same rendering.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from synapse_channel.core.federation import FederationPeer
from synapse_channel.core.federation_store import (
    FederationStoreError,
    peer_from_dict,
    peer_to_dict,
)


class FederationWireError(ValueError):
    """Raised when a federation-offer wire body is malformed.

    Carries the fail-closed contract: a fetching operator that catches this imports
    nothing — corrupt or hostile offer material can never become a peering.
    """


def encode_federation_offer(peer: FederationPeer) -> dict[str, Any]:
    """Return the JSON-object body for one offered peer-domain bundle.

    Parameters
    ----------
    peer : FederationPeer
        The offering domain's own bundle material.

    Returns
    -------
    dict[str, Any]
        The peer-bundle mapping, exactly the format ``synapse federation import`` reads.
    """
    return peer_to_dict(peer)


def decode_federation_offer(raw: object) -> FederationPeer:
    """Reconstruct an offered peer-domain bundle from a decoded JSON object.

    Deny-by-default omissions are preserved: only ``domain_id`` is required, and a bundle
    that omits namespaces, keys, or pins grants nothing when later imported.

    Parameters
    ----------
    raw : object
        The decoded offer body; expected to be a mapping.

    Returns
    -------
    FederationPeer
        The offered bundle material, still untrusted until the operator confirms it.

    Raises
    ------
    FederationWireError
        If the body is not a mapping, names no domain, or carries a malformed field.
    """
    if not isinstance(raw, Mapping):
        msg = "federation offer body must be a JSON object"
        raise FederationWireError(msg)
    try:
        return peer_from_dict(raw)
    except (FederationStoreError, TypeError, ValueError) as exc:
        msg = f"malformed federation offer: {exc}"
        raise FederationWireError(msg) from exc


def bundle_fingerprint(peer: FederationPeer) -> str:
    """Return the ``sha256:<hex>`` fingerprint of the whole canonical bundle.

    The digest is taken over the canonical JSON of every bundle field (sorted keys,
    compact separators, collections normalised by the serialiser), so two operators hold
    the same fingerprint exactly when they hold the same policy content. Comparing this
    value out-of-band — reading it over a call, not over the fetched channel — is the
    trust decision of the exchange ceremony.

    Parameters
    ----------
    peer : FederationPeer
        The bundle material to fingerprint.

    Returns
    -------
    str
        ``sha256:`` followed by the lowercase hex digest.
    """
    canonical = json.dumps(peer_to_dict(peer), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_offer_fingerprints(peer: FederationPeer) -> str:
    """Return the operator-facing fingerprint block for one offered bundle.

    Both ceremony halves print this same rendering — ``synapse federation offer`` on the
    offering side and ``synapse federation fetch`` on the importing side — so the two
    operators comparing values out-of-band are reading identical lines. The individual
    key ids and pins aid diagnosis when the bundle fingerprints differ.

    Parameters
    ----------
    peer : FederationPeer
        The bundle material to render.

    Returns
    -------
    str
        A multi-line block: domain, signing key ids, certificate pins, namespaces,
        scope-grant count, expiry, and the whole-bundle fingerprint.
    """
    lines = [f"domain:             {peer.domain_id}"]
    lines.extend(_labelled_block("signing key ids:", sorted(peer.signing_key_ids)))
    lines.extend(_labelled_block("certificate pins:", sorted(peer.certificate_pins)))
    namespaces = ", ".join(sorted(peer.namespaces)) or "(none)"
    lines.append(f"namespaces:         {namespaces}")
    lines.append(f"scope grants:       {len(peer.scope_grants)}")
    lines.append(f"expires:            {render_expiry(peer.expires_at)}")
    if peer.revoked:
        lines.append("revoked:            yes")
    lines.append(f"bundle fingerprint: {bundle_fingerprint(peer)}")
    return "\n".join(lines)


def _labelled_block(label: str, values: list[str]) -> list[str]:
    """Return ``label`` with its values one per line, aligned under the first."""
    if not values:
        return [f"{label:<20}(none)"]
    first, *rest = values
    return [f"{label:<20}{first}", *(f"{'':<20}{value}" for value in rest)]


def render_expiry(expires_at: float | None) -> str:
    """Return a deterministic UTC rendering of a bundle expiry, or ``never``.

    Shared by the ceremony fingerprint block and the rotation summary so an expiry reads the
    same wherever it is shown. ``None`` renders as ``never``; a timestamp renders as an
    ISO-8601 UTC instant with a ``Z`` suffix.
    """
    if expires_at is None:
        return "never"
    stamp = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")
