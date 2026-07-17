# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the fail-closed hub journal recovery gate

from __future__ import annotations

from typing import Any

from synapse_channel.core.event_row_recovery import CorruptEventRow, decode_event_row
from synapse_channel.core.hub_journal_recovery_gate import HubJournalRecoveryGate
from synapse_channel.core.protocol import MessageType


def _marker(seq: int = 4) -> CorruptEventRow:
    marker = decode_event_row((seq, 1.0, "claim", "secret-not-json")).corruption
    assert marker is not None
    return marker


def _system(payload: str, **extra: Any) -> dict[str, Any]:
    extra["type"] = extra.pop("msg_type", MessageType.SYSTEM)
    return {"payload": payload, **extra}


async def test_empty_gate_allows_mutations() -> None:
    sent: list[dict[str, Any]] = []

    async def send_json(_socket: Any, message: dict[str, Any]) -> None:
        sent.append(message)

    gate = HubJournalRecoveryGate((), send_json=send_json, system=_system)

    assert await gate.refuse_mutation("alice", MessageType.CLAIM, object()) is False
    assert sent == []


async def test_degraded_gate_keeps_queries_available() -> None:
    sent: list[dict[str, Any]] = []

    async def send_json(_socket: Any, message: dict[str, Any]) -> None:
        sent.append(message)

    gate = HubJournalRecoveryGate((_marker(),), send_json=send_json, system=_system)

    assert await gate.refuse_mutation("alice", MessageType.STATE_REQUEST, object()) is False
    assert sent == []


async def test_degraded_gate_privately_refuses_mutation_with_safe_metadata() -> None:
    socket = object()
    deliveries: list[tuple[Any, dict[str, Any]]] = []

    async def send_json(target: Any, message: dict[str, Any]) -> None:
        deliveries.append((target, message))

    gate = HubJournalRecoveryGate((_marker(17),), send_json=send_json, system=_system)

    assert await gate.refuse_mutation("alice", MessageType.CHAT, socket) is True
    assert len(deliveries) == 1
    target, denial = deliveries[0]
    assert target is socket
    assert denial["type"] == MessageType.ERROR
    assert denial["target"] == "alice"
    assert denial["journal_recovery_required"] is True
    assert denial["corrupt_rows"] == 1
    assert denial["first_corrupt_seq"] == 17
    assert "secret-not-json" not in str(denial)
