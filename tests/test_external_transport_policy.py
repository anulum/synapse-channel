# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — external transport effective-policy conformance

from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

from synapse_channel.a2a_grpc import (
    DEFAULT_GRPC_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_GRPC_RPCS,
    DEFAULT_MAX_GRPC_MESSAGE_BYTES,
    A2AGrpcPolicy,
)
from synapse_channel.a2a_http import (
    DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS,
    DEFAULT_MAX_CONCURRENT_A2A_REQUESTS,
    MAX_A2A_JSON_BODY_BYTES,
)
from synapse_channel.core.hub import (
    DEFAULT_AUTH_TIMEOUT,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_CONNECTIONS_PER_HOST,
    DEFAULT_MAX_MSG_BYTES,
    SynapseHub,
)
from synapse_channel.core.hub_config import HubConfig
from synapse_channel.core.multihub_transport import DEFAULT_FETCH_TIMEOUT
from synapse_channel.dashboard_operator_writes import MAX_OPERATOR_BODY_BYTES
from synapse_channel.dashboard_setup_contract import MAX_SETUP_REQUEST_BYTES
from synapse_channel.mcp.bridge import DEFAULT_REQUEST_TIMEOUT
from synapse_channel.safe_webhook_transport import (
    WEBHOOK_MAX_REDIRECTS,
    WEBHOOK_MAX_RESPONSE_BYTES,
    describe_webhook_redirect_policy,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_external_transport_policy.py"
CI = ROOT / ".github" / "workflows" / "ci.yml"
SECURITY = ROOT / "SECURITY.md"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_external_transport_policy", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _policy_by_edge(tool: ModuleType) -> dict[str, Any]:
    policies = cast("tuple[Any, ...]", tool.EXTERNAL_TRANSPORT_POLICIES)
    return {str(policy.edge): policy for policy in policies}


def test_contract_enumerates_every_edge_and_dimension_without_implicit_policy() -> None:
    """One data contract must make every shipped boundary decision inspectable."""
    tool = _load_tool()
    policies = cast("tuple[Any, ...]", tool.EXTERNAL_TRANSPORT_POLICIES)

    assert tuple(policy.edge for policy in policies) == tool.EXPECTED_EDGES
    assert tool.EXPECTED_EDGES == (
        "websocket",
        "hub-http",
        "a2a-http",
        "a2a-grpc",
        "dashboard",
        "metrics",
        "webhook",
        "mcp",
        "federation",
    )
    assert tool.POLICY_DIMENSIONS == (
        "activation",
        "identity",
        "auth_acl",
        "encryption",
        "exposure",
        "request_size",
        "response_size",
        "timeout_concurrency",
        "sanitized_errors",
    )
    assert tool.contract_findings() == ()

    for policy in policies:
        assert tuple(policy.evidence)
        assert dataclasses.is_dataclass(policy)
        for dimension in tool.POLICY_DIMENSIONS:
            assert str(getattr(policy, dimension)).strip()


def test_contract_is_an_evidence_projection_not_a_second_config_surface() -> None:
    """The record cannot carry runtime values, listeners, credentials, or grants."""
    tool = _load_tool()
    policy_type = tool.ExternalTransportPolicy
    assert policy_type.__dataclass_params__.frozen is True
    assert tuple(field.name for field in dataclasses.fields(policy_type)) == (
        "edge",
        *tool.POLICY_DIMENSIONS,
        "evidence",
    )
    constructor_parameters = set(inspect.signature(SynapseHub.__init__).parameters)
    assert set(HubConfig().to_kwargs()) == constructor_parameters - {"self"}


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        ("missing-edge", "edge inventory must be"),
        ("duplicate-edge", "edge inventory contains duplicates"),
        ("implicit-control", "websocket.identity is not explicit"),
        ("missing-evidence", "websocket.evidence is empty"),
        ("stale-evidence", "is unresolved"),
        ("malformed-evidence", "is unresolved"),
    ],
)
def test_contract_check_fails_closed_on_inventory_or_evidence_drift(
    mutation: str, finding: str
) -> None:
    tool = _load_tool()
    policies = list(tool.EXTERNAL_TRANSPORT_POLICIES)
    first = policies[0]
    if mutation == "missing-edge":
        policies.pop()
    elif mutation == "duplicate-edge":
        policies[1] = dataclasses.replace(policies[1], edge=first.edge)
    elif mutation == "implicit-control":
        policies[0] = dataclasses.replace(first, identity="TBD")
    elif mutation == "missing-evidence":
        policies[0] = dataclasses.replace(first, evidence=())
    elif mutation == "stale-evidence":
        policies[0] = dataclasses.replace(first, evidence=("synapse_channel.missing:value",))
    else:
        policies[0] = dataclasses.replace(first, evidence=("not-a-reference",))

    assert any(finding in item for item in tool.contract_findings(policies))


