# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end journeys for the ``synapse worker`` long-running responder.

The worker connects to a hub, advertises itself, and answers chat with a model
provider. These journeys drive the packaged command as a subprocess against an
isolated hub using the offline, deterministic ``rule`` provider — so no model
credentials or network are involved — and observe its behaviour on the durable
event log the hub writes, exactly as an operator auditing the bus afterwards
would. They prove the worker replies when addressed and stays silent otherwise.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from cli_e2e_helpers import isolated_hub, isolated_worker, run_cli

_RULE_ACK = "message received via Synapse"


def _chat_events(db_path: Path) -> list[dict[str, object]]:
    """Return the chat events on the durable log as decoded payloads."""
    drained = run_cli("ingest", str(db_path), "--kind", "chat")
    assert drained.ok(), drained.output
    events = []
    for line in drained.stdout.splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line)["payload"])
    return events


def _await_worker_replies(db_path: Path, sender: str, *, timeout: float = 8.0) -> list[str]:
    """Poll the durable log until ``sender`` has spoken, returning its chat texts."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        texts = [e["payload"] for e in _chat_events(db_path) if e.get("sender") == sender]
        if texts:
            return [str(t) for t in texts]
        time.sleep(0.1)
    return []


def test_worker_rule_provider_acknowledges_a_directed_message(tmp_path: Path) -> None:
    with isolated_hub(tmp_path) as hub, isolated_worker(hub.uri, name="BOT") as bot:
        sent = run_cli("send", "BOT are you there?", "--name", "USER", "--target", bot, uri=hub.uri)
        assert sent.ok(), sent.output

        replies = _await_worker_replies(hub.db_path, sender=bot)
        assert replies, "worker did not acknowledge on the bus"
        assert any(_RULE_ACK in text for text in replies)


def test_worker_ignores_unaddressed_chatter(tmp_path: Path) -> None:
    with isolated_hub(tmp_path) as hub, isolated_worker(hub.uri, name="BOT") as bot:
        # Chatter that neither targets nor names the worker, from a non-USER peer:
        # the worker's reply filter should let it pass in silence.
        ignored = run_cli(
            "send", "just thinking out loud", "--name", "ALICE", "--target", "all", uri=hub.uri
        )
        assert ignored.ok(), ignored.output

        # A directly addressed message the worker must answer — its reply is the
        # sync point that proves the earlier chatter has already been processed.
        pinged = run_cli("send", "BOT status?", "--name", "USER", "--target", bot, uri=hub.uri)
        assert pinged.ok(), pinged.output

        replies = _await_worker_replies(hub.db_path, sender=bot)
        assert len(replies) == 1, f"expected exactly one reply, got {replies}"
        assert _RULE_ACK in replies[0]
