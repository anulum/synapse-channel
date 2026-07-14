# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dedicated tests for the A2A JSON-RPC dispatch surface

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from synapse_channel import a2a_rpc
from synapse_channel.a2a import JsonMap

if TYPE_CHECKING:
    from synapse_channel.a2a_server import A2ABridge


class _FakeBridge:
    """Records the dispatched call and returns a configurable result per method.

    Only the methods :func:`a2a_rpc.dispatch_json_rpc` reaches are implemented; a
    method configured to return ``None`` drives the "unknown task/config"
    not-found branches. A structural stand-in for the concrete ``A2ABridge``.
    """

    def __init__(self, **returns: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.agent_card: JsonMap = {"name": "fake-agent"}
        self._returns: dict[str, Any] = {
            "send_message": {"kind": "message", "messageId": "m1"},
            "stream_message": {"kind": "stream"},
            "get_task": {"id": "t1", "status": {"state": "completed"}},
            "list_tasks": {"tasks": [], "nextPageToken": ""},
            "cancel_task": {"id": "t1", "status": {"state": "canceled"}},
            "create_push_notification_config": {"id": "c1"},
            "list_push_notification_configs": {"pushNotificationConfigs": [{"id": "c1"}]},
            "get_push_notification_config": {"id": "c1"},
            "delete_push_notification_config": {"deleted": True},
        }
        self._returns.update(returns)

    def _record(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))
        return self._returns[name]

    def send_message(self, params: JsonMap, *, protocol_version: str | None = None) -> Any:
        return self._record("send_message", params, protocol_version=protocol_version)

    def stream_message(self, params: JsonMap, *, protocol_version: str | None = None) -> Any:
        return self._record("stream_message", params, protocol_version=protocol_version)

    def get_task(self, task_id: str, *, history_length: int | None) -> Any:
        return self._record("get_task", task_id, history_length=history_length)

    def list_tasks(self, *, state: str | None, page_size: int | None, page_token: str) -> Any:
        return self._record("list_tasks", state=state, page_size=page_size, page_token=page_token)

    def cancel_task(self, task_id: str) -> Any:
        return self._record("cancel_task", task_id)

    def create_push_notification_config(self, task_id: str, config: JsonMap) -> Any:
        return self._record("create_push_notification_config", task_id, config)

    def list_push_notification_configs(self, task_id: str) -> Any:
        return self._record("list_push_notification_configs", task_id)

    def get_push_notification_config(self, task_id: str, config_id: str) -> Any:
        return self._record("get_push_notification_config", task_id, config_id)

    def delete_push_notification_config(self, task_id: str, config_id: str) -> Any:
        return self._record("delete_push_notification_config", task_id, config_id)


def _as_bridge(bridge: _FakeBridge) -> A2ABridge:
    """Present the structural fake as the concrete bridge type the dispatch expects."""
    return cast("A2ABridge", bridge)


def _request(method: str, params: Any = None, *, rpc_id: Any = 1) -> JsonMap:
    body: JsonMap = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _dispatch(bridge: _FakeBridge, method: str, params: Any = None, **kwargs: Any) -> JsonMap:
    return a2a_rpc.dispatch_json_rpc(_as_bridge(bridge), _request(method, params), **kwargs)


def _dispatch_raw(bridge: _FakeBridge, body: JsonMap) -> JsonMap:
    """Dispatch a hand-built body (for malformed-envelope tests)."""
    return a2a_rpc.dispatch_json_rpc(_as_bridge(bridge), body)


# --- envelope validation -----------------------------------------------------


def test_rejects_wrong_jsonrpc_version() -> None:
    result = _dispatch_raw(_FakeBridge(), {"jsonrpc": "1.0", "id": 7, "method": "message/send"})
    assert result == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {"code": -32600, "message": "Invalid Request"},
    }


