# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict operable multi-hub serving-policy configuration
"""Load an operator-authored multi-hub serving policy for ``synapse hub``.

The serving gate itself is deliberately pure.  This module is its supported
configuration boundary: a small versioned document names the already-audited
federation store, the client CA that asks TLS peers for certificates, and the
exact sender identities allowed to request multi-hub operations.  Trust is not
duplicated in the document.  Certificate pins, signing keys, namespace scope,
expiry, and revocation remain authoritative in the federation store and are
composed into the matching mutual-TLS bundle.

Every ambiguity fails closed.  Unknown or duplicate JSON fields are refused,
paths resolve relative to the policy document, grants must be unique, and a
grant cannot name material absent from the federation store.  Revoked or
expired peerings still load so their live authorisation result remains the
appropriate deny reason; configuration never turns revocation into absence.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.federation import FederationBundle
from synapse_channel.core.federation_store import FederationStoreError, load_store
from synapse_channel.core.multihub_serving import (
    MultiHubServingGrant,
    MultiHubServingPolicy,
)
from synapse_channel.core.secret_files import SecretFileError, read_regular_file_bytes
from synapse_channel.core.tls import MTLSPeerTrustBundle, MTLSTrustedPeer

SERVING_POLICY_VERSION = 1
"""Current on-disk multi-hub serving-policy version."""

MAX_SERVING_POLICY_BYTES = 65_536
"""Maximum accepted policy document size."""

_ROOT_FIELDS = frozenset({"version", "federation_store", "client_ca_file", "grants"})
_GRANT_FIELDS = frozenset({"sender", "domain_id", "namespace", "signing_key_id"})


class MultiHubServingConfigError(SynapseError, ValueError):
    """The multi-hub serving policy is inaccessible or inconsistent."""

    code = "multihub_serving_config"


@dataclass(frozen=True, slots=True)
class LoadedMultiHubServingConfig:
    """Runtime serving policy and its validated client-CA path.

    Attributes
    ----------
    policy : MultiHubServingPolicy
        Deny-by-default live peer authorisation policy.
    client_ca_file : pathlib.Path
        CA bundle used by the hub TLS context to request and verify client
        certificates.  The path has already passed a bounded no-follow read.
    federation_store : pathlib.Path
        Audited federation store from which the policy was derived.
    """

    policy: MultiHubServingPolicy
    client_ca_file: Path
    federation_store: Path


class _DuplicateField(ValueError):
    """Internal duplicate JSON field signal."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object while refusing duplicate field names."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateField(key)
        result[key] = value
    return result


def _exact_object(value: object, *, label: str, fields: frozenset[str]) -> dict[str, object]:
    """Return an exact-field object or raise a bounded configuration error."""
    if not isinstance(value, dict):
        raise MultiHubServingConfigError(f"{label} must be a JSON object")
    item = cast("dict[str, object]", value)
    unknown = set(item) - fields
    missing = fields - set(item)
    if unknown:
        raise MultiHubServingConfigError(
            f"{label} has unknown fields: {', '.join(sorted(unknown))}"
        )
    if missing:
        raise MultiHubServingConfigError(f"{label} is missing fields: {', '.join(sorted(missing))}")
    return item


def _text(value: object, *, label: str) -> str:
    """Return one stripped non-empty string without echoing rejected content."""
    if not isinstance(value, str) or not value.strip():
        raise MultiHubServingConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _resolved_path(value: object, *, label: str, base: Path) -> Path:
    """Resolve one policy path relative to its document directory."""
    configured = Path(_text(value, label=label)).expanduser()
    return configured if configured.is_absolute() else base / configured


def _parse_document(raw: bytes) -> dict[str, object]:
    """Decode strict UTF-8 JSON with duplicate and non-finite rejection."""
    try:
        decoded: object = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                MultiHubServingConfigError(
                    f"multi-hub serving policy contains non-finite constant {value!r}"
                )
            ),
        )
    except _DuplicateField as exc:
        raise MultiHubServingConfigError(
            f"multi-hub serving policy repeats field {exc.args[0]!r}"
        ) from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MultiHubServingConfigError(
            "multi-hub serving policy is not valid UTF-8 JSON"
        ) from exc
    return _exact_object(decoded, label="multi-hub serving policy", fields=_ROOT_FIELDS)


