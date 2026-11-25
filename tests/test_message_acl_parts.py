# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — an enforced message ACL authorises every part of a target
"""An enforced message ACL authorises every part of a target, and a glob only by containment.

A chat target is a comma list, and delivery reaches each part. These tests serve a
hub that enforces an ACL and drive real sockets through it. Every part must be
granted, or the frame is refused before it reaches anyone. A glob part is granted
only when everything it can match is granted, never because its text happens to
match a rule. A seat on a peer hub is its own target kind, so a grant for local
seats never authorises forwarding. The delivery-request verb is gated the same
way, and the access mapper splits a target exactly as delivery does.
"""

from __future__ import annotations

import json
import time
from fnmatch import fnmatchcase
from typing import Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from hub_e2e_helpers import read_json, read_until_type, running_hub
from synapse_channel.core.acl import MESSAGE, AclPolicy, AclRule
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.protocol import MessageType, is_recipient
from synapse_channel.core.verb_access import message_access


def _hub(*rules: tuple[str, str]) -> SynapseHub:
    """Return a hub that enforces an ACL granting ``PROJ`` senders the given message rules."""
    policy = AclPolicy(
        [AclRule(MESSAGE, kind, pattern, "PROJ", f"grant {pattern}") for kind, pattern in rules]
    )
    return SynapseHub(hub_id="acl-parts", acl_policy=policy, require_acl=True)


async def _seat(uri: str, name: str, *, version: int = 1) -> ClientConnection:
    """Open a socket and register ``name`` on it."""
    websocket = await connect(uri)
    await read_until_type(websocket, MessageType.WELCOME)
    await websocket.send(
        json.dumps(
            {
                "sender": name,
                "type": MessageType.HEARTBEAT,
                "target": "System",
                "payload": "online",
                "protocol_version": version,
            }
        )
    )
    return websocket


async def _outcome(websocket: ClientConnection, payload: str) -> dict[str, Any]:
    """Read until the sender sees its own chat or an ACL refusal, and return that frame."""
    for _ in range(50):
        frame = await read_json(websocket)
        if frame.get("type") == MessageType.CHAT and frame.get("payload") == payload:
            return frame
        if frame.get("type") == "error":
            return frame
    raise TimeoutError(f"no outcome for {payload!r}")


async def _chat(websocket: ClientConnection, target: str, payload: str) -> dict[str, Any]:
    """Send a chat from ``PROJ/a`` and return its outcome."""
    await websocket.send(
        json.dumps(
            {"sender": "PROJ/a", "type": MessageType.CHAT, "target": target, "payload": payload}
        )
    )
    return await _outcome(websocket, payload)


def _refused(frame: dict[str, Any]) -> bool:
    """Return whether ``frame`` is the hub's ACL refusal."""
    return frame.get("type") == "error" and frame.get("acl_decision") == "would_deny"


async def _payloads(websocket: ClientConnection, until: str) -> list[str]:
    """Return every chat payload on ``websocket`` up to and including ``until``."""
    seen: list[str] = []
    for _ in range(100):
        frame = await read_json(websocket)
        if frame.get("type") == MessageType.CHAT:
            seen.append(str(frame.get("payload")))
            if frame.get("payload") == until:
                return seen
    raise TimeoutError(f"the chat {until!r} did not arrive")


@pytest.mark.parametrize(
    "target",
    ["PROJ/x,OTHER/victim", "PROJ/x,*", "PROJ/x, OTHER/victim", "PROJ/x,,OTHER/victim"],
)
async def test_a_target_with_an_ungranted_part_is_refused_and_reaches_nobody(target: str) -> None:
    """A grant for ``PROJ/*`` never carries a chat to a part outside it."""
    async with running_hub(_hub(("agent", "PROJ/*"))) as (_, uri):
        sender = await _seat(uri, "PROJ/a")
        victim = await _seat(uri, "OTHER/victim")
        try:
            refused = await _chat(sender, target, "smuggled")
            assert _refused(refused), refused
            granted = await _chat(sender, "PROJ/x", "granted")
            assert granted["type"] == MessageType.CHAT
            assert "smuggled" not in await _payloads(victim, until="granted")
        finally:
            await sender.close()
            await victim.close()


