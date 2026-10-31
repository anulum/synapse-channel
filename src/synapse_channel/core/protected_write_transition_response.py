# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — unsigned protected transition responses
"""Build unsigned transitions without authentication, signing or journal authority."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from synapse_channel.core.protected_write_lifecycle import ProtectedWriteReservation

PROTECTED_RESULT_STATE_FIELDS = (
    "reservation_id",
    "operation_phase",
    "revocation_phase",
    "outcome",
    "writer_principal",
    "writer_incarnation",
    "admission_sequence",
    "begin_sequence",
    "settlement_sequence",
    "revocation_sequence",
    "evidence_reference",
    "reason_code",
)


def _advance_response_body(
    body: dict[str, object], source: Mapping[str, object], previous: ProtectedWriteReservation
) -> None:
    """Project an eligible verb without assigning sequences or signing authority."""
    verb = str(source["type"])
    request_body = cast(dict[str, object], source["body"])
    old = json.loads(previous.result_bytes)["body"]
    if verb == "protected_write_begin":
        body["operation_phase"] = "executing"
    elif verb == "protected_write_settle":
        outcome = request_body["outcome"]
        body["outcome"] = outcome
        body["operation_phase"] = (
            "settled" if outcome in {"committed", "no_write"} else "recovery_required"
        )
        body["evidence_reference"] = (
            request_body["quiescence_reference"]
            if outcome in {"committed", "no_write"}
            else request_body["writer_journal_reference"]
        )
        if old["revocation_phase"] == "requested" and outcome in {
            "committed",
            "no_write",
        }:
            body["revocation_phase"] = "effective"
    elif old["operation_phase"] == "admitted":
        body.update(
            operation_phase="settled",
            outcome="no_write",
            revocation_phase="effective",
            disposition="effective",
            evidence_reference=json.loads(previous.admission.proposal_bytes)["content_reference"],
        )
    elif old["operation_phase"] == "settled":
        body["revocation_phase"] = "effective"
        body["disposition"] = "effective"
    else:
        body["revocation_phase"] = "requested"
        body["disposition"] = "pending"


def make_protected_transition_response(
    source: Mapping[str, object],
    *,
    request_digest: str,
    disposition: str,
    now: float,
    previous: ProtectedWriteReservation | None,
) -> dict[str, object]:
    """Build an unsigned checked transition or operation-identity conflict.

    Parameters
    ----------
    source:
        Canonical already parsed and authenticated request fields.
    request_digest:
        Digest retained by the request parser, not supplied in the body.
    disposition:
        Actor-selected accepted or conflict disposition.
    now:
        Trusted authority time sampled under the actor.
    previous:
        Eligible current reservation; None only for an identity conflict.

    Returns
    -------
    dict[str, object]
        Fresh unsigned response. Journal finalization must assign actual sequences,
        sign and validate it before publishing changed reservation state.
    """
    if previous is None:
        body: dict[str, object] = dict.fromkeys(PROTECTED_RESULT_STATE_FIELDS)
    else:
        body = dict(json.loads(previous.result_bytes)["body"])
    body.update(request_type=source["type"], request_digest=request_digest, disposition=disposition)
    if previous is not None:
        _advance_response_body(body, source, previous)
    envelope = {name: value for name, value in source.items() if name not in {"auth", "signature"}}
    envelope.update(
        type="protected_write_result",
        sender=source["target"],
        target=source["sender"],
        timestamp=now,
        body=body,
    )
    return envelope
