# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — a peer hub reserves, settles and recovers on a real owner hub (F02)
"""A real owner hub serves a signing peer's reservations; everyone else gets the same refusal.

The owner hub requires identity binding, and its serving grant names the peer's identity
key, as behind a TLS-terminating proxy. The peer uses the real transport. Refusals must
be indistinguishable whether the peer lacks a grant, the hub has no ledger, or the pool
refuses.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hub_e2e_helpers import running_hub
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.identity_binding import load_identity_trust_bundle
from synapse_channel.core.identity_keys import (
    generate_signing_key,
    public_key_b64,
    write_signing_key,
)
from synapse_channel.core.peer_identity import (
    PeerRegistrationSigner,
    load_peer_registration_signer,
    signed,
)
from synapse_channel.core.protocol import MessageType, build_envelope
from synapse_channel.core.spend_ledger import SpendLedger
from synapse_channel.core.spend_transport import (
    SpendTransportError,
    SpendTransportTimeoutError,
    request_spend,
)
from synapse_channel.core.spend_wire import SpendWireError, encode_spend_request
from test_multihub_identity_grant import FOLLOWER, IDENTITY_KEY, OTHER_KEY, _policy

OWNER = "hub-owner"
NOT_ADMITTED = {"admitted": False, "reason": "not-admitted"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _pool(**overrides: Any) -> dict[str, Any]:
    now = _now()
    document: dict[str, Any] = {
        "pool_id": "pool-a",
        "owner_hub_id": OWNER,
        "epoch": 1,
        "account_ref": "acct-1",
        "billing_surface": "api",
        "unit": "USD",
        "window_starts_at": (now - timedelta(days=1)).isoformat(),
        "window_ends_at": (now + timedelta(days=1)).isoformat(),
        "price_revision": "price-1",
        "hard_bound": "50",
        "cost_basis": {"tax": "pre_tax", "fixed_fee": "0", "minimum_charge": "0"},
        "limits": {"max_depth": 3, "max_agents": 10, "max_wall_seconds": 3600},
        "grantees": [{"hub": FOLLOWER, "project": "PROJ"}],
        "cause": "e2e pool",
    }
    document.update(overrides)
    return document


def _reserve(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "pool_id": "pool-a",
        "seat": "PROJ/alice",
        "project": "PROJ",
        "task": "t1",
        "operation": "call",
        "key": "k1",
        "unit": "USD",
        "tax": "pre_tax",
        "price_revision": "price-1",
        "upper_bound": "20",
        "depth": 1,
        "wall_seconds": 600,
    }
    document.update(overrides)
    return document


def _material(tmp_path: Path) -> tuple[PeerRegistrationSigner, PeerRegistrationSigner, Any]:
    granted, spare = generate_signing_key(), generate_signing_key()
    trust = tmp_path / "identity-trust.json"
    trust.write_text(
        json.dumps(
            {
                "keys": [
                    {
                        "key_id": IDENTITY_KEY,
                        "public_key": public_key_b64(granted),
                        "senders": [FOLLOWER],
                    },
                    {
                        "key_id": OTHER_KEY,
                        "public_key": public_key_b64(spare),
                        "senders": [FOLLOWER],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    write_signing_key(tmp_path / "granted.pem", granted)  # the peer's key file for CLI tests
    return (
        load_peer_registration_signer(tmp_path / "granted.pem", IDENTITY_KEY),
        PeerRegistrationSigner(spare, OTHER_KEY),
        load_identity_trust_bundle(trust),
    )


def _ledger(tmp_path: Path) -> SpendLedger:
    home = tmp_path / "spend"
    home.mkdir(mode=0o700)
    ledger = SpendLedger(home / "ledger.sqlite3", owner_hub_id=OWNER)
    ledger.configure(_pool(), now=_now())
    return ledger


def _hub(trust: Any, ledger: SpendLedger | None) -> SynapseHub:
    return SynapseHub(
        hub_id=OWNER,
        identity_trust_bundle=trust,
        require_identity_binding=True,
        multihub_serving_policy=_policy(),
        spend_ledger=ledger,
    )


async def test_a_signing_peer_reserves_settles_and_recovers_a_lost_answer(tmp_path: Path) -> None:
    granted, spare, trust = _material(tmp_path)
    ledger = _ledger(tmp_path)
    async with running_hub(_hub(trust, ledger)) as (_hub_ref, uri):

        async def ask(action: str, document: dict[str, Any], signer: Any = granted) -> Any:
            return await request_spend(action, document, uri=uri, local_id=FOLLOWER, signer=signer)

        grant = await ask("reserve", _reserve())
        again = await ask("reserve", _reserve())
        conflict = await ask("reserve", _reserve(upper_bound="21"))
        query = {"pool_id": "pool-a", "seat": "PROJ/alice", "task": "t1", "operation": "call"}
        found = await ask("query", {**query, "key": "k1"})
        missing = await ask("query", {**query, "key": "k2"})
        over = await ask("reserve", _reserve(key="k3", upper_bound="31"))
        settle = {
            "pool_id": "pool-a",
            "reservation_id": grant["reservation_id"],
            "usage_ref": "u1",
            "amount": "12",
            "provenance": "billed",
            "final": True,
        }
        settled = await ask("settle", settle)
        after = await ask("reserve", _reserve(key="k4", upper_bound="38"))
        not_granted = await ask("reserve", _reserve(key="k5"), signer=spare)
        stranger_query = await ask("query", {**query, "key": "k1"}, signer=spare)
    status = ledger.status("pool-a", now=_now())

    assert grant["admitted"] is True and grant["exposure"] == "20"
    assert again == grant
    assert conflict == {"admitted": False, "reason": "idempotency_conflict"}
    assert found == {"found": True, "response": grant}
    assert missing == {"found": False}
    assert over == NOT_ADMITTED
    assert settled["settled"] is True and settled["overrun"] is False
    assert after["admitted"] is True  # 12 settled + 38 = 50
    assert not_granted == NOT_ADMITTED
    assert stranger_query == {"found": False}
    assert (status["settled"], status["outstanding"], status["headroom"]) == ("12", "38", "0")
    reasons = [e["body"]["reason"] for e in ledger.audit("pool-a") if e["kind"] == "refusal"]
    assert reasons == ["bound_exceeded"]  # the unserved peer never reached the ledger


async def test_a_hub_without_a_ledger_refuses_exactly_like_a_pool(tmp_path: Path) -> None:
    granted, _spare, trust = _material(tmp_path)
    async with running_hub(_hub(trust, None)) as (_hub_ref, uri):
        reserve = await request_spend(
            "reserve", _reserve(), uri=uri, local_id=FOLLOWER, signer=granted
        )
        settle = await request_spend(
            "settle", {"pool_id": "p"}, uri=uri, local_id=FOLLOWER, signer=granted
        )
    assert reserve == NOT_ADMITTED
    assert settle == {"settled": False, "reason": "not-admitted"}


async def test_an_unavailable_ledger_fails_closed(tmp_path: Path) -> None:
    granted, _spare, trust = _material(tmp_path)
    ledger = _ledger(tmp_path)
    ledger.path.parent.chmod(0o755)  # no longer owner-only: the ledger refuses to open
    try:
        async with running_hub(_hub(trust, ledger)) as (_hub_ref, uri):
            answer = await request_spend(
                "reserve", _reserve(), uri=uri, local_id=FOLLOWER, signer=granted
            )
    finally:
        ledger.path.parent.chmod(0o700)
    assert answer == NOT_ADMITTED


@contextlib.asynccontextmanager
async def _server(replies: list[str | bytes]) -> AsyncIterator[str]:
    """A real websocket server that answers the first frame with ``replies``, then waits."""

    async def handler(connection: ServerConnection) -> None:
        await connection.recv()
        for reply in replies:
            await connection.send(reply)
        await asyncio.sleep(5)

    async with serve(handler, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


@pytest.mark.parametrize(
    ("replies", "error", "match"),
    [
        ([], SpendTransportTimeoutError, "query by key"),
        (['{"type": "error", "payload": "no"}'], SpendTransportError, "refused the request"),
        (["[1, 2]"], SpendTransportError, "not a JSON object"),
        (['{"type": "spend_result", "spend_action": "settle"}'], SpendTransportError, "failed"),
        (["{not json"], SpendTransportError, "failed"),
    ],
)
async def test_the_transport_fails_closed(
    replies: list[str | bytes], error: type[Exception], match: str
) -> None:
    async with _server(['{"type": "welcome"}', *replies]) as uri:
        with pytest.raises(error, match=match):
            await request_spend("reserve", _reserve(), uri=uri, local_id=FOLLOWER, timeout=0.3)
    with pytest.raises(SpendTransportError, match="failed"):
        await request_spend("reserve", _reserve(), uri="ws://127.0.0.1:9", local_id=FOLLOWER)

    async def interrupt_handshake(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await reader.readuntil(b"\r\n\r\n")
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(interrupt_handshake, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(SpendTransportError, match="failed"):
            await request_spend(
                "reserve", _reserve(), uri=f"ws://127.0.0.1:{port}", local_id=FOLLOWER
            )


async def test_a_malformed_request_and_bytes_frames(tmp_path: Path) -> None:
    granted, _spare, trust = _material(tmp_path)
    with pytest.raises(SpendWireError):
        encode_spend_request("configure", {})
    async with running_hub(_hub(trust, _ledger(tmp_path))) as (_hub_ref, uri):
        async with connect(uri) as socket:
            frame = signed(
                build_envelope(FOLLOWER, MessageType.SPEND_REQUEST, spend_action="configure"),
                granted,
            )
            await socket.send(json.dumps(frame))
            replies: list[dict[str, Any]] = []
            while not any(r.get("type") == "error" for r in replies):
                replies.append(json.loads(await asyncio.wait_for(socket.recv(), 3)))
    assert any("Malformed spend request" in str(r.get("payload")) for r in replies)
    binary = b'{"type": "spend_result", "spend_action": "reserve", "spend_result": {}}'
    async with _server([binary]) as uri:
        answer = await request_spend("reserve", _reserve(), uri=uri, local_id=FOLLOWER, token="t")
    assert answer == {}
