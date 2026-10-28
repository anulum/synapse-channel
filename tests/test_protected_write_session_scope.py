# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — credential-free session metadata contracts
"""Exercise public scopes and their actual retained journal representation."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.core.journal import replay
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protected_write_enrollment_journal import (
    ISSUE_EVENT_KIND,
    enrollment_issue_payload,
)
from synapse_channel.core.protected_write_session_scope import ProtectedSessionScope
from test_protected_write_enrollment_journal import _enrollment


@pytest.mark.parametrize("incarnation", [None, "writer-incarnation"])
def test_scope_can_describe_an_operator_session_without_a_key(
    incarnation: str | None,
) -> None:
    """Metadata construction does not require or retain credential material."""
    grant = _enrollment()
    scope = ProtectedSessionScope(
        grant.session_id,
        grant.principal,
        grant.target,
        grant.authority_id,
        grant.authority_continuity,
        grant.enrollment_revision,
        grant.transaction_id,
        grant.proposal_sha256,
        grant.verbs,
        grant.expires_at,
        writer_incarnation=incarnation,
    )
    assert scope.session_id == grant.session_id
    assert scope.principal == grant.principal and scope.verbs == grant.verbs
    assert scope.writer_incarnation == incarnation
    assert not hasattr(scope, "key")


def test_actual_retained_metadata_reuses_scope_validation_without_a_credential(
    tmp_path: Path,
) -> None:
    """Reopening a real metadata-only journal reconstructs the complete scope."""
    grant = _enrollment()
    path = tmp_path / "metadata.db"
    with EventStore(path) as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(grant), durable=True)
    assert grant.key.secret not in path.read_bytes()
    with EventStore(path) as store:
        state = replay(store, protected_write_policies={}).state
        retained = state.protected_enrollments[grant.session_id]
        assert isinstance(retained, ProtectedSessionScope)
        assert not hasattr(retained, "key")
        assert retained.writer_incarnation == grant.writer_incarnation
        assert retained.materialize(grant.key, now=grant.expires_at - 1) == grant
        assert store.read_all()[0].payload == enrollment_issue_payload(grant)


@pytest.mark.parametrize(
    "field",
    [
        "session_id",
        "principal",
        "target",
        "authority_id",
        "authority_continuity",
        "enrollment_revision",
        "transaction_id",
    ],
)
def test_retained_scope_rejects_each_empty_identity_field(tmp_path: Path, field: str) -> None:
    """A malformed retained identity cannot become a partially valid session."""
    payload = enrollment_issue_payload(_enrollment())
    payload[field] = ""
    with EventStore(tmp_path / "identity.db") as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
        with pytest.raises(ValueError, match="nonempty identifiers"):
            replay(store, protected_write_policies={})


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"proposal_sha256": None}, "exact proposal digest"),
        ({"proposal_sha256": "a" * 63}, "exact proposal digest"),
        ({"proposal_sha256": "G" * 64}, "exact proposal digest"),
        ({"verbs": ["chat"]}, "explicit supported verbs"),
        ({"expires_at": True}, "expiry must be finite"),
        ({"writer_incarnation": ""}, "exact nonempty identifier"),
        ({"writer_incarnation": 1}, "exact nonempty identifier"),
    ],
)
def test_retained_scope_refuses_unusable_proposal_verbs_expiry_and_incarnation(
    tmp_path: Path, change: dict[str, object], reason: str
) -> None:
    """Valid JSON still has to satisfy the actual public session metadata contract."""
    payload = enrollment_issue_payload(_enrollment())
    payload.update(change)
    with EventStore(tmp_path / "scope.db") as store:
        store.append(ISSUE_EVENT_KIND, payload, durable=True)
        with pytest.raises(ValueError, match=reason):
            replay(store, protected_write_policies={})


@pytest.mark.parametrize("expiry", [float("inf"), float("-inf"), float("nan")])
def test_scope_refuses_nonfinite_operator_expiry_before_serialisation(expiry: float) -> None:
    """The Python API refuses invalid time before a journal encoder is involved."""
    with pytest.raises(ValueError, match="expiry must be finite"):
        replace(_enrollment(), expires_at=expiry)


@pytest.mark.parametrize("revoked", [None, 0, 1, "false"])
def test_retained_record_requires_an_explicit_boolean_revocation(
    tmp_path: Path, revoked: object
) -> None:
    """An external record adapter cannot substitute a truthy scalar for revocation."""
    grant = _enrollment()
    with EventStore(tmp_path / "record.db") as store:
        store.append(ISSUE_EVENT_KIND, enrollment_issue_payload(grant), durable=True)
        retained = replay(store, protected_write_policies={}).state.protected_enrollments[
            grant.session_id
        ]
        fields = json.loads(json.dumps({"revoked": revoked}))
        with pytest.raises(ValueError, match="revocation flag must be boolean"):
            replace(retained, **fields)
