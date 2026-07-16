# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — claim-forwarding wire codec tests

from __future__ import annotations

import pytest

from synapse_channel.core.multihub_claim_wire import (
    CLAIM_FIELD,
    CLAIMANT_FIELD,
    DETAIL_FIELD,
    GRANT_FIELD,
    GRANTED_FIELD,
    NAMESPACE_FIELD,
    OWNER_HUB_ID_FIELD,
    TASK_ID_FIELD,
    ClaimForwardRequest,
    ClaimForwardResult,
    ClaimWireError,
    decode_claim_forward_request,
    decode_claim_forward_result,
    encode_claim_forward_request,
    encode_claim_forward_result,
)


def _request() -> ClaimForwardRequest:
    """Return a claim-forward request with a small claim body for round-trip tests."""
    return ClaimForwardRequest(
        namespace="SYNAPSE-CHANNEL",
        claimant="SYNAPSE-CHANNEL/alice",
        task_id="task-1",
        claim={"note": "wire up the codec", "paths": ["a.py"]},
    )


def _grant() -> dict[str, object]:
    """Return an authentic grant body the owning hub would relay back."""
    return {
        "owner": "SYNAPSE-CHANNEL/alice",
        "lease_expires_at": 123.0,
        "status": "claimed",
        "paths": ["a.py"],
        "epoch": 0,
        "version": 0,
    }


# --- request -----------------------------------------------------------------------------


def test_encode_claim_forward_request_emits_canonical_fields() -> None:
    body = encode_claim_forward_request(_request())
    assert body == {
        NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
        CLAIMANT_FIELD: "SYNAPSE-CHANNEL/alice",
        TASK_ID_FIELD: "task-1",
        CLAIM_FIELD: {"note": "wire up the codec", "paths": ["a.py"]},
    }


def test_claim_forward_request_round_trips() -> None:
    request = _request()
    assert decode_claim_forward_request(encode_claim_forward_request(request)) == request


def test_claim_forward_request_preserves_additive_path_identity() -> None:
    identity = {
        "version": 1,
        "worktree_path": "/repo",
        "worktree_object_id": "1:2",
        "filesystem_namespace": "host:1",
        "case_sensitive": True,
        "paths": [{"git_path": "a.py", "filesystem_path": "real.py", "object_id": "1:3"}],
    }
    request = ClaimForwardRequest(
        namespace="SYNAPSE-CHANNEL",
        claimant="SYNAPSE-CHANNEL/alice",
        task_id="task-identity",
        claim={"paths": ["a.py"], "path_identity": identity},
    )

    decoded = decode_claim_forward_request(encode_claim_forward_request(request))

    assert decoded.claim["path_identity"] == identity


@pytest.mark.parametrize("field", [NAMESPACE_FIELD, CLAIMANT_FIELD, TASK_ID_FIELD])
def test_encode_claim_forward_request_rejects_blank_identifiers(field: str) -> None:
    kwargs = {
        "namespace": "SYNAPSE-CHANNEL",
        "claimant": "SYNAPSE-CHANNEL/alice",
        "task_id": "task-1",
    }
    kwargs[field] = "   "
    with pytest.raises(ClaimWireError, match=f"{field} must not be empty"):
        encode_claim_forward_request(ClaimForwardRequest(claim={}, **kwargs))


def test_decode_claim_forward_request_rejects_non_mapping() -> None:
    with pytest.raises(ClaimWireError, match="request body must be a JSON object"):
        decode_claim_forward_request(["not", "a", "mapping"])


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({NAMESPACE_FIELD: ""}, "namespace must not be empty"),
        ({NAMESPACE_FIELD: 7}, "namespace must be a string"),
        ({CLAIMANT_FIELD: ""}, "claimant must not be empty"),
        ({TASK_ID_FIELD: None}, "task_id must be a string"),
        ({CLAIM_FIELD: "flat"}, "claim body must be a JSON object"),
    ],
)
def test_decode_claim_forward_request_rejects_bad_fields(
    mutation: dict[str, object], match: str
) -> None:
    body: dict[str, object] = {
        NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
        CLAIMANT_FIELD: "SYNAPSE-CHANNEL/alice",
        TASK_ID_FIELD: "task-1",
        CLAIM_FIELD: {},
    }
    body.update(mutation)
    with pytest.raises(ClaimWireError, match=match):
        decode_claim_forward_request(body)


def test_decode_claim_forward_request_copies_claim() -> None:
    claim = {"note": "shared"}
    decoded = decode_claim_forward_request(
        {
            NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
            CLAIMANT_FIELD: "SYNAPSE-CHANNEL/alice",
            TASK_ID_FIELD: "task-1",
            CLAIM_FIELD: claim,
        }
    )
    claim["note"] = "mutated"
    assert decoded.claim == {"note": "shared"}


