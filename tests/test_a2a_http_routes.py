# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real HTTP negotiated route and push-configuration lifecycles
from __future__ import annotations

import time

import pytest

from test_a2a_http_handler import edge, request


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/tasks"),
        ("POST", "/message:send"),
        ("DELETE", "/tasks/t/pushNotificationConfigs/c"),
    ],
)
def test_unsupported_protocol_version_is_refused_before_route_effects(
    method: str, path: str
) -> None:
    """Version negotiation refuses all verbs before their route effects."""
    with edge() as (bridge, server):
        status, body = request(server, method, path, headers={"A2A-Version": "999.0"})
        assert status == 400
        assert body["error"]["details"][0]["reason"] == "VERSION_NOT_SUPPORTED"
        assert bridge.store.list_tasks() == []


def test_real_push_configuration_create_read_list_delete_has_one_task_scope() -> None:
    """One concrete task keeps the complete push-configuration HTTP lifecycle."""
    with edge() as (bridge, server):
        bridge.store.put(
            {
                "id": "t",
                "contextId": "ctx",
                "status": {"state": "TASK_STATE_WORKING"},
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
        )
        path = "/tasks/t/pushNotificationConfigs"
        status, _ = request(
            server,
            "POST",
            path,
            body=b'{"pushNotificationConfig":{"id":"cfg","webhookUrl":"https://receiver.example/callback"}}',
        )
        assert status == 200
        status, listing = request(server, "GET", path)
        assert status == 200 and len(listing["pushNotificationConfigs"]) == 1
        status, config = request(server, "GET", path + "/cfg")
        assert status == 200 and config["id"] == "cfg"
        status, deleted = request(server, "DELETE", path + "/cfg")
        assert status == 200 and deleted["deleted"] is True
        assert request(server, "GET", path)[1]["pushNotificationConfigs"] == []
