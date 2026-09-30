# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the peer frames of shared-pool reservations (F02)
"""Frame and unframe a peer hub's reservation, settlement or query, and the owner's answer.

A ``spend_request`` frame carries ``spend_action`` (``reserve``, ``settle`` or
``query``) and the ``spend`` document that
:class:`~synapse_channel.core.spend_ledger.SpendLedger` validates in full. The owner
answers privately with ``spend_result`` carrying the same action and the ledger's
response. The codec checks only the envelope. It never interprets amounts, so the
serving and requesting halves agree on the shape without importing each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from synapse_channel.core.errors import SynapseError

SPEND_ACTIONS = ("reserve", "settle", "query")
"""Peer actions on the owner's ledger. Configuring and reconciling are never on the wire."""

REFUSALS: Mapping[str, Mapping[str, object]] = {
    "reserve": {"admitted": False, "reason": "not-admitted"},
    "settle": {"settled": False, "reason": "not-admitted"},
    "query": {"found": False},
}
"""The uniform answer to a peer the owner will not serve, per action."""


class SpendWireError(SynapseError, ValueError):
    """Raised when a spend frame does not have the documented shape."""

    code = "spend_wire"


def encode_spend_request(action: str, document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the fields of a ``spend_request`` frame."""
    if action not in SPEND_ACTIONS:
        raise SpendWireError(f"spend action must be one of {', '.join(SPEND_ACTIONS)}")
    return {"spend_action": action, "spend": dict(document)}


def decode_spend_request(data: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Return the action and document of a ``spend_request`` frame.

    Raises
    ------
    SpendWireError
        When the action is unknown or the document is not an object.
    """
    action = data.get("spend_action")
    document = data.get("spend")
    if action not in SPEND_ACTIONS or not isinstance(document, Mapping):
        raise SpendWireError("a spend request names an action and carries a document")
    return str(action), document


def encode_spend_result(action: str, result: Mapping[str, object]) -> dict[str, Any]:
    """Return the fields of a ``spend_result`` frame."""
    return {"spend_action": action, "spend_result": dict(result)}


def decode_spend_result(data: Mapping[str, Any], action: str) -> dict[str, Any]:
    """Return the owner's response for ``action`` from a ``spend_result`` frame.

    Raises
    ------
    SpendWireError
        When the frame answers another action or carries no result object.
    """
    result = data.get("spend_result")
    if data.get("spend_action") != action or not isinstance(result, Mapping):
        raise SpendWireError("the spend result does not answer this request")
    return dict(result)