# --- result ------------------------------------------------------------------------------


def test_encode_claim_forward_result_emits_grant() -> None:
    result = ClaimForwardResult(
        granted=True,
        task_id="task-1",
        namespace="SYNAPSE-CHANNEL",
        owner_hub_id="hub-owner",
        detail="granted",
        grant=_grant(),
    )
    body = encode_claim_forward_result(result)
    assert body[GRANTED_FIELD] is True
    assert body[OWNER_HUB_ID_FIELD] == "hub-owner"
    assert body[GRANT_FIELD] == _grant()


def test_encode_claim_forward_result_denial_emits_null_grant() -> None:
    result = ClaimForwardResult(
        granted=False,
        task_id="task-1",
        namespace="SYNAPSE-CHANNEL",
        owner_hub_id="hub-owner",
        detail="task already held",
    )
    body = encode_claim_forward_result(result)
    assert body[GRANTED_FIELD] is False
    assert body[GRANT_FIELD] is None


def test_claim_forward_result_round_trips_granted() -> None:
    result = ClaimForwardResult(
        granted=True,
        task_id="task-1",
        namespace="SYNAPSE-CHANNEL",
        owner_hub_id="hub-owner",
        detail="granted",
        grant=_grant(),
    )
    assert decode_claim_forward_result(encode_claim_forward_result(result)) == result


def test_claim_forward_result_round_trips_denied() -> None:
    result = ClaimForwardResult(
        granted=False,
        task_id="task-1",
        namespace="SYNAPSE-CHANNEL",
        owner_hub_id="hub-owner",
    )
    assert decode_claim_forward_result(encode_claim_forward_result(result)) == result


@pytest.mark.parametrize(
    ("result", "match"),
    [
        (
            ClaimForwardResult(
                granted=False, task_id="", namespace="SYNAPSE-CHANNEL", owner_hub_id="hub-owner"
            ),
            "task_id must not be empty",
        ),
        (
            ClaimForwardResult(
                granted=False, task_id="task-1", namespace="", owner_hub_id="hub-owner"
            ),
            "namespace must not be empty",
        ),
        (
            ClaimForwardResult(
                granted=False, task_id="task-1", namespace="SYNAPSE-CHANNEL", owner_hub_id=""
            ),
            "owner_hub_id must not be empty",
        ),
    ],
    ids=["task_id", "namespace", "owner_hub_id"],
)
def test_encode_claim_forward_result_rejects_blank_identifiers(
    result: ClaimForwardResult, match: str
) -> None:
    with pytest.raises(ClaimWireError, match=match):
        encode_claim_forward_result(result)


def test_decode_claim_forward_result_rejects_non_mapping() -> None:
    with pytest.raises(ClaimWireError, match="result body must be a JSON object"):
        decode_claim_forward_result("nope")


def test_decode_claim_forward_result_absent_detail_is_empty() -> None:
    decoded = decode_claim_forward_result(
        {
            GRANTED_FIELD: False,
            TASK_ID_FIELD: "task-1",
            NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
            OWNER_HUB_ID_FIELD: "hub-owner",
        }
    )
    assert decoded.detail == ""
    assert decoded.grant is None


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({GRANTED_FIELD: "yes"}, "granted must be a boolean"),
        ({GRANTED_FIELD: 1}, "granted must be a boolean"),
        ({TASK_ID_FIELD: ""}, "task_id must not be empty"),
        ({NAMESPACE_FIELD: 9}, "namespace must be a string"),
        ({OWNER_HUB_ID_FIELD: ""}, "owner_hub_id must not be empty"),
        ({DETAIL_FIELD: 5}, "detail must be a string"),
        ({GRANT_FIELD: "flat"}, "grant body must be a JSON object"),
    ],
)
def test_decode_claim_forward_result_rejects_bad_fields(
    mutation: dict[str, object], match: str
) -> None:
    body: dict[str, object] = {
        GRANTED_FIELD: True,
        TASK_ID_FIELD: "task-1",
        NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
        OWNER_HUB_ID_FIELD: "hub-owner",
        DETAIL_FIELD: "granted",
        GRANT_FIELD: {},
    }
    body.update(mutation)
    with pytest.raises(ClaimWireError, match=match):
        decode_claim_forward_result(body)


def test_decode_claim_forward_result_copies_grant() -> None:
    grant = {"owner": "SYNAPSE-CHANNEL/alice"}
    decoded = decode_claim_forward_result(
        {
            GRANTED_FIELD: True,
            TASK_ID_FIELD: "task-1",
            NAMESPACE_FIELD: "SYNAPSE-CHANNEL",
            OWNER_HUB_ID_FIELD: "hub-owner",
            GRANT_FIELD: grant,
        }
    )
    grant["owner"] = "mutated"
    assert decoded.grant == {"owner": "SYNAPSE-CHANNEL/alice"}
