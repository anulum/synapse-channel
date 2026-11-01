# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — causality graph nodes keep and check claim path identities
"""A claim's path identity is checked against its display paths and kept in the JSON view."""

from __future__ import annotations

import pytest

from synapse_channel.core.causality import build_causal_graph, causality_to_json, causes
from synapse_channel.core.journal import EventKind
from synapse_channel.core.path_identity import CanonicalPathIdentity, ClaimScopeIdentity
from synapse_channel.core.persistence import StoredEvent


def _identity(path: str) -> dict[str, object]:
    return ClaimScopeIdentity(
        worktree_path="wt1",
        worktree_object_id="root:1",
        filesystem_namespace="host:1",
        case_sensitive=True,
        paths=(CanonicalPathIdentity(path, path, "1:2"),),
    ).as_dict()


def _claim(seq: int, paths: tuple[str, ...], identity: dict[str, object]) -> StoredEvent:
    return StoredEvent(
        seq=seq,
        ts=float(seq),
        kind=EventKind.CLAIM,
        payload={
            "task_id": "A",
            "owner": "alice",
            "status": "claimed",
            "paths": list(paths),
            "worktree": "wt1",
            "path_identity": identity,
        },
    )


def test_a_claim_whose_identity_names_other_paths_is_refused() -> None:
    """An identity that does not align with the claim's display paths stops the graph."""
    with pytest.raises(ValueError, match="does not match its display scope"):
        build_causal_graph((_claim(1, ("owned.py",), _identity("other.py")),))


def test_the_json_view_keeps_a_claims_path_identity() -> None:
    """A node built from a claim with an identity carries that identity in its JSON form."""
    identity = _identity("owned.py")
    graph = build_causal_graph((_claim(1, ("owned.py",), identity),))
    rendered = causality_to_json(causes(graph, 1))
    node = rendered["node"]
    assert isinstance(node, dict)
    assert node["path_identity"] == identity
