# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — wire codec for forwarding a claim to its owning hub
"""Canonical wire codec for forwarding a claim to the hub that owns its namespace.

Claims are mutual exclusion routed by namespace ownership
(:mod:`synapse_channel.core.namespace_ownership`): each namespace has exactly one
authoritative owning hub, and only that hub grants claims inside it. When an agent
claims through a non-owning hub, that hub must *ask* the owning hub to grant the claim
on the agent's behalf and relay the authoritative answer back. This module is the one
place that names the shapes of that exchange, so the serving half (a handler on the
owning hub) and the forwarding half (a network client on the non-owning hub) agree on
the format without importing each other — exactly as
:mod:`synapse_channel.core.multihub_wire` does for the event-log pull.

Two shapes ride the exchange:

* a :class:`ClaimForwardRequest` — the namespace, the claimant the grant is made under,
  the task id, and the original claim body the owning hub re-applies authoritatively;
* a :class:`ClaimForwardResult` — whether the owner granted, the owning hub's id, a
  human-readable detail, and (on a grant) the authentic grant fields the forwarding hub
  relays to its client as a :data:`~synapse_channel.core.protocol.MessageType.CLAIM_GRANTED`.

The codec is **pure**: it has no network, no clock, and no hub dependency — it only
converts these shapes to and from the JSON-object bodies that ride the wire envelope
(:func:`synapse_channel.core.protocol.build_envelope`). Decoding is defensive because a
request and a result each arrive from another host: a malformed body raises
:class:`ClaimWireError` rather than yielding a half-built shape, so a forwarding hub that
catches it refuses the claim and never relays a grant it cannot trust — the same
fail-closed posture the local grant path already holds.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from synapse_channel.core.errors import SynapseError

NAMESPACE_FIELD = "namespace"
"""Request and result field: the namespace the claim concerns, owned by exactly one hub."""

CLAIMANT_FIELD = "claimant"
"""Request field: the agent identity the owning hub grants the claim under."""

TASK_ID_FIELD = "task_id"
"""Request and result field: the claimed task id, correlating a result with its request."""

CLAIM_FIELD = "claim"
"""Request field: the original claim body the owning hub re-applies (note, ttl, paths, git)."""

GRANTED_FIELD = "granted"
"""Result field: whether the owning hub granted the claim authoritatively."""

OWNER_HUB_ID_FIELD = "owner_hub_id"
"""Result field: the id of the owning hub that authoritatively answered."""

DETAIL_FIELD = "detail"
"""Result field: the human-readable grant or denial message, possibly empty."""

GRANT_FIELD = "grant"
"""Result field: the authentic grant fields on a grant, or ``null`` on a denial."""


class ClaimWireError(SynapseError, ValueError):
    """Raised when a claim-forwarding wire body is malformed.

    Carries the fail-closed contract: a forwarding hub that catches this refuses the
    claim and relays no grant, so a corrupt or hostile request or result can never cause
    a claim to be granted on doubt.
    """

    code = "claim_wire"


@dataclass(frozen=True, slots=True)
class ClaimForwardRequest:
    """A non-owning hub's request that the owning hub grant a claim on a claimant's behalf.

    Parameters
    ----------
    namespace : str
        The namespace the claim concerns; the owning hub verifies it owns this before
        granting. Non-empty.
    claimant : str
        The agent identity the grant is made under — the original claim's sender, so the
        owning hub records the lease in the right agent's name. Non-empty.
    task_id : str
        The claimed task id, echoed back in the result for request/result correlation.
        Non-empty.
    claim : Mapping[str, Any]
        The original claim body (note, ttl, worktree, paths, git, …) the owning hub
        re-applies through its authoritative grant path. A JSON object.
    """

    namespace: str
    claimant: str
    task_id: str
    claim: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ClaimForwardResult:
    """The owning hub's authoritative answer to a forwarded claim, relayed to the client.

    Parameters
    ----------
    granted : bool
        Whether the owning hub granted the claim.
    task_id : str
        The claimed task id, matching the request it answers. Non-empty.
    namespace : str
        The namespace the claim concerned. Non-empty.
    owner_hub_id : str
        The id of the owning hub that produced this answer. Non-empty.
    detail : str
        The human-readable grant or denial message; may be empty.
    grant : Mapping[str, Any] or None
        On a grant, the authentic grant fields (owner, lease, status, paths, epoch,
        version, checkpoint, git) the forwarding hub relays as a ``CLAIM_GRANTED``. ``None``
        on a denial.
    """

    granted: bool
    task_id: str
    namespace: str
    owner_hub_id: str
    detail: str = ""
    grant: Mapping[str, Any] | None = None


def encode_claim_forward_request(request: ClaimForwardRequest) -> dict[str, Any]:
    """Return the JSON-object body for a claim-forward request.

    Parameters
    ----------
    request : ClaimForwardRequest
        The namespace, claimant, task id, and claim body to forward.

    Returns
    -------
    dict[str, Any]
        A mapping with ``namespace``, ``claimant``, ``task_id``, and ``claim``.

    Raises
    ------
    ClaimWireError
        If ``namespace``, ``claimant``, or ``task_id`` is empty.
    """
    return {
        NAMESPACE_FIELD: _require_nonempty(request.namespace, NAMESPACE_FIELD),
        CLAIMANT_FIELD: _require_nonempty(request.claimant, CLAIMANT_FIELD),
        TASK_ID_FIELD: _require_nonempty(request.task_id, TASK_ID_FIELD),
        CLAIM_FIELD: dict(request.claim),
    }


def decode_claim_forward_request(raw: object) -> ClaimForwardRequest:
    """Reconstruct a claim-forward request from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded request body; expected to be a mapping.

    Returns
    -------
    ClaimForwardRequest
        The reconstructed request.

    Raises
    ------
    ClaimWireError
        If the body is not a mapping, an identifier field is missing/non-string/empty, or
        ``claim`` is missing or not a mapping.
    """
    body = _require_mapping(raw, "request")
    return ClaimForwardRequest(
        namespace=_require_nonempty(body.get(NAMESPACE_FIELD), NAMESPACE_FIELD),
        claimant=_require_nonempty(body.get(CLAIMANT_FIELD), CLAIMANT_FIELD),
        task_id=_require_nonempty(body.get(TASK_ID_FIELD), TASK_ID_FIELD),
        claim=dict(_require_mapping(body.get(CLAIM_FIELD), CLAIM_FIELD)),
    )


def encode_claim_forward_result(result: ClaimForwardResult) -> dict[str, Any]:
    """Return the JSON-object body for a claim-forward result.

    Parameters
    ----------
    result : ClaimForwardResult
        The owning hub's authoritative answer.

    Returns
    -------
    dict[str, Any]
        A mapping with ``granted``, ``task_id``, ``namespace``, ``owner_hub_id``,
        ``detail``, and ``grant`` (``null`` when the claim was denied).

    Raises
    ------
    ClaimWireError
        If ``task_id``, ``namespace``, or ``owner_hub_id`` is empty.
    """
    return {
        GRANTED_FIELD: bool(result.granted),
        TASK_ID_FIELD: _require_nonempty(result.task_id, TASK_ID_FIELD),
        NAMESPACE_FIELD: _require_nonempty(result.namespace, NAMESPACE_FIELD),
        OWNER_HUB_ID_FIELD: _require_nonempty(result.owner_hub_id, OWNER_HUB_ID_FIELD),
        DETAIL_FIELD: str(result.detail),
        GRANT_FIELD: None if result.grant is None else dict(result.grant),
    }


def decode_claim_forward_result(raw: object) -> ClaimForwardResult:
    """Reconstruct a claim-forward result from a decoded JSON object.

    Parameters
    ----------
    raw : object
        The decoded result body; expected to be a mapping.

    Returns
    -------
    ClaimForwardResult
        The reconstructed result.

    Raises
    ------
    ClaimWireError
        If the body is not a mapping, ``granted`` is missing or not a boolean, an
        identifier field is missing/non-string/empty, ``detail`` is present but not a
        string, or ``grant`` is present but neither ``null`` nor a mapping.
    """
    body = _require_mapping(raw, "result")
    raw_grant = body.get(GRANT_FIELD)
    grant = None if raw_grant is None else dict(_require_mapping(raw_grant, GRANT_FIELD))
    raw_detail = body.get(DETAIL_FIELD)
    detail = "" if raw_detail is None else _require_str(raw_detail, DETAIL_FIELD)
    return ClaimForwardResult(
        granted=_require_bool(body.get(GRANTED_FIELD), GRANTED_FIELD),
        task_id=_require_nonempty(body.get(TASK_ID_FIELD), TASK_ID_FIELD),
        namespace=_require_nonempty(body.get(NAMESPACE_FIELD), NAMESPACE_FIELD),
        owner_hub_id=_require_nonempty(body.get(OWNER_HUB_ID_FIELD), OWNER_HUB_ID_FIELD),
        detail=detail,
        grant=grant,
    )


def _require_mapping(value: object, name: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise :class:`ClaimWireError`."""
    if not isinstance(value, Mapping):
        msg = f"{name} body must be a JSON object"
        raise ClaimWireError(msg)
    return value


def _require_str(value: object, name: str) -> str:
    """Return ``value`` as a string or raise :class:`ClaimWireError`."""
    if not isinstance(value, str):
        msg = f"{name} must be a string"
        raise ClaimWireError(msg)
    return value


def _require_nonempty(value: object, name: str) -> str:
    """Return ``value`` as a non-blank string or raise :class:`ClaimWireError`.

    An identifier that is missing, non-string, or blank after stripping cannot route or
    correlate a claim, so it fails closed rather than forwarding an unaddressable request.
    """
    text = _require_str(value, name)
    if not text.strip():
        msg = f"{name} must not be empty"
        raise ClaimWireError(msg)
    return text


def _require_bool(value: object, name: str) -> bool:
    """Return ``value`` as a boolean or raise :class:`ClaimWireError`.

    A non-boolean grant verdict is rejected outright: a forwarding hub never coerces a
    string or number into a grant decision.
    """
    if not isinstance(value, bool):
        msg = f"{name} must be a boolean"
        raise ClaimWireError(msg)
    return value