def test_rejects_non_string_method() -> None:
    result = _dispatch_raw(_FakeBridge(), {"jsonrpc": "2.0", "id": 8, "method": 123})
    assert result["error"]["code"] == -32600
    assert result["id"] == 8


def test_none_params_is_treated_as_empty_mapping() -> None:
    bridge = _FakeBridge()
    result = _dispatch_raw(
        bridge,
        {"jsonrpc": "2.0", "id": 9, "method": "message/send", "params": None},
    )
    assert result["result"] == {"kind": "message", "messageId": "m1"}
    assert bridge.calls[0] == ("send_message", ({},), {"protocol_version": None})


def test_rejects_non_dict_params() -> None:
    result = _dispatch_raw(
        _FakeBridge(),
        {"jsonrpc": "2.0", "id": 10, "method": "message/send", "params": ["not", "a", "dict"]},
    )
    assert result["error"] == {"code": -32602, "message": "Invalid params"}


def test_unknown_method_is_method_not_found() -> None:
    result = _dispatch(_FakeBridge(), "tasks/teleport", {})
    assert result["error"]["code"] == -32601
    assert result["error"]["message"] == "Method not found"


def test_value_error_from_method_becomes_invalid_params() -> None:
    # get_task returning None raises A2ANotFoundError (a ValueError) → -32602 with its message.
    result = _dispatch(_FakeBridge(get_task=None), "tasks/get", {"taskId": "ghost"})
    assert result["error"]["code"] == -32602
    assert "Unknown task: ghost" in result["error"]["message"]


# --- method routing (happy paths) --------------------------------------------


def test_message_send_passes_params_and_protocol_version() -> None:
    bridge = _FakeBridge()
    result = _dispatch(
        bridge, "message/send", {"message": {"role": "user"}}, protocol_version="0.3.0"
    )
    assert result == {"jsonrpc": "2.0", "id": 1, "result": {"kind": "message", "messageId": "m1"}}
    assert bridge.calls[0] == (
        "send_message",
        ({"message": {"role": "user"}},),
        {"protocol_version": "0.3.0"},
    )


def test_message_stream_is_dispatched() -> None:
    bridge = _FakeBridge()
    result = _dispatch(bridge, "message/stream", {"message": {}})
    assert result["result"] == {"kind": "stream"}
    assert bridge.calls[0][0] == "stream_message"


def test_tasks_get_parses_history_length() -> None:
    bridge = _FakeBridge()
    result = _dispatch(bridge, "tasks/get", {"id": "t1", "historyLength": "5"})
    assert result["result"] == {"id": "t1", "status": {"state": "completed"}}
    assert bridge.calls[0] == ("get_task", ("t1",), {"history_length": 5})


def test_tasks_get_without_history_length_passes_none() -> None:
    bridge = _FakeBridge()
    _dispatch(bridge, "tasks/get", {"taskId": "t1"})
    assert bridge.calls[0] == ("get_task", ("t1",), {"history_length": None})


def test_tasks_list_maps_all_filters() -> None:
    bridge = _FakeBridge()
    _dispatch(bridge, "tasks/list", {"status": "working", "pageSize": "20", "pageToken": "next"})
    assert bridge.calls[0] == (
        "list_tasks",
        (),
        {"state": "working", "page_size": 20, "page_token": "next"},
    )


def test_tasks_list_defaults_when_filters_absent() -> None:
    bridge = _FakeBridge()
    _dispatch(bridge, "tasks/list", {})
    assert bridge.calls[0] == (
        "list_tasks",
        (),
        {"state": None, "page_size": None, "page_token": ""},
    )


def test_tasks_cancel_returns_task() -> None:
    bridge = _FakeBridge()
    result = _dispatch(bridge, "tasks/cancel", {"taskId": "t1"})
    assert result["result"]["status"]["state"] == "canceled"


def test_tasks_cancel_unknown_task_is_not_found() -> None:
    result = _dispatch(_FakeBridge(cancel_task=None), "tasks/cancel", {"id": "ghost"})
    assert result["error"]["code"] == -32602
    assert "Unknown task: ghost" in result["error"]["message"]


