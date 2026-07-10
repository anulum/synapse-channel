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
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from synapse_channel.core.federation import FederationBundle, FederationPeer, ScopeGrant

STORE_VERSION = 1
"""On-disk schema version of the federation store file."""
_DIRECTORY_FSYNC_SUPPORTED = os.name == "posix"


class FederationStoreError(ValueError):
    """Raised when a federation bundle or store is malformed or inaccessible."""


def _finite_float(field: str, value: Any) -> float:
    """Return ``value`` as a finite float, or raise :class:`FederationStoreError`.

    Numeric fields arrive from an out-of-band peer bundle, so a hostile or corrupt
    value must fail the parser's contract (``FederationStoreError``) like every other
    malformed field — not escape as a raw ``TypeError``/``ValueError`` that callers,
    which catch only ``FederationStoreError``, would let crash the hub or an import.
    A non-finite ``nan`` is rejected too: it would defeat the ``now >= expires_at``
    expiry comparison and leave a peering that never expires. ``OverflowError`` is
    caught because a JSON integer too large for a double raises it on conversion.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"federation bundle field {field!r} must be a number"
        raise FederationStoreError(msg)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        msg = f"federation bundle field {field!r} must be a number"
        raise FederationStoreError(msg) from exc
    if not math.isfinite(number):
        msg = f"federation bundle field {field!r} must be a finite number"
        raise FederationStoreError(msg)
    return number


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
    if not isinstance(raw, list):
        msg = f"federation bundle field {key!r} must be a list"
        raise FederationStoreError(msg)
    if not all(isinstance(item, str) for item in raw):
        msg = f"federation bundle field {key!r} must contain only strings"
        raise FederationStoreError(msg)
    return [item.strip() for item in raw if item.strip()]


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

    Parameters
    ----------
    data : Mapping
        The peer sub-mapping of a federation bundle.

    Returns
    -------
    FederationPeer
        The parsed peer, with empty defaults for every omitted optional field.

    Raises
    ------
    FederationStoreError
        If ``data`` is not a mapping, ``domain_id`` is missing or is not a
        non-empty string, or ``scope_grants`` is not a list of mappings.
    """
    if not isinstance(data, Mapping):
        msg = "federation bundle must be a mapping"
        raise FederationStoreError(msg)
    domain_raw = data.get("domain_id", "")
    if not isinstance(domain_raw, str):
        msg = "federation bundle must name a non-empty string domain_id"
        raise FederationStoreError(msg)
    domain_id = domain_raw.strip()
    if not domain_id:
        msg = "federation bundle must name a non-empty domain_id"
        raise FederationStoreError(msg)
    grants_raw = data.get("scope_grants", [])
    if not isinstance(grants_raw, list):
        msg = "federation bundle field 'scope_grants' must be a list"
        raise FederationStoreError(msg)
    scope_grants: list[ScopeGrant] = []
    for grant in grants_raw:
        if not isinstance(grant, Mapping):
            msg = "each scope grant must be a mapping with 'verb' and 'namespace'"
            raise FederationStoreError(msg)
        verb_raw = grant.get("verb", "")
        namespace_raw = grant.get("namespace", "")
        if not isinstance(verb_raw, str) or not isinstance(namespace_raw, str):
            msg = "scope grant 'verb' and 'namespace' must be strings"
            raise FederationStoreError(msg)
        verb = verb_raw.strip()
        namespace = namespace_raw.strip()
        if verb and namespace:
            scope_grants.append(ScopeGrant(verb=verb, namespace=namespace))
    expires_raw = data.get("expires_at")
    revoked = data.get("revoked", False)
    if not isinstance(revoked, bool):
        msg = "federation bundle field 'revoked' must be a boolean"
        raise FederationStoreError(msg)
    return FederationPeer(
        domain_id=domain_id,
        namespaces=frozenset(_str_list(data, "namespaces")),
        certificate_pins=frozenset(_str_list(data, "certificate_pins")),
        signing_key_ids=frozenset(_str_list(data, "signing_key_ids")),
        scope_grants=tuple(scope_grants),
        expires_at=None if expires_raw is None else _finite_float("expires_at", expires_raw),
        revoked=revoked,
    )


