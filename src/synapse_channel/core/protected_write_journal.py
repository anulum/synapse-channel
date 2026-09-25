# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — shared protected operation commit evidence
"""Read one complete atomic response without inventing a second operation ledger."""

from __future__ import annotations

import hashlib
import json

from synapse_channel.core.persistence import EventStore


def protected_operation_response(
    store: EventStore,
    *,
    operation_key: str,
    request_digest: str,
    mutation_sequence: int,
    through_seq: int | None = None,
) -> str:
    """Verify the complete operation, canonical response and commit marker.

    Parameters
    ----------
    store:
        Authoritative journal containing the mutation and operation.
    operation_key:
        Already bound principal/authority/request namespace.
    request_digest:
        Semantic digest recomputed from the validated original request.
    mutation_sequence:
        Actual event sequence for this single-mutation operation.
    through_seq:
        Optional replay prefix, which cannot split the atomic operation.

    Returns
    -------
    str
        Exact canonical stored response JSON.

    Raises
    ------
    ValueError
        On absent, incomplete or inconsistent operation evidence.
    """
    if type(mutation_sequence) is not int or mutation_sequence <= 0:
        raise ValueError("invalid protected mutation sequence")
    operation = store.get_operation(operation_key)
    if (
        operation is None
        or operation.request_digest != request_digest
        or operation.first_event_seq != mutation_sequence
        or operation.commit_seq != mutation_sequence + 1
        or (through_seq is not None and operation.commit_seq > through_seq)
    ):
        raise ValueError("protected mutation lacks a complete matching operation")
    response = json.dumps(
        operation.response,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if hashlib.sha256(response.encode("ascii")).hexdigest() != operation.response_sha256:
        raise ValueError("protected mutation response integrity mismatch")
    marker = store.latest_at_or_before(operation.commit_seq)
    if (
        marker is None
        or marker.seq != operation.commit_seq
        or marker.kind != "idempotency"
        or marker.payload.get("key") != operation_key
        or marker.payload.get("request_digest") != request_digest
        or marker.payload.get("response_sha256") != operation.response_sha256
        or json.dumps(
            marker.payload.get("response"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        != response
    ):
        raise ValueError("protected mutation commit marker mismatch")
    return response
