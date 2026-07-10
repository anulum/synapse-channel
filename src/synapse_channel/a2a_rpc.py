# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A JSON-RPC dispatch
"""Dispatch the compatibility JSON-RPC surface onto an A2A bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from synapse_channel import a2a_errors
from synapse_channel.a2a import JsonMap
from synapse_channel.a2a_http_protocol import non_negative_int

if TYPE_CHECKING:
    from synapse_channel.a2a_server import A2ABridge


def dispatch_json_rpc(
    bridge: A2ABridge,
    request_body: JsonMap,
    *,
    protocol_version: str | None = None,
) -> JsonMap:
    """Dispatch one JSON-RPC 2.0 request onto ``bridge``."""
    rpc_id = request_body.get("id")
    if request_body.get("jsonrpc") != "2.0" or not isinstance(request_body.get("method"), str):
        return _rpc_error(rpc_id, -32600, "Invalid Request")
    params = request_body.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _rpc_error(rpc_id, -32602, "Invalid params")
    method = str(request_body["method"])
    try:
        result = _dispatch_method(
            bridge,
            method,
            params,
            protocol_version=protocol_version,
        )
    except KeyError:
        return _rpc_error(rpc_id, -32601, "Method not found")
    except ValueError as exc:
        return _rpc_error(rpc_id, -32602, str(exc))
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _dispatch_method(
    bridge: A2ABridge,
    method: str,
    params: JsonMap,
    *,
    protocol_version: str | None,
) -> object:
    """Return the result object for one supported method."""
    if method == "message/send":
        return bridge.send_message(params, protocol_version=protocol_version)
    if method == "message/stream":
        return bridge.stream_message(params, protocol_version=protocol_version)
    if method == "tasks/get":
        task_id = _task_id(params)
        history_length = params.get("historyLength")
        task = bridge.get_task(
            task_id,
            history_length=(
                non_negative_int(history_length) if history_length is not None else None
            ),
        )
        if task is None:
            raise a2a_errors.A2ANotFoundError(f"Unknown task: {task_id}")
        return task
    if method == "tasks/list":
        state = params.get("status")
        page_size = params.get("pageSize")
        return bridge.list_tasks(
            state=str(state) if state else None,
            page_size=non_negative_int(page_size) if page_size is not None else None,
            page_token=str(params.get("pageToken") or ""),
        )
    if method == "tasks/cancel":
        task_id = _task_id(params)
        task = bridge.cancel_task(task_id)
        if task is None:
            raise a2a_errors.A2ANotFoundError(f"Unknown task: {task_id}")
        return task
    if method == "tasks/pushNotificationConfig/set":
        task_id = _task_id(params)
        config = params.get("pushNotificationConfig")
        if not isinstance(config, dict):
            raise a2a_errors.A2AValidationError("pushNotificationConfig is required")
        created = bridge.create_push_notification_config(task_id, config)
        if created is None:
            raise a2a_errors.A2ANotFoundError(f"Unknown task: {task_id}")
        return created
    if method == "tasks/pushNotificationConfig/list":
        return bridge.list_push_notification_configs(_task_id(params))["pushNotificationConfigs"]
    if method == "tasks/pushNotificationConfig/get":
        task_id = _task_id(params)
        config_id = _config_id(params)
        config = bridge.get_push_notification_config(task_id, config_id)
        if config is None:
            raise a2a_errors.A2ANotFoundError(f"Unknown push notification config: {config_id}")
        return config
    if method == "tasks/pushNotificationConfig/delete":
        return bridge.delete_push_notification_config(
            _task_id(params),
            _config_id(params),
        )
    if method == "agent/getAuthenticatedExtendedCard":
        return bridge.agent_card
    raise KeyError(method)


def _task_id(params: JsonMap) -> str:
    """Read a task id from either supported compatibility spelling."""
    return str(params.get("taskId") or params.get("id") or "")


def _config_id(params: JsonMap) -> str:
    """Read a push-config id from either supported compatibility spelling."""
    return str(params.get("pushNotificationConfigId") or params.get("configId") or "")


def _rpc_error(rpc_id: object, code: int, message: str) -> JsonMap:
    """Build a JSON-RPC error response."""
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
