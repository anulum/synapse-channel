# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — A2A conformance backend stays out of the production path
"""Request content can never select the canned TCK answers of a production bridge.

The canned residual answers exist only for an official A2A TCK run. They are
provided only by the separate :class:`ConformanceA2ABridge`, chosen once at
start (``a2a-serve --conformance-backend``). These tests drive the shipped HTTP+JSON edge, the
public send/stream methods and the real CLI command.
"""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

import pytest

from a2a_server_helpers import HandlerHarness, RecordingAgent
from synapse_channel import cli, cli_a2a
from synapse_channel.a2a_conformance_backend import ConformanceA2ABridge
from synapse_channel.a2a_scenario_responses import TCK_TEXT_ARTIFACT_BODY
from synapse_channel.a2a_server import A2ABridge
from synapse_channel.a2a_store import A2ATaskStore

V1_HEADERS = {"A2A-Version": "1.0", "Content-Type": "application/json"}

_SCENARIO_SELECTORS: list[tuple[str, dict[str, Any]]] = [
    ("tck-message-id", {"message": {"messageId": "tck-artifact-text-session"}}),
    ("tck-direct-message-id", {"message": {"messageId": "tck-message-response-session"}}),
    ("metadata-a2aScenario", {"message": {"metadata": {"a2aScenario": "artifact-text"}}}),
    ("metadata-synapseScenario", {"message": {"metadata": {"synapseScenario": "artifact-data"}}}),
    ("metadata-scenario", {"message": {"metadata": {"scenario": "artifact-file"}}}),
    ("configuration-a2aScenario", {"configuration": {"a2aScenario": "message-response"}}),
]


def _bridge(*, conformance_backend: bool = False) -> A2ABridge:
    bridge_class = ConformanceA2ABridge if conformance_backend else A2ABridge
    return bridge_class(
        agent=RecordingAgent(),
        agent_card={"name": "conformance-backend-bridge"},
        target="WORKER",
        store=A2ATaskStore(),
    )


def _send_body(selector: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "messageId": "ordinary-message",
        "role": "ROLE_USER",
        "parts": [{"text": "please do the real work"}],
    }
    message.update(selector.get("message", {}))
    body: dict[str, Any] = {"message": message}
    if "configuration" in selector:
        body["configuration"] = selector["configuration"]
    return body


