# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provenance-keeping persistence for imported federation bundles
"""Provenance-keeping persistence for operator-confirmed federation bundles.

Federation trust roots move between domains **out-of-band**: an operator receives a
peer domain's bundle through a trusted channel and imports it explicitly
(`docs/federated-trust-model.md`). Synapse is not a certificate authority and never
auto-discovers trust, so every imported peering must be auditable back to a human
decision — *who* provided the bundle, *when* it was imported, and *which* operator
confirmed it.

This module is that audit-keeping persistence: it serialises a
:class:`~synapse_channel.core.federation.FederationPeer` to and from JSON, pairs it with
a :class:`PeerProvenance`, and reads and writes a local store file keyed by domain id.
The serialisation and merge logic are pure; only :func:`load_store` and
:func:`save_store` touch the filesystem, so the policy is testable without one. Importing
is deny-by-default: a bundle that names no namespaces, keys, or pins grants nothing until
the operator adds them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant

STORE_VERSION = 1
"""On-disk schema version of the federation store file."""


class FederationStoreError(ValueError):
    """Raised when a federation bundle or store file is malformed."""


@dataclass(frozen=True)
class PeerProvenance:
    """Who provided an imported peering, when, and which operator confirmed it.

    Attributes
    ----------
    source : str
        Where the bundle came from (a signed file, a ticket, a key-signing exchange).
    imported_at : float
        Time the peering was imported.
    confirmed_by : str
        The operator who confirmed the import — the human decision it is auditable to.
    """

    source: str
    imported_at: float
    confirmed_by: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping."""
        return {
            "source": self.source,
            "imported_at": self.imported_at,
            "confirmed_by": self.confirmed_by,
        }


@dataclass(frozen=True)
class FederationRecord:
    """An imported peer domain paired with the provenance of its import."""

    peer: FederationPeer
    provenance: PeerProvenance

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping of the peer and its provenance."""
        return {**peer_to_dict(self.peer), "provenance": self.provenance.to_dict()}


def _str_list(data: Mapping[str, Any], key: str) -> list[str]:
    """Return a cleaned list of strings under ``key``, or empty when absent."""
    raw = data.get(key, [])
    if not isinstance(raw, (list, tuple)):
        msg = f"federation bundle field {key!r} must be a list"
        raise FederationStoreError(msg)
    return [str(item).strip() for item in raw if str(item).strip()]


def peer_to_dict(peer: FederationPeer) -> dict[str, Any]:
    """Serialise a :class:`FederationPeer` to a JSON-compatible mapping."""
    return {
        "domain_id": peer.domain_id,
        "namespaces": sorted(peer.namespaces),
        "certificate_pins": sorted(peer.certificate_pins),
        "signing_key_ids": sorted(peer.signing_key_ids),
        "scope_grants": [{"verb": g.verb, "namespace": g.namespace} for g in peer.scope_grants],
        "expires_at": peer.expires_at,
        "revoked": peer.revoked,
    }


def peer_from_dict(data: Mapping[str, Any]) -> FederationPeer:
    """Parse a :class:`FederationPeer` from a bundle mapping, deny-by-default on omissions.

    Only ``domain_id`` is required; everything else defaults to empty, so a bundle that
    omits namespaces, keys, or pins grants nothing until the operator adds them.
    """
    if not isinstance(data, Mapping):
        msg = "federation bundle must be a mapping"
        raise FederationStoreError(msg)
    domain_id = str(data.get("domain_id", "")).strip()
    if not domain_id:
        msg = "federation bundle must name a non-empty domain_id"
        raise FederationStoreError(msg)
    grants_raw = data.get("scope_grants", [])
    if not isinstance(grants_raw, (list, tuple)):
        msg = "federation bundle field 'scope_grants' must be a list"
        raise FederationStoreError(msg)
    scope_grants: list[ScopeGrant] = []
    for grant in grants_raw:
        if not isinstance(grant, Mapping):
            msg = "each scope grant must be a mapping with 'verb' and 'namespace'"
            raise FederationStoreError(msg)
        verb = str(grant.get("verb", "")).strip()
        namespace = str(grant.get("namespace", "")).strip()
        if verb and namespace:
            scope_grants.append(ScopeGrant(verb=verb, namespace=namespace))
    expires_raw = data.get("expires_at")
    return FederationPeer(
        domain_id=domain_id,
        namespaces=frozenset(_str_list(data, "namespaces")),
        certificate_pins=frozenset(_str_list(data, "certificate_pins")),
        signing_key_ids=frozenset(_str_list(data, "signing_key_ids")),
        scope_grants=tuple(scope_grants),
        expires_at=None if expires_raw is None else float(expires_raw),
        revoked=bool(data.get("revoked", False)),
    )


def merge_record(
    records: Mapping[str, FederationRecord], record: FederationRecord
) -> dict[str, FederationRecord]:
    """Return ``records`` with ``record`` added or replacing its domain's prior peering."""
    merged = dict(records)
    merged[record.peer.domain_id] = record
    return merged


def load_store(path: str | Path) -> dict[str, FederationRecord]:
    """Load the federation store keyed by domain id; an absent file is an empty store."""
    file = Path(path)
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"federation store is not valid JSON: {exc}"
        raise FederationStoreError(msg) from exc
    if not isinstance(data, Mapping) or not isinstance(data.get("records"), list):
        msg = "federation store must be a mapping with a 'records' list"
        raise FederationStoreError(msg)
    records: dict[str, FederationRecord] = {}
    for entry in data["records"]:
        peer = peer_from_dict(entry)
        prov = entry.get("provenance", {})
        if not isinstance(prov, Mapping):
            msg = "federation record 'provenance' must be a mapping"
            raise FederationStoreError(msg)
        records[peer.domain_id] = FederationRecord(
            peer=peer,
            provenance=PeerProvenance(
                source=str(prov.get("source", "")),
                imported_at=float(prov.get("imported_at", 0.0)),
                confirmed_by=str(prov.get("confirmed_by", "")),
            ),
        )
    return records


def bundle_from_store(path: str | Path) -> FederationBundle:
    """Build a :class:`FederationBundle` from the operator-confirmed peerings at ``path``.

    The bundle is the policy a hub composes into its live frame authorisation; this turns
    the persisted, audited peerings (imported with ``synapse federation import``) into that
    runtime policy. An absent store yields an empty bundle that peers nothing, so federation
    stays a no-op until a peering is imported. Every record is loaded, including revoked or
    expired ones: revocation and expiry are honoured at authorisation time and re-sampled
    per frame, so a revoked or expired peering is present but authorises nothing.

    Parameters
    ----------
    path : str or pathlib.Path
        The federation store file, as written by :func:`save_store`.

    Returns
    -------
    FederationBundle
        The deny-by-default policy over the stored peer domains.
    """
    records = load_store(path)
    return FederationBundle(record.peer for record in records.values())


def save_store(path: str | Path, records: Iterable[FederationRecord]) -> None:
    """Write the federation store file, records sorted by domain id for a stable diff."""
    ordered = sorted(records, key=lambda record: record.peer.domain_id)
    payload = {"version": STORE_VERSION, "records": [record.to_dict() for record in ordered]}
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
