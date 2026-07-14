# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — multihub observe + MCP store joins open SQLCipher stores

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from synapse_channel import cli
from synapse_channel.core.at_rest import generate_key_file
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.persistence_sqlcipher import sqlcipher_available
from synapse_channel.core.state import TaskClaim
from synapse_channel.mcp.advisory_actions import McpAdvisoryActions
from synapse_channel.mcp.bridge import SynapseHubBridge

pytestmark = pytest.mark.skipif(
    not sqlcipher_available(),
    reason="sqlcipher3-binary not installed",
)


def _encrypted_claim_store(tmp_path: Path) -> tuple[Path, Path]:
    key = generate_key_file(tmp_path / "peer.key")
    db = tmp_path / "peer.db"
    store = EventStore(db, key_file=key)
    claim = TaskClaim(
        task_id="T-PEER",
        owner="agent/peer",
        note="multihub sqlcipher probe",
        claimed_at=10.0,
        lease_expires_at=9999.0,
        status="claimed",
        data_ref="",
        worktree="repo",
        paths=("src/x.py",),
        epoch=1,
        checkpoint="",
    )
    store.append(EventKind.CLAIM, claim.as_dict(), ts=10.0, durable=True)
    store.append(
        EventKind.RECALL,
        {
            "author": "agent/peer",
            "task_id": "T-PEER",
            "text": "remembered: packaging release notes for synapse channel",
        },
        ts=11.0,
        durable=True,
    )
    store.close()
    return db, key