@pytest.mark.parametrize(
    ("rule", "target", "granted"),
    [
        ("PROJ/?", "PROJ/*", False),
        ("PROJ/*", "PROJ/a*", True),
        ("PROJ/*", "S*/*", False),
        ("PROJ/*", "*", False),
        ("*", "*", True),
        ("PROJ/?x", "PROJ/*x", False),
        ("PROJ/[ab]", "PROJ/[ab]", True),
        ("P?OJ/*", "PROJ/a*", False),
        ("PROJ/*x", "PROJ/a*", False),
        ("PROJ/*x", "PROJ/[ab]*", False),
    ],
)
async def test_a_glob_part_is_granted_only_when_its_matches_are_granted(
    rule: str, target: str, granted: bool
) -> None:
    """A glob part passes only when every address it can reach is inside the rule."""
    async with running_hub(_hub(("agent", rule))) as (_, uri):
        sender = await _seat(uri, "PROJ/a")
        try:
            outcome = await _chat(sender, target, "glob")
        finally:
            await sender.close()
    assert _refused(outcome) is not granted, outcome


async def test_a_granted_target_with_a_trailing_comma_is_delivered() -> None:
    """A blank part is no address, so it needs no grant and cannot refuse a granted target."""
    async with running_hub(_hub(("agent", "PROJ/*"))) as (_, uri):
        sender = await _seat(uri, "PROJ/a")
        try:
            outcome = await _chat(sender, "PROJ/x,", "trailing comma")
        finally:
            await sender.close()
    assert outcome["type"] == MessageType.CHAT, outcome


@pytest.mark.parametrize(
    ("rules", "acl_refused"),
    [
        ((("agent", "PROJ/*"),), True),
        ((("agent", "PROJ/*"), ("hub_agent", "PROJ/x@PEER")), False),
    ],
    ids=["local-grant-only", "hub-agent-grant"],
)
async def test_a_seat_on_a_peer_hub_needs_its_own_grant(
    rules: tuple[tuple[str, str], ...], acl_refused: bool
) -> None:
    """A grant for local seats does not authorise a chat to the same name on a peer hub."""
    async with running_hub(_hub(*rules)) as (_, uri):
        sender = await _seat(uri, "PROJ/a")
        try:
            outcome = await _chat(sender, "PROJ/x@PEER", "forwarded")
        finally:
            await sender.close()
    assert _refused(outcome) is acl_refused, outcome


async def test_the_delivery_request_verb_authorises_every_part() -> None:
    """A delivery request to a list with an ungranted part is refused by the ACL."""
    async with running_hub(_hub(("agent", "PROJ/*"))) as (_, uri):
        sender = await _seat(uri, "PROJ/a", version=3)
        try:
            await sender.send(
                json.dumps(
                    {
                        "sender": "PROJ/a",
                        "type": MessageType.DELIVERY_REQUEST,
                        "target": "PROJ/x,OTHER/victim",
                        "protocol_version": 3,
                        "request_id": "req-1",
                        "idempotency_key": "idem-1",
                        "target_incarnation": "0" * 32,
                        "mode": "follow_up",
                        "allowed_fallbacks": ["next_turn"],
                        "task_id": "T-1",
                        "body": "deliver",
                        "deadline": time.time() + 120,
                    }
                )
            )
            outcome = await read_until_type(sender, "error")
        finally:
            await sender.close()
    assert _refused(outcome), outcome


_ODD_TARGETS = [
    "PROJ/x,OTHER/y",
    " PROJ/x , OTHER/y ",
    "PROJ/x,,OTHER/y",
    "PROJ/x,",
    "\tPROJ/x\t,OTHER/y",
    ",",
    "all",
    "",
    "PROJ",
]
_NAMES = ["PROJ/x", "OTHER/y", "PROJ/z", "all", "PROJ"]


@pytest.mark.parametrize("target", _ODD_TARGETS)
def test_the_message_access_splits_a_target_exactly_as_delivery_does(target: str) -> None:
    """Every name delivery reaches is matched by one authorised part, and no other name."""
    values = [access.value for _permission, access in message_access({"target": target})]
    for name in _NAMES:
        covered = any(
            fnmatchcase(name, value) or fnmatchcase(name.split("/", 1)[0], value)
            for value in values
        )
        broadcast = target.strip() in ("", "all")
        assert is_recipient(target, name) == (covered or broadcast), (target, name, values)