def test_json_entry_point_emits_the_versioned_complete_contract() -> None:
    result = subprocess.run(
        [sys.executable, str(TOOL), "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    document = cast("dict[str, object]", json.loads(result.stdout))
    assert document["schema_version"] == "synapse-external-transport-policy.v1"
    edges = cast("list[dict[str, object]]", document["edges"])
    assert [edge["edge"] for edge in edges] == list(_load_tool().EXPECTED_EDGES)
    assert all(len(cast("list[object]", edge["evidence"])) >= 1 for edge in edges)


def test_in_process_cli_covers_human_json_and_fail_closed_modes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _load_tool()

    assert tool.main(["--check"]) == 0
    assert "9 edges x 9 dimensions" in capsys.readouterr().out
    assert tool.main(["--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == "synapse-external-transport-policy.v1"

    monkeypatch.setattr(tool, "contract_findings", lambda: ("injected drift",))
    assert tool.main(["--check"]) == 1
    assert "external transport policy: injected drift" in capsys.readouterr().out


def test_websocket_and_hub_http_rows_bind_to_the_immutable_hub_config() -> None:
    tool = _load_tool()
    policies = _policy_by_edge(tool)
    hub = SynapseHub.from_config(HubConfig())

    assert hub.max_msg_bytes == DEFAULT_MAX_MSG_BYTES == 1024 * 1024
    assert hub.max_clients == DEFAULT_MAX_CLIENTS == 256
    assert hub.max_connections_per_host == DEFAULT_MAX_CONNECTIONS_PER_HOST == 32
    assert hub.auth_timeout == DEFAULT_AUTH_TIMEOUT == 10.0
    source = inspect.getsource(SynapseHub.serve)
    assert "max_size=self.max_msg_bytes" in source
    assert "process_request=self._process_request" in source
    assert "ssl=ssl_context" in source
    assert "no second wire cap" in str(policies["websocket"].response_size)
    assert "GET-only" in str(policies["hub-http"].request_size)


def test_a2a_http_and_grpc_rows_share_the_selected_sibling_policy() -> None:
    """The historically divergent gRPC row stays on HTTP's selected profile."""
    tool = _load_tool()
    policies = _policy_by_edge(tool)
    policy = A2AGrpcPolicy()

    assert MAX_A2A_JSON_BODY_BYTES == DEFAULT_MAX_GRPC_MESSAGE_BYTES == 1024 * 1024
    assert DEFAULT_MAX_CONCURRENT_A2A_REQUESTS == policy.max_concurrent_rpcs == 32
    assert DEFAULT_MAX_CONCURRENT_GRPC_RPCS == 32
    assert DEFAULT_A2A_REQUEST_READ_TIMEOUT_SECONDS == policy.max_rpc_seconds == 30.0
    assert DEFAULT_GRPC_TIMEOUT_SECONDS == 30.0

    from synapse_channel import cli_a2a_serve

    source = inspect.getsource(cli_a2a_serve._cmd_a2a_serve)
    for composition in (
        "build_grpc_server_credentials(",
        "bearer_token=bridge.auth_token",
        "max_concurrent_rpcs=max_concurrent",
        "max_rpc_seconds=read_timeout",
        "server_credentials=grpc_credentials",
        "policy=grpc_policy",
    ):
        assert composition in source
    assert "same native TLS or mTLS" in str(policies["a2a-grpc"].encryption)


def test_dashboard_webhook_mcp_and_federation_rows_pin_real_resource_controls() -> None:
    tool = _load_tool()
    policies = _policy_by_edge(tool)

    assert MAX_OPERATOR_BODY_BYTES == 64 * 1024
    assert MAX_SETUP_REQUEST_BYTES == 4096
    assert WEBHOOK_MAX_RESPONSE_BYTES == 64 * 1024
    assert WEBHOOK_MAX_REDIRECTS == 5
    assert DEFAULT_REQUEST_TIMEOUT == 5.0
    assert DEFAULT_FETCH_TIMEOUT == 10.0
    redirect = describe_webhook_redirect_policy()
    assert redirect["max_redirects"] == WEBHOOK_MAX_REDIRECTS
    assert redirect["https_downgrade"] == "deny"
    assert "no MCP network listener" in str(policies["mcp"].exposure)
    assert "certificate pin" in str(policies["federation"].encryption)


def test_contract_is_a_required_ci_and_public_security_surface() -> None:
    ci = CI.read_text(encoding="utf-8")
    security = SECURITY.read_text(encoding="utf-8")
    assert "python tools/audit_external_transport_policy.py --check" in ci
    assert "External transport effective-policy contract" in security
    for edge in _load_tool().EXPECTED_EDGES:
        assert f"`{edge}`" in security
