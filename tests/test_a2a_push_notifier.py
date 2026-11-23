# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — characterisation of A2A push delivery timing

from __future__ import annotations

from typing import Any
from urllib.error import URLError

from a2a_server_helpers import RecordingAgent
from synapse_channel.a2a_push import A2APushNotifier
from synapse_channel.a2a_push_delivery import PushDeliveryState
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def _message() -> dict[str, Any]:
    return {
        "taskId": "task-a",
        "messageId": "message-a",
        "role": "ROLE_USER",
        "parts": [{"text": "work"}],
    }


def _bridge(**kwargs: Any) -> A2ABridge:
    return A2ABridge(
        agent=RecordingAgent(),
        agent_card={"name": "SYNAPSE CHANNEL"},
        target="WORKER",
        store=A2ATaskStore(),
        **kwargs,
    )


def test_retry_delays_and_clock_are_recorded_on_each_attempt() -> None:
    """Injected delays and clock values reach the durable delivery evidence."""
    sleeps: list[float] = []
    calls = 0

    def fail(_delivery: dict[str, Any]) -> None:
        nonlocal calls
        calls += 1
        raise URLError("offline")

    bridge = _bridge(
        push_deliverer=fail,
        push_retry_delays_seconds=(0.25, 1.0),
        push_sleep=sleeps.append,
        push_clock=iter((10.0, 11.0, 12.0)).__next__,
    )
    task = bridge.create_working_task(_message())
    assert isinstance(bridge._notifier, A2APushNotifier)
    stored = bridge.create_push_notification_config(
        str(task["id"]),
        {"id": "cfg-a", "webhookUrl": "https://example.test/hook"},
    )
    assert stored is not None

    canceled = bridge.cancel_task(str(task["id"]))

    assert canceled is not None
    evidence = bridge.list_push_notification_deliveries(str(task["id"]))[
        "pushNotificationDeliveries"
    ]
    assert calls == 3
    assert sleeps == [0.25, 1.0]
    assert [attempt["state"] for attempt in evidence] == [
        PushDeliveryState.RETRY_SCHEDULED.value,
        PushDeliveryState.RETRY_SCHEDULED.value,
        PushDeliveryState.DEAD_LETTERED.value,
    ]
    assert [attempt["occurredAt"] for attempt in evidence] == [10.0, 11.0, 12.0]
    assert [attempt["retryDelaySeconds"] for attempt in evidence[:2]] == [0.25, 1.0]


def test_stored_request_config_is_persisted_and_delivered() -> None:
    """A send-time push config is stored and delivered with the injected clock."""
    deliveries: list[dict[str, Any]] = []
    bridge = _bridge(
        push_deliverer=deliveries.append,
        push_retry_delays_seconds=(0.25, 1.0),
        push_sleep=lambda _seconds: None,
        push_clock=lambda: 10.0,
    )
    task = bridge.create_working_task(_message())

    stored = bridge._notifier.store_request_config(
        {
            "configuration": {
                "taskPushNotificationConfig": {
                    "pushNotificationConfig": {
                        "id": "cfg-a",
                        "webhookUrl": "https://example.test/hook",
                    }
                }
            }
        },
        task_id=str(task["id"]),
    )

    assert stored is not None
    assert stored["webhookUrl"] == "https://example.test/hook"
    configs = bridge.list_push_notification_configs(str(task["id"]))["pushNotificationConfigs"]
    assert [config["id"] for config in configs] == ["cfg-a"]
    assert len(deliveries) == 1
    assert deliveries[0]["url"] == "https://example.test/hook"
    evidence = bridge.list_push_notification_deliveries(str(task["id"]))[
        "pushNotificationDeliveries"
    ]
    assert [attempt["state"] for attempt in evidence] == [PushDeliveryState.SUCCEEDED.value]
    assert evidence[0]["occurredAt"] == 10.0