def merge_record(
    records: Mapping[str, FederationRecord], record: FederationRecord
) -> dict[str, FederationRecord]:
    """Return ``records`` with ``record`` added or replacing its domain's prior peering."""
    merged = dict(records)
    merged[record.peer.domain_id] = record
    return merged


def load_store(path: str | Path) -> dict[str, FederationRecord]:
    """Load and validate the federation store, keyed by peer domain id.

    Every field is checked deny-by-default: a store that is unreadable,
    malformed, of an unknown version, or internally inconsistent raises rather
    than loading a partial or ambiguous policy, so a corrupt file can never
    silently authorise or drop a peering. An absent file is the empty store.

    Parameters
    ----------
    path : str or pathlib.Path
        The federation store file, as written by :func:`save_store`.

    Returns
    -------
    dict of str to FederationRecord
        The stored peerings keyed by ``peer.domain_id``; empty when the file
        does not exist.

    Raises
    ------
    FederationStoreError
        If the file cannot be read (an ``OSError`` other than a missing file),
        is not valid JSON, is not a mapping, declares an unsupported
        ``version``, lacks a ``records`` list, carries non-mapping or
        non-string provenance, or names a duplicate ``domain_id``.
    """
    file = Path(path)
    try:
        raw = file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        msg = f"cannot read federation store {file}: {exc}"
        raise FederationStoreError(msg) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = f"federation store is not valid JSON: {exc}"
        raise FederationStoreError(msg) from exc
    if not isinstance(data, Mapping):
        msg = "federation store must be a mapping with a 'records' list"
        raise FederationStoreError(msg)
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != STORE_VERSION:
        msg = f"unsupported federation store version: {version!r}"
        raise FederationStoreError(msg)
    if not isinstance(data.get("records"), list):
        msg = "federation store must be a mapping with a 'records' list"
        raise FederationStoreError(msg)
    records: dict[str, FederationRecord] = {}
    for entry in data["records"]:
        peer = peer_from_dict(entry)
        prov = entry.get("provenance", {})
        if not isinstance(prov, Mapping):
            msg = "federation record 'provenance' must be a mapping"
            raise FederationStoreError(msg)
        source = prov.get("source", "")
        confirmed_by = prov.get("confirmed_by", "")
        if not isinstance(source, str) or not isinstance(confirmed_by, str):
            msg = "federation provenance source and confirmed_by must be strings"
            raise FederationStoreError(msg)
        if peer.domain_id in records:
            msg = f"duplicate federation domain_id: {peer.domain_id!r}"
            raise FederationStoreError(msg)
        records[peer.domain_id] = FederationRecord(
            peer=peer,
            provenance=PeerProvenance(
                source=source,
                imported_at=_finite_float("provenance.imported_at", prov.get("imported_at", 0.0)),
                confirmed_by=confirmed_by,
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

    Raises
    ------
    FederationStoreError
        Propagated from :func:`load_store` when the store exists but is malformed.
    """
    records = load_store(path)
    return FederationBundle(record.peer for record in records.values())


def _fsync_directory(directory: Path) -> None:
    """Persist a completed rename in ``directory`` on POSIX filesystems."""
    if not _DIRECTORY_FSYNC_SUPPORTED:
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_store(path: str | Path, records: Iterable[FederationRecord]) -> None:
    """Atomically write an owner-only federation store sorted by domain id.

    The replacement is ordered after an ``fsync`` of the owner-only sibling
    temporary file, then the parent directory is synced so a power loss cannot
    expose a partial policy or lose the completed rename.

    Raises
    ------
    FederationStoreError
        If the destination directory or durable replacement cannot be written.
    """
    ordered = sorted(records, key=lambda record: record.peer.domain_id)
    payload = {"version": STORE_VERSION, "records": [record.to_dict() for record in ordered]}
    file = Path(path).expanduser()
    try:
        encoded = json.dumps(payload, indent=2, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        msg = f"cannot encode federation store {file}: {exc}"
        raise FederationStoreError(msg) from exc
    temporary: Path | None = None
    descriptor = -1
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=file.parent,
            prefix=f".{file.name}.",
            suffix=".tmp",
        )
        temporary = Path(temp_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, file)
        temporary = None
        _fsync_directory(file.parent)
    except OSError as exc:
        msg = f"cannot write federation store {file}: {exc}"
        raise FederationStoreError(msg) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