def _http_send(bridge: A2ABridge, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    harness = HandlerHarness("POST", "/message:send", body=body, headers=V1_HEADERS, bridge=bridge)
    status, _headers, raw = harness._request()
    return status, json.loads(raw.decode("utf-8"))


@pytest.mark.parametrize(("label", "selector"), _SCENARIO_SELECTORS)
def test_production_bridge_forwards_every_scenario_selector_over_http(
    label: str, selector: dict[str, Any]
) -> None:
    """Message ids, metadata and configuration cannot produce a canned completion."""
    bridge = _bridge()
    status, body = _http_send(bridge, _send_body(selector))

    assert status == HTTPStatus.OK, label
    assert "message" not in body
    task = body["task"]
    assert task["status"]["state"] != "TASK_STATE_COMPLETED"
    assert not task.get("artifacts")
    assert bridge.agent.messages, "the request must be forwarded into SYNAPSE"
    assert bridge.agent.messages[0][1] == "please do the real work"


@pytest.mark.parametrize(("label", "selector"), _SCENARIO_SELECTORS)
def test_production_stream_forwards_every_scenario_selector(
    label: str, selector: dict[str, Any]
) -> None:
    """The streaming entry point applies the same start-time rule."""
    bridge = _bridge()
    result = bridge.stream_message(_send_body(selector))

    assert "message" not in result, label
    assert result["task"]["status"]["state"] != "TASK_STATE_COMPLETED"
    assert bridge.agent.messages


def test_changing_only_the_message_id_does_not_change_work_semantics() -> None:
    """A TCK-shaped id and an ordinary id produce the same kind of forwarded task."""
    ordinary_bridge, tck_bridge = _bridge(), _bridge()
    _status, ordinary = _http_send(ordinary_bridge, _send_body({}))
    _status, tck = _http_send(
        tck_bridge, _send_body({"message": {"messageId": "tck-artifact-data-x"}})
    )

    assert ordinary["task"]["status"]["state"] == tck["task"]["status"]["state"]
    assert ordinary_bridge.agent.messages == tck_bridge.agent.messages


def test_enabled_conformance_backend_answers_send_and_stream_without_forwarding() -> None:
    """Only the explicit start-time profile returns the canned TCK artifact."""
    bridge = _bridge(conformance_backend=True)
    body = _send_body({"message": {"messageId": "tck-artifact-text-session"}})

    status, sent = _http_send(bridge, body)
    streamed = bridge.stream_message(body)

    assert status == HTTPStatus.OK
    for result in (sent, streamed):
        task = result["task"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task["artifacts"][0]["parts"][0]["text"] == TCK_TEXT_ARTIFACT_BODY
    assert bridge.agent.messages == []
    assert bridge.conformance_backend is True


def test_enabled_conformance_backend_streams_a_direct_message() -> None:
    """The direct-Message scenario is answered on the stream entry point too."""
    bridge = _bridge(conformance_backend=True)
    result = bridge.stream_message(
        _send_body({"message": {"messageId": "tck-message-response-session"}})
    )

    assert "task" not in result
    assert result["message"]["role"] == "ROLE_AGENT"
    assert bridge.agent.messages == []


def test_enabled_conformance_backend_still_forwards_ordinary_messages() -> None:
    """The conformance profile changes only matching TCK scenarios."""
    bridge = _bridge(conformance_backend=True)
    _status, body = _http_send(bridge, _send_body({}))

    assert body["task"]["status"]["state"] != "TASK_STATE_COMPLETED"
    assert bridge.agent.messages


def test_the_production_bridge_has_no_conformance_switch() -> None:
    """No constructor argument turns canned answers on for the production class."""
    assert A2ABridge.conformance_backend is False
    assert ConformanceA2ABridge.conformance_backend is True
    arguments: dict[str, Any] = {
        "agent": RecordingAgent(),
        "agent_card": {},
        "target": "WORKER",
        "store": A2ATaskStore(),
        "conformance_backend": True,
    }
    with pytest.raises(TypeError, match="conformance_backend"):
        A2ABridge(**arguments)


@pytest.mark.parametrize("enabled", [False, True])
def test_a2a_serve_flag_reaches_the_real_bridge(
    enabled: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """``a2a-serve`` builds the real bridge with the profile chosen at start."""
    seen: dict[str, Any] = {}

    async def manifest(**_: Any) -> list[dict[str, Any]]:
        return [{"agent": "WORKER", "description": "worker", "skills": ["chat"]}]

    class Runtime:
        def __init__(self, agent: Any) -> None:
            self.agent = agent

        def start(self) -> bool:
            return True

        def run(self, coro: Any) -> Any:
            coro.close()
            return None

        def stop(self) -> None:
            seen["stopped"] = True

    def serve(**kwargs: Any) -> None:
        bridge = kwargs["bridge"]
        seen["bridge"] = bridge
        seen["result"] = bridge.send_message(
            _send_body({"message": {"messageId": "tck-artifact-text-session"}})
        )
        raise KeyboardInterrupt

    argv = ["a2a-serve", "--endpoint-url", "http://127.0.0.1:8877/a2a/v1"]
    if enabled:
        argv.append("--conformance-backend")
    ns = cli.build_parser().parse_args(argv)

    assert ns.conformance_backend is enabled
    assert (
        cli_a2a._cmd_a2a_serve(
            ns, manifest_fetcher=manifest, runtime_factory=Runtime, server_runner=serve
        )
        == 0
    )
    bridge = seen["bridge"]
    assert type(bridge) is (ConformanceA2ABridge if enabled else A2ABridge)
    state = seen["result"]["task"]["status"]["state"]
    assert (state == "TASK_STATE_COMPLETED") is enabled
    assert seen["stopped"] is True
    warning = "conformance backend enabled" in capsys.readouterr().err
    assert warning is enabled
