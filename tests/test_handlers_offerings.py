# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — module-owned capability and resource offering handler tests
"""Exercise offering registration, broadcast, persistence, and rejection paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.handlers import offerings
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.journal import EventKind
from synapse_channel.core.persistence import EventStore
from synapse_channel.core.protocol import MessageType


class _RecordingHub(SynapseHub):
    """Use real hub registries while recording handler delivery side effects."""

    def __init__(
        self,
        *,
        journal: EventStore | None = None,
        max_offers_per_agent: int = 64,
    ) -> None:
        super().__init__(
            hub_id="syn-offerings-test",
            journal=journal,
            max_offers_per_agent=max_offers_per_agent,
        )
        self.sent: list[tuple[Any, dict[str, Any]]] = []
        self.broadcasts: list[dict[str, Any]] = []
        self.remembered: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def _send_json(self, websocket: Any, data: dict[str, Any]) -> None:
        self.sent.append((websocket, data))

    async def _broadcast(self, data: dict[str, Any]) -> None:
        self.broadcasts.append(data)

    def _remember(self, data: dict[str, Any], response: dict[str, Any]) -> None:
        self.remembered.append((data, response))


async def test_advertise_normalizes_and_broadcasts_the_stored_card() -> None:
    """A capability advertisement publishes the exact normalized registry card."""
    hub = _RecordingHub()
    await offerings.handle_advertise(
        hub,
        "WORKER",
        {
            "description": " fast local worker ",
            "skills": [" vision ", 7, ""],
            "task_classes": [" chat ", "reason"],
            "model": " local/model ",
            "contracts": [{"task_class": " chat ", "preconditions": ["ready"]}],
            "meta": {"region": "local"},
        },
        object(),
    )

    card = hub.capabilities.get("WORKER")
    assert card is not None
    assert card.description == "fast local worker"
    assert card.skills == ("vision", "7")
    assert card.task_classes == ("chat", "reason")
    assert card.model == "local/model"
    assert [contract.task_class for contract in card.contracts] == ["chat"]
    assert card.meta == {"region": "local"}
    assert hub.sent == []
    assert len(hub.broadcasts) == 1
    advertised = hub.broadcasts[0]
    assert advertised["type"] == MessageType.CAPABILITY_ADVERTISED
    assert advertised["agent"] == "WORKER"
    assert advertised["card"] == card.as_dict()


async def test_advertise_rejects_non_container_optional_fields_to_safe_defaults() -> None:
    """Non-list tags and non-mapping metadata cannot leak malformed card shapes."""
    hub = _RecordingHub()
    await offerings.handle_advertise(
        hub,
        "MINIMAL",
        {
            "description": 0,
            "skills": "not-a-list",
            "task_classes": ("not", "a", "list"),
            "model": None,
            "meta": ["not-a-mapping"],
        },
        object(),
    )

    card = hub.capabilities.get("MINIMAL")
    assert card is not None
    assert card.description == ""
    assert card.skills == ()
    assert card.task_classes == ()
    assert card.model == ""
    assert card.contracts == ()
    assert card.meta == {}
    assert hub.broadcasts[0]["card"] == card.as_dict()


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "gpu-0"}, id="missing-kind"),
        pytest.param({"kind": "gpu"}, id="missing-name"),
    ],
)
async def test_resource_missing_required_field_is_privately_rejected(
    data: dict[str, Any],
) -> None:
    """A malformed offer returns only a sender-targeted error and changes no state."""
    hub = _RecordingHub()
    websocket = object()

    await offerings.handle_resource(hub, "WORKER", data, websocket)

    assert len(hub.sent) == 1
    sent_socket, error = hub.sent[0]
    assert sent_socket is websocket
    assert error["type"] == MessageType.ERROR
    assert error["target"] == "WORKER"
    assert error["payload"] == "resource offer requires kind+name"
    assert hub.state.resources == {}
    assert hub.broadcasts == []
    assert hub.remembered == []


async def test_resource_alias_fields_are_persisted_before_broadcast_and_quota_rejects(
    tmp_path: Path,
) -> None:
    """Accepted alias fields journal one offer; overflow remains private and inert."""
    store = EventStore(tmp_path / "offerings.db")
    try:
        hub = _RecordingHub(journal=store, max_offers_per_agent=1)
        data: dict[str, Any] = {
            "resource_kind": " gpu ",
            "resource_name": " card-0 ",
            "capacity": "3",
            "meta": {"rack": "A"},
            "idem_key": "offer-1",
        }

        await offerings.handle_resource(hub, "WORKER", data, object())

        key = "WORKER:gpu:card-0"
        offer = hub.state.resources[key]
        assert offer.capacity == 3
        assert offer.meta == {"rack": "A"}
        assert hub.sent == []
        assert len(hub.broadcasts) == 1
        offered = hub.broadcasts[0]
        assert offered["type"] == MessageType.RESOURCE_OFFERED
        assert offered["agent"] == "WORKER"
        assert offered["kind"] == "gpu"
        assert offered["name"] == "card-0"
        assert offered["key"] == key
        assert hub.remembered == [(data, offered)]

        events = store.read_all()
        assert [event.kind for event in events] == [EventKind.RESOURCE]
        assert events[0].payload["agent"] == "WORKER"
        assert events[0].payload["capacity"] == 3
        assert events[0].payload["meta"] == {"rack": "A"}

        overflow_socket = object()
        await offerings.handle_resource(
            hub,
            "WORKER",
            {"kind": "cpu", "name": "node-0"},
            overflow_socket,
        )

        assert len(hub.sent) == 1
        sent_socket, error = hub.sent[0]
        assert sent_socket is overflow_socket
        assert error["type"] == MessageType.ERROR
        assert error["target"] == "WORKER"
        assert error["payload"] == "resource offer quota exceeded"
        assert set(hub.state.resources) == {key}
        assert hub.broadcasts == [offered]
        assert hub.remembered == [(data, offered)]
        assert len(store.read_all()) == 1
    finally:
        store.close()


async def test_resource_primary_fields_work_without_a_journal() -> None:
    """The in-memory contract still records and broadcasts when persistence is off."""
    hub = _RecordingHub(journal=None)
    data: dict[str, Any] = {"kind": "llm", "name": "local", "meta": None}

    await offerings.handle_resource(hub, "WORKER", data, object())

    offer = hub.state.resources["WORKER:llm:local"]
    assert offer.capacity == 1
    assert offer.meta == {}
    assert hub.remembered == [(data, hub.broadcasts[0])]
    assert hub.broadcasts[0]["type"] == MessageType.RESOURCE_OFFERED
    assert hub.sent == []