def _parse_grants(value: object) -> dict[str, MultiHubServingGrant]:
    """Parse a non-empty, sender-unique serving grant list."""
    if not isinstance(value, list) or not value:
        raise MultiHubServingConfigError("multi-hub serving policy grants must be a non-empty list")
    grants: dict[str, MultiHubServingGrant] = {}
    for index, raw in enumerate(value):
        item = _exact_object(raw, label=f"grants[{index}]", fields=_GRANT_FIELDS)
        sender = _text(item["sender"], label=f"grants[{index}].sender")
        if sender in grants:
            raise MultiHubServingConfigError(f"serving policy grants sender {sender!r} twice")
        grants[sender] = MultiHubServingGrant(
            domain_id=_text(item["domain_id"], label=f"grants[{index}].domain_id"),
            namespace=_text(item["namespace"], label=f"grants[{index}].namespace"),
            signing_key_id=_text(item["signing_key_id"], label=f"grants[{index}].signing_key_id"),
        )
    return grants


def _mtls_bundle(
    grants: Mapping[str, MultiHubServingGrant],
    records: Mapping[str, Any],
) -> MTLSPeerTrustBundle:
    """Derive mutual-TLS trust from the same federation records as the policy."""
    peers: dict[str, MTLSTrustedPeer] = {}
    for sender, grant in grants.items():
        record = records.get(grant.domain_id)
        if record is None:
            raise MultiHubServingConfigError(
                f"serving grant {sender!r} names unknown federation domain {grant.domain_id!r}"
            )
        peer = record.peer
        if grant.namespace not in peer.namespaces:
            raise MultiHubServingConfigError(
                f"serving grant {sender!r} namespace is absent from its federation peering"
            )
        if grant.signing_key_id not in peer.signing_key_ids:
            raise MultiHubServingConfigError(
                f"serving grant {sender!r} signing key is absent from its federation peering"
            )
        if not peer.certificate_pins:
            raise MultiHubServingConfigError(
                f"serving grant {sender!r} federation peering has no certificate pin"
            )
        peers[grant.domain_id] = MTLSTrustedPeer(
            peer_id=grant.domain_id,
            certificate_pins=peer.certificate_pins,
            signing_key_ids=peer.signing_key_ids,
            projects=peer.namespaces,
            revoked=peer.revoked,
        )
    return MTLSPeerTrustBundle(peers=peers)


def load_multihub_serving_config(path: str | Path) -> LoadedMultiHubServingConfig:
    """Load a strict serving policy and compose its federation/mTLS bundles.

    Parameters
    ----------
    path : str or pathlib.Path
        Versioned JSON policy document.  Relative paths inside it resolve from
        the document's directory.

    Returns
    -------
    LoadedMultiHubServingConfig
        Runtime policy plus validated client-CA and federation-store paths.

    Raises
    ------
    MultiHubServingConfigError
        If the document, referenced files, grants, or trust composition are
        unavailable or inconsistent.
    """
    policy_path = Path(path).expanduser()
    try:
        raw = read_regular_file_bytes(
            policy_path,
            label="multi-hub serving policy",
            limit=MAX_SERVING_POLICY_BYTES,
        )
        document = _parse_document(raw)
        version = document["version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise MultiHubServingConfigError("multi-hub serving policy version must be an integer")
        if version != SERVING_POLICY_VERSION:
            raise MultiHubServingConfigError(
                f"unsupported multi-hub serving policy version: {version!r}"
            )
        base = policy_path.parent
        federation_store = _resolved_path(
            document["federation_store"], label="federation_store", base=base
        )
        client_ca_file = _resolved_path(
            document["client_ca_file"], label="client_ca_file", base=base
        )
        read_regular_file_bytes(client_ca_file, label="multi-hub client CA")
        records = load_store(federation_store)
        grants = _parse_grants(document["grants"])
        mtls = _mtls_bundle(grants, records)
    except (FederationStoreError, SecretFileError) as exc:
        raise MultiHubServingConfigError(str(exc)) from exc
    return LoadedMultiHubServingConfig(
        policy=MultiHubServingPolicy(
            federation=FederationBundle(record.peer for record in records.values()),
            mtls=mtls,
            grants=grants,
            clock=time.time,
        ),
        client_ca_file=client_ca_file,
        federation_store=federation_store,
    )
