# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — parse edges: deep mirrors, unreadable cards, expired receipt identity
"""Bound deep mirror JSON, name unreadable capability cards, keep a receipt's client id."""

from __future__ import annotations

from pathlib import Path

import pytest

from synapse_channel.core.capability_card_signing import (
    CapabilityCardSigningError,
    load_capability_card_json,
)
from synapse_channel.core.delivery_receipts import expired_receipt_payload
from synapse_channel.core.pending_receipts import ReceiptEntry
from synapse_channel.fleet_mirror_contract import parse_mirror


def test_mirror_nested_beyond_the_parser_limit_is_a_bounded_value_error() -> None:
    """A mirror nested past the interpreter's recursion limit is refused, not crashed on."""
    deep = "[" * 100_000 + "]" * 100_000
    with pytest.raises(ValueError, match="^mirror nesting or number exceeds limit$") as caught:
        parse_mirror(deep)
    assert isinstance(caught.value.__cause__, RecursionError)


def test_capability_card_that_is_not_utf8_is_named_unreadable(tmp_path: Path) -> None:
    """Undecodable card bytes are reported against the card path."""
    card = tmp_path / "card.json"
    card.write_bytes(b"\xff\xfe{}")
    with pytest.raises(CapabilityCardSigningError, match="^cannot read capability card .*utf-8"):
        load_capability_card_json(card)


def test_capability_card_reached_through_a_link_is_unreadable_not_missing(
    tmp_path: Path,
) -> None:
    """A link that cannot be opened securely exists, so it is never called 'does not exist'."""
    target = tmp_path / "card.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "card-link.json"
    link.symlink_to(target)
    with pytest.raises(CapabilityCardSigningError) as caught:
        load_capability_card_json(link)
    assert str(caught.value).startswith(f"cannot read capability card {link}: ")
    assert "cannot securely open" in str(caught.value)
    assert "does not exist" not in str(caught.value)


@pytest.mark.parametrize(("client_msg_id", "carried"), [("cli-42", True), ("", False)])
def test_expired_receipt_carries_the_client_message_id_only_when_present(
    client_msg_id: str, carried: bool
) -> None:
    """An expiring deferred receipt keeps the sender's own id so the client can correlate it."""
    entry = ReceiptEntry("P/sender", "P/target", 7, client_msg_id)
    payload = expired_receipt_payload(entry=entry, message_seq=11, reason="window")
    assert payload["expired"] is True
    assert payload["message_seq"] == 11
    assert ("client_msg_id" in payload) is carried
    assert payload.get("client_msg_id", "") == client_msg_id
