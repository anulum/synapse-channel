# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — explicit receiving-hub journal recovery
"""Bind legacy delivery ownership without rewriting immutable request history.

The receiving hub cannot be recovered from a forwarded request's origin or its
local target. Offline recovery therefore requires an operator's external custody
reference. Recovery is atomic, append-only and never invoked during hub startup.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

from synapse_channel.core.delivery_modes import DeliveryRefusal
from synapse_channel.core.hub_address import parse_hub_qualified

logger = logging.getLogger("synapse.delivery")


def _bounded_reference(value: object, maximum: int) -> bool:
    """Validate a printable, non-empty identity or operator custody reference."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return size <= maximum and not any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)


def validate_receiving_hub(value: object) -> None:
    """Refuse an invalid server-selected receiving hub without echoing its value.

    Parameters
    ----------
    value : object
        Non-empty, printable stable hub identity, at most 512 UTF-8 bytes.

    Raises
    ------
    DeliveryRefusal
        If the identity is malformed, padded or outside the byte limit.
    """
    if not _bounded_reference(value, 512):
        raise DeliveryRefusal("invalid_shape", "receiving hub identity is malformed")


def validate_recovery_ref(value: object) -> None:
    """Require bounded external custody evidence for explicit offline recovery.

    Parameters
    ----------
    value : object
        Printable operator record reference, at most 1024 UTF-8 bytes.

    Raises
    ------
    DeliveryRefusal
        If the reference is empty, malformed, padded or outside the byte limit.
    """
    if not _bounded_reference(value, 1024):
        raise DeliveryRefusal("invalid_shape", "delivery recovery reference is malformed")


def bind_legacy_receiving_hub(
    connection: Any,
    *,
    insert_event: Callable[[float, str, str], int],
    verify_authentication: Callable[[], None],
    hub_id: str,
    recovery_ref: str,
    retry_authority_refusals: bool,
) -> int:
    """Bind one locked journal atomically after verifying its complete replay.

    The public ledger facade holds the event store lock. A local legacy request
    or already bound record naming another receiver refuses the entire recovery.
    Only an explicitly requested retry of the historical authority refusal can
    clear quarantine; its reason is retained in the recovery event.

    Parameters
    ----------
    connection : Any
        Owning event store's locked SQLite or SQLCipher connection.
    insert_event : Callable
        Owning store's authenticated event-row writer, used in this transaction.
    verify_authentication : Callable
        Owning store's row-key and complete row-authentication verification.
    hub_id : str
        Stable receiving identity established from external custody evidence.
    recovery_ref : str
        Operator's credential-free reference to the verified recovery record.
    retry_authority_refusals : bool
        Whether to release historical ``unauthorised_requester`` quarantine
        when initially binding an operation. Other reasons remain held.

    Returns
    -------
    int
        Number of legacy operations bound; already bound operations add no rows.

    Raises
    ------
    DeliveryRefusal
        If evidence, replay or a known receiving identity refuses recovery.
    """
    from synapse_channel.core.delivery_persistence import DELIVERY_OWNER_BOUND
    from synapse_channel.core.delivery_replay import verify_delivery_replay

    validate_receiving_hub(hub_id)
    validate_recovery_ref(recovery_ref)
    if not isinstance(retry_authority_refusals, bool):
        raise DeliveryRefusal("invalid_shape", "authority retry flag must be boolean")
    committed = False
    try:
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        verify_authentication()
        verify_delivery_replay(connection)
        rows = connection.execute(
            "SELECT operation_key, request_json, sender, receiving_hub, storage_profile, "
            "ordinal, stage FROM delivery_requests ORDER BY operation_key"
        ).fetchall()
        for _, raw, sender, receiver, _, _, _ in rows:
            request = json.loads(raw)
            forwarded = parse_hub_qualified(sender)
            if (
                receiver is not None
                and receiver != hub_id
                or (forwarded is None and request["origin_hub"] != hub_id)
            ):
                raise DeliveryRefusal(
                    "hub_identity_mismatch", "delivery journal belongs to a different stable hub id"
                )
            if forwarded is not None and forwarded.hub_id != request["origin_hub"]:
                raise DeliveryRefusal("replay_incompatible", "forwarded delivery origin disagrees")
        count = 0
        for key, _, _, receiver, profile, ordinal, stage in rows:
            if receiver is not None:
                continue
            if profile != 3:
                raise DeliveryRefusal(
                    "replay_incompatible", "unbound delivery profile is incompatible"
                )
            quarantine = connection.execute(
                "SELECT reason_code FROM delivery_quarantine WHERE operation_key = ?", (key,)
            ).fetchone()
            released = (
                "unauthorised_requester"
                if retry_authority_refusals and quarantine == ("unauthorised_requester",)
                else None
            )
            payload = {
                "profile": 4,
                "operation_key": key,
                "ordinal": ordinal + 1,
                "prior_stage": stage,
                "receiving_hub": hub_id,
                "recovery_ref": recovery_ref,
                "released_quarantine_reason": released,
            }
            event_seq = insert_event(
                time.time(),
                DELIVERY_OWNER_BOUND,
                json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
            )
            connection.execute(
                "UPDATE delivery_requests SET receiving_hub = ?, storage_profile = 4, "
                "ordinal = ?, latest_event_seq = ? WHERE operation_key = ?",
                (hub_id, ordinal + 1, event_seq, key),
            )
            if released is not None:
                connection.execute(
                    "DELETE FROM delivery_quarantine WHERE operation_key = ?", (key,)
                )
            count += 1
        verify_delivery_replay(connection)
        connection.commit()
        committed = True
        return count
    except BaseException:
        if not committed:
            connection.rollback()
        raise
    finally:
        try:
            connection.execute("PRAGMA synchronous=NORMAL")
        except BaseException:
            if not committed:
                raise
            logger.exception("Could not restore SQLite synchronous=NORMAL")