def test_multihub_observe_reads_encrypted_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, key = _encrypted_claim_store(tmp_path)
    code = cli.main(
        [
            "multihub",
            "observe",
            "--peer-db",
            str(db),
            "--db-key-file",
            str(key),
            "--json",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "T-PEER" in out or "observed_claims" in out


def test_multihub_observe_without_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db, _key = _encrypted_claim_store(tmp_path)
    code = cli.main(
        [
            "multihub",
            "observe",
            "--peer-db",
            str(db),
            "--json",
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "t-peer" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def test_multihub_observe_wrong_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wrong SQLCipher key must fail closed (not return empty observed state)."""
    db, _key = _encrypted_claim_store(tmp_path)
    wrong = generate_key_file(tmp_path / "wrong.key")
    code = cli.main(
        [
            "multihub",
            "observe",
            "--peer-db",
            str(db),
            "--db-key-file",
            str(wrong),
            "--json",
        ]
    )
    assert code != 0
    err = capsys.readouterr().err.lower()
    assert "t-peer" not in err
    assert any(
        token in err
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def _bare_bridge_with_advisory() -> SynapseHubBridge:
    """``__new__`` bridge for pure local recall (no hub); advisory facade attached."""
    bridge = SynapseHubBridge.__new__(SynapseHubBridge)
    bridge.advisory_actions = McpAdvisoryActions(
        agent=cast(Any, None),
        await_reply=cast(Any, None),
    )
    return bridge


@pytest.mark.asyncio
async def test_mcp_memory_recall_reads_encrypted_store(tmp_path: Path) -> None:
    db, key = _encrypted_claim_store(tmp_path)
    bridge = _bare_bridge_with_advisory()  # no hub connection needed
    out = await SynapseHubBridge.memory_recall(
        bridge,
        str(db),
        "packaging",
        limit=5,
        since_seq=0,
        event_store_key_file=str(key),
    )
    assert "file is not a database" not in out.lower()
    # Success returns JSON (hits optional); key open must not fail closed.
    assert "missing event store" not in out.lower()
    assert not out.lower().startswith("sqlcipher")
    assert "db-key-file" not in out.lower() or "packaging" in out.lower() or "{" in out


@pytest.mark.asyncio
async def test_mcp_memory_recall_without_key_fails_closed(tmp_path: Path) -> None:
    db, _key = _encrypted_claim_store(tmp_path)
    bridge = _bare_bridge_with_advisory()
    out = await SynapseHubBridge.memory_recall(
        bridge,
        str(db),
        "packaging",
        limit=5,
        since_seq=0,
    )
    text = out.lower()
    assert "packaging release notes" not in text
    assert any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


@pytest.mark.asyncio
async def test_mcp_memory_recall_wrong_key_fails_closed(tmp_path: Path) -> None:
    db, _key = _encrypted_claim_store(tmp_path)
    wrong = generate_key_file(tmp_path / "wrong-mem.key")
    bridge = _bare_bridge_with_advisory()
    out = await SynapseHubBridge.memory_recall(
        bridge,
        str(db),
        "packaging",
        limit=5,
        since_seq=0,
        event_store_key_file=str(wrong),
    )
    text = out.lower()
    assert "packaging release notes" not in text
    assert any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


def _stub_bridge_for_route_task() -> SynapseHubBridge:
    """Shipped bridge with hub snapshot replies faked (store open still real)."""
    from synapse_channel.core.protocol import MessageType

    task = {
        "task_id": "T1",
        "title": "Python routing cleanup",
        "description": "Improve deterministic route fallback.",
        "status": "open",
    }
    replies: list[dict[str, Any]] = [
        {
            "type": MessageType.BOARD_SNAPSHOT,
            "board": {"tasks": [task]},
        },
        {"type": MessageType.MANIFEST_SNAPSHOT, "manifest": []},
        {"type": MessageType.STATE_SNAPSHOT, "snapshot": {"resources": []}},
    ]

    class _Agent:
        async def request_board(self) -> None:
            return None

        async def request_manifest(self) -> None:
            return None

        async def request_state(self) -> None:
            return None

    bridge = SynapseHubBridge.__new__(SynapseHubBridge)
    bridge.agent = _Agent()  # type: ignore[assignment]
    idx = {"i": 0}

    async def _await_reply(
        predicate: Callable[[dict[str, Any]], bool],
        request: Callable[[], Awaitable[None]],
    ) -> dict[str, Any] | None:
        await request()
        item = replies[idx["i"]]
        idx["i"] += 1
        return item if predicate(item) else None

    cast(Any, bridge)._await_reply = _await_reply

    async def await_reply(
        predicate: Callable[[dict[str, Any]], bool],
        request: Callable[[], Awaitable[None]],
    ) -> dict[str, Any] | None:
        return await bridge._await_reply(predicate, request)

    bridge.advisory_actions = McpAdvisoryActions(bridge.agent, await_reply)
    return bridge


@pytest.mark.asyncio
async def test_mcp_route_task_observation_store_without_key_fails_closed(
    tmp_path: Path,
) -> None:
    """Encrypted observation store without key returns an error string, not empty ranks."""
    db, _key = _encrypted_claim_store(tmp_path)
    bridge = _stub_bridge_for_route_task()
    out = await bridge.route_task("T1", event_store=str(db))
    text = out.lower()
    # Must not silently rank as if observations were empty success.
    assert any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


@pytest.mark.asyncio
async def test_mcp_route_task_observation_store_wrong_key_fails_closed(
    tmp_path: Path,
) -> None:
    """Wrong SQLCipher key on observation store must fail closed."""
    db, _key = _encrypted_claim_store(tmp_path)
    wrong = generate_key_file(tmp_path / "wrong-route.key")
    bridge = _stub_bridge_for_route_task()
    out = await bridge.route_task("T1", event_store=str(db), event_store_key_file=str(wrong))
    text = out.lower()
    assert "candidates" not in text or any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )
    assert any(
        token in text
        for token in ("key", "sqlcipher", "encrypt", "cipher", "db-key-file", "database")
    )


@pytest.mark.asyncio
async def test_mcp_route_task_observation_store_with_key_succeeds(tmp_path: Path) -> None:
    db, key = _encrypted_claim_store(tmp_path)
    bridge = _stub_bridge_for_route_task()
    out = await bridge.route_task("T1", event_store=str(db), event_store_key_file=str(key))
    text = out.lower()
    assert "file is not a database" not in text
    # Recommendation JSON on success.
    assert "{" in out