def test_push_config_set_requires_a_mapping() -> None:
    result = _dispatch(
        _FakeBridge(),
        "tasks/pushNotificationConfig/set",
        {"taskId": "t1", "pushNotificationConfig": "nope"},
    )
    assert result["error"]["code"] == -32602
    assert "pushNotificationConfig is required" in result["error"]["message"]


def test_push_config_set_creates_config() -> None:
    bridge = _FakeBridge()
    result = _dispatch(
        bridge,
        "tasks/pushNotificationConfig/set",
        {"taskId": "t1", "pushNotificationConfig": {"url": "https://x"}},
    )
    assert result["result"] == {"id": "c1"}
    assert bridge.calls[0] == ("create_push_notification_config", ("t1", {"url": "https://x"}), {})


def test_push_config_set_unknown_task_is_not_found() -> None:
    result = _dispatch(
        _FakeBridge(create_push_notification_config=None),
        "tasks/pushNotificationConfig/set",
        {"taskId": "ghost", "pushNotificationConfig": {"url": "https://x"}},
    )
    assert result["error"]["code"] == -32602


def test_push_config_list_unwraps_configs() -> None:
    result = _dispatch(_FakeBridge(), "tasks/pushNotificationConfig/list", {"taskId": "t1"})
    assert result["result"] == [{"id": "c1"}]


def test_push_config_get_returns_config() -> None:
    bridge = _FakeBridge()
    result = _dispatch(
        bridge,
        "tasks/pushNotificationConfig/get",
        {"taskId": "t1", "pushNotificationConfigId": "c1"},
    )
    assert result["result"] == {"id": "c1"}
    assert bridge.calls[0] == ("get_push_notification_config", ("t1", "c1"), {})


def test_push_config_get_unknown_is_not_found() -> None:
    result = _dispatch(
        _FakeBridge(get_push_notification_config=None),
        "tasks/pushNotificationConfig/get",
        {"taskId": "t1", "configId": "ghost"},
    )
    assert result["error"]["code"] == -32602
    assert "Unknown push notification config: ghost" in result["error"]["message"]


def test_push_config_delete_returns_bridge_result() -> None:
    bridge = _FakeBridge()
    result = _dispatch(
        bridge,
        "tasks/pushNotificationConfig/delete",
        {"taskId": "t1", "pushNotificationConfigId": "c1"},
    )
    assert result["result"] == {"deleted": True}
    assert bridge.calls[0] == ("delete_push_notification_config", ("t1", "c1"), {})


def test_authenticated_extended_card_returns_agent_card() -> None:
    bridge = _FakeBridge()
    result = _dispatch(bridge, "agent/getAuthenticatedExtendedCard", {})
    assert result["result"] == {"name": "fake-agent"}


# --- compatibility spelling helpers ------------------------------------------


def test_task_id_prefers_taskid_then_id_then_empty() -> None:
    assert a2a_rpc._task_id({"taskId": "a", "id": "b"}) == "a"
    assert a2a_rpc._task_id({"id": "b"}) == "b"
    assert a2a_rpc._task_id({}) == ""


def test_config_id_prefers_camel_then_alias_then_empty() -> None:
    assert a2a_rpc._config_id({"pushNotificationConfigId": "a", "configId": "b"}) == "a"
    assert a2a_rpc._config_id({"configId": "b"}) == "b"
    assert a2a_rpc._config_id({}) == ""


def test_rpc_id_is_echoed_on_success_and_error() -> None:
    bridge = _FakeBridge()
    ok = _dispatch(bridge, "message/send", {})
    assert ok["id"] == 1
    err = _dispatch_raw(
        bridge, {"jsonrpc": "2.0", "id": "abc", "method": "message/send", "params": 5}
    )
    assert err["id"] == "abc"
