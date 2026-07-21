# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared helpers for the cmd_hub test split

from __future__ import annotations

import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any


def _close_runner(coro: Coroutine[Any, Any, None]) -> None:
    coro.close()


def _owner_only(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def _federation_store(tmp_path: Path, *, grant_scope: bool = True) -> str:
    """Write a federation store with one peer and return its path.

    With ``grant_scope`` the peering maps a scope grant inside its granted
    namespace, so it could authorise a cross-domain frame under per-message
    authentication; without it the peering is observe-only by construction.
    """
    from synapse_channel.core.federation import FederationPeer, ScopeGrant
    from synapse_channel.core.federation_store import (
        FederationRecord,
        PeerProvenance,
        save_store,
    )

    peer = FederationPeer(
        domain_id="domain-b",
        namespaces=frozenset({"SYNAPSE-CHANNEL"}),
        certificate_pins=frozenset({"sha256:aa"}),
        signing_key_ids=frozenset({"domain-b:main"}),
        scope_grants=(ScopeGrant("message", "SYNAPSE-CHANNEL"),) if grant_scope else (),
    )
    store = tmp_path / "federation.json"
    save_store(store, [FederationRecord(peer, PeerProvenance("bundle", 1.0, "ops"))])
    return str(store)


def _write_identity_trust(path: Path) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    path.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": "k",
                        "public_key": base64.b64encode(raw).decode("ascii"),
                        "senders": ["proj/claude"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
