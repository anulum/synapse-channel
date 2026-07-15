# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the A2A HTTP+JSON bridge

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib import request
from urllib.error import URLError

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel import a2a_push, safe_webhook_transport
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore


def test_push_notification_config_lifecycle_routes_use_store() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    harness = HandlerHarness(
        "POST",
        f"/tasks/{task['id']}/pushNotificationConfigs",
        body={
            "pushNotificationConfig": {
                "webhookUrl": "https://example.test/hook",
                "authentication": {"scheme": "Bearer", "credentials": "token"},
            }
        },
    )
    harness.handler.bridge = bridge

    status, created = harness.run()

    assert status == HTTPStatus.OK
    config_id = created["id"]
    assert created["taskId"] == task["id"]
    assert created["webhookUrl"] == "https://example.test/hook"

    list_harness = HandlerHarness("GET", f"/tasks/{task['id']}/pushNotificationConfigs")
    list_harness.handler.bridge = bridge
    list_status, listed = list_harness.run()
    assert list_status == HTTPStatus.OK
    assert listed["pushNotificationConfigs"][0]["id"] == config_id

    get_harness = HandlerHarness(
        "GET",
        f"/tasks/{task['id']}/pushNotificationConfigs/{config_id}",
    )
    get_harness.handler.bridge = bridge
    get_status, fetched = get_harness.run()
    assert get_status == HTTPStatus.OK
    assert fetched["id"] == config_id

    delete_harness = HandlerHarness(
        "DELETE",
        f"/tasks/{task['id']}/pushNotificationConfigs/{config_id}",
    )
    delete_harness.handler.bridge = bridge
    delete_status, deleted = delete_harness.run()
    assert delete_status == HTTPStatus.OK
    assert deleted["deleted"] is True


def test_send_message_stores_push_notification_config_from_request() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())

    response = bridge.send_message(
        {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
            "configuration": {
                "taskPushNotificationConfig": {
                    "pushNotificationConfig": {"webhookUrl": "https://example.test/hook"}
                }
            },
        }
    )

    task_id = response["task"]["id"]
    configs = bridge.list_push_notification_configs(task_id)
    assert configs["pushNotificationConfigs"][0]["webhookUrl"] == "https://example.test/hook"


def test_send_message_delivers_push_notification_to_configured_webhook() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )

    response = bridge.send_message(
        {
            "message": {
                "messageId": "m1",
                "role": "ROLE_USER",
                "parts": [{"text": "hello"}],
            },
            "configuration": {
                "taskPushNotificationConfig": {
                    "pushNotificationConfig": {
                        "webhookUrl": "https://example.test/hook",
                        "authentication": {
                            "scheme": "Bearer",
                            "credentials": "push-token",
                        },
                    }
                }
            },
        }
    )

    assert deliveries == [
        {
            "url": "https://example.test/hook",
            "headers": {"Authorization": "Bearer push-token"},
            "payload": {"task": response["task"]},
        }
    ]


def test_cancel_task_delivers_push_notification_to_stored_configs() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    bridge.create_push_notification_config(
        task["id"],
        {
            "webhookUrl": "https://example.test/hook",
            "authentication": {"scheme": "Bearer", "credentials": "push-token"},
        },
    )

    canceled = bridge.cancel_task(task["id"])

    assert canceled is not None
    assert deliveries == [
        {
            "url": "https://example.test/hook",
            "headers": {"Authorization": "Bearer push-token"},
            "payload": {"task": canceled},
        }
    ]


def test_completion_delivers_push_notification_to_stored_config() -> None:
    deliveries: list[dict[str, Any]] = []
    bridge = A2ABridge(
        agent=RecordingAgent(),
        agent_card={},
        target="WORKER",
        store=A2ATaskStore(),
        push_deliverer=deliveries.append,
    )
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )
    bridge.create_push_notification_config(task["id"], {"webhookUrl": "https://example.test/hook"})

    bridge.handle_synapse_frame(
        {
            "type": "chat",
            "sender": "WORKER",
            "payload": "done",
            "metadata": {"a2aTaskId": task["id"], "a2aContextId": task["contextId"]},
        }
    )

    assert len(deliveries) == 1
    assert deliveries[0]["payload"]["task"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_push_notification_config_rejects_non_http_webhook_url() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )

    try:
        bridge.create_push_notification_config(task["id"], {"webhookUrl": "file:///tmp/hook"})
    except ValueError as exc:
        assert str(exc) == "pushNotificationConfig.webhookUrl must use http or https"
    else:
        raise AssertionError("non-HTTP webhook URL was accepted")


def test_push_notification_config_rejects_missing_webhook_host() -> None:
    bridge = A2ABridge(agent=RecordingAgent(), agent_card={}, target="WORKER", store=A2ATaskStore())
    task = bridge.create_completed_task(
        {
            "messageId": "m1",
            "role": "ROLE_USER",
            "parts": [{"text": "hello"}],
        },
        target="WORKER",
    )

    try:
        bridge.create_push_notification_config(task["id"], {"webhookUrl": "https:///hook"})
    except ValueError as exc:
        assert str(exc) == "pushNotificationConfig.webhookUrl must include a host"
    else:
        raise AssertionError("hostless webhook URL was accepted")


# --- SSRF-guard branches in the webhook target validator -----------------------


def test_validate_webhook_target_rejects_each_unsafe_shape() -> None:
    """Every rejection branch names its reason: scheme, host, localhost, port."""
    with pytest.raises(URLError, match="must use http or https"):
        a2a_push._validate_webhook_target("ftp://example.org/hook")
    with pytest.raises(URLError, match="must include a host"):
        a2a_push._validate_webhook_target("http:///hook")
    with pytest.raises(URLError, match="local network"):
        a2a_push._validate_webhook_target("http://LOCALHOST/hook")
    with pytest.raises(URLError, match="invalid port"):
        a2a_push._validate_webhook_target("http://example.org:99999/hook")


def test_http_push_deliverer_opens_the_safe_transport_and_bounds_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deliverer POSTs through the safe transport opener and bounds the read."""
    calls: list[object] = []

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, amount: int) -> bytes:
            calls.append(("read", amount))
            return b""

    class _Opener:
        def open(self, req: request.Request, timeout: float) -> _Response:
            calls.append(req.full_url)
            return _Response()

    monkeypatch.setattr(a2a_push, "_validate_webhook_target", lambda url, **kwargs: None)
    monkeypatch.setattr(
        "synapse_channel.a2a_push.safe_webhook_transport.build_safe_opener",
        lambda **kwargs: _Opener(),
    )
    a2a_push.http_push_deliverer(
        {"url": "http://public.example/hook", "headers": {}, "payload": {"task": {}}}
    )
    assert calls == [
        "http://public.example/hook",
        ("read", safe_webhook_transport.WEBHOOK_MAX_RESPONSE_BYTES),
    ]


def test_build_push_delivery_skips_incomplete_authentication() -> None:
    """An authentication block missing scheme or credentials adds no header."""
    delivery = a2a_push.build_push_delivery(
        task={"id": "T1"},
        config={"webhookUrl": "http://public.example/hook", "authentication": {"scheme": "Bearer"}},
    )
    assert delivery["headers"] == {}
    complete = a2a_push.build_push_delivery(
        task={"id": "T1"},
        config={
            "webhookUrl": "http://public.example/hook",
            "authentication": {"scheme": "Bearer", "credentials": "tok"},
        },
    )
    assert complete["headers"] == {"Authorization": "Bearer tok"}
