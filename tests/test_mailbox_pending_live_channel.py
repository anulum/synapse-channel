# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live mailbox counting excludes channel chats
"""A channel chat observed live never raises a mailbox pending count."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from synapse_channel.core.journal import EventKind
from synapse_channel.core.mailbox_pending import MailboxPendingTracker
from synapse_channel.core.persistence import EventStore


def test_live_channel_chat_is_not_counted_but_a_direct_one_is(tmp_path: Path) -> None:
    """After counts are materialised, only directed non-channel chats add to them."""
    store = EventStore(tmp_path / "hub.db")
    try:
        tracker = MailboxPendingTracker(store)
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 0}

        channel: dict[str, Any] = {
            "sender": "PEER",
            "target": "PROJ/BOB",
            "type": "chat",
            "payload": "in the ops channel",
            "channel": "ops",
        }
        tracker.observe_chat(store.append(EventKind.CHAT, channel), channel)
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 0}

        direct = {key: value for key, value in channel.items() if key != "channel"}
        tracker.observe_chat(store.append(EventKind.CHAT, direct), direct)
        assert tracker.snapshot(("PROJ/BOB",), lambda _name: ()) == {"PROJ/BOB": 1}
    finally:
        store.close()
