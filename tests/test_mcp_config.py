# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — strict outbound MCP execution-policy schema tests

from __future__ import annotations

from typing import Any

import pytest

from synapse_channel.core.mcp_config import (
    McpConfigError,
    McpServerSpec,
    parse_mcp_config,
    tool_allowed,
)


def test_parse_mcp_config_preserves_explicit_launch_policy() -> None:
    servers = parse_mcp_config(
        {
            "version": 1,
            "servers": [
                {
                    "name": "fs",
                    "command": "/opt/mcp/fs",
                    "args": ["--root", "/data"],
                    "env": {"MODE": "read-only"},
                    "inherit_env": ["LANG"],
                    "cwd": "/srv/mcp",
                    "allowed_tools": ["read", "list"],
                    "timeout_seconds": 12,
                    "command_sha256": "AB" * 32,
                }
            ],
        }
    )

    assert set(servers) == {"fs"}
    spec = servers["fs"]
    assert spec.args == ("--root", "/data")
    assert spec.env == {"MODE": "read-only"}
    assert spec.inherit_env == ("LANG",)
    assert spec.allowed_tools == frozenset({"read", "list"})
    assert spec.timeout_seconds == 12.0
    assert spec.command_sha256 == "ab" * 32


def test_tool_allowed_is_deny_by_default() -> None:
    explicit = McpServerSpec(name="fs", command="/bin/false", allowed_tools=frozenset({"read"}))
    wildcard = McpServerSpec(name="fs", command="/bin/false", allowed_tools=frozenset({"*"}))

    assert tool_allowed(explicit, "read") is True
    assert tool_allowed(explicit, "write") is False
    assert tool_allowed(wildcard, "write") is True


def test_server_spec_preserves_legacy_positional_field_order() -> None:
    spec = McpServerSpec(
        "legacy",
        "/bin/false",
        ("--flag",),
        {"LANG": "C"},
        "/tmp",
        frozenset({"echo"}),
        12.0,
    )

    assert spec.cwd == "/tmp"
    assert spec.allowed_tools == frozenset({"echo"})
    assert spec.timeout_seconds == 12.0
    assert spec.inherit_env == ()


def test_server_spec_defensively_freezes_nested_policy() -> None:
    source_env = {"LANG": "C"}
    spec = McpServerSpec(
        name="immutable",
        command="/bin/false",
        args=["--flag"],  # type: ignore[arg-type]
        env=source_env,
        inherit_env=["LANG"],  # type: ignore[arg-type]
        allowed_tools={"echo"},  # type: ignore[arg-type]
    )
    source_env["LANG"] = "tampered"

    assert spec.args == ("--flag",)
    assert spec.env == {"LANG": "C"}
    assert spec.inherit_env == ("LANG",)
    assert spec.allowed_tools == frozenset({"echo"})
    with pytest.raises(TypeError):
        spec.env["INJECTED"] = "yes"  # type: ignore[index]


@pytest.mark.parametrize(
    ("document", "match"),
    [
        ([], "JSON object"),
        ({"version": 2, "servers": []}, "version must be 1"),
        ({"servers": [], "typo": True}, "unknown field"),
        ({}, "'servers' list"),
        ({"servers": [1]}, "entry 0 must be an object"),
        ({"servers": [{"name": "", "command": "/x"}]}, "non-empty string"),
        ({"servers": [{"name": "x", "command": "/x", "typo": 1}]}, "unknown field"),
        ({"servers": [{"name": "x", "command": "/x", "args": "bad"}]}, "list of strings"),
        ({"servers": [{"name": "x", "command": "/x", "args": [""]}]}, "empty strings"),
        (
            {"servers": [{"name": "x", "command": "/x", "allowed_tools": ["a", "a"]}]},
            "must not contain duplicates",
        ),
        ({"servers": [{"name": "x", "command": "/x", "env": []}]}, "must be an object"),
        (
            {"servers": [{"name": "x", "command": "/x", "env": {"1BAD": "x"}}]},
            "invalid environment name",
        ),
        (
            {"servers": [{"name": "x", "command": "/x", "env": {"OK": 1}}]},
            "must be a string",
        ),
        (
            {"servers": [{"name": "x", "command": "/x", "inherit_env": ["BAD-NAME"]}]},
            "invalid inherited environment",
        ),
        ({"servers": [{"name": "x", "command": "/x", "cwd": 1}]}, "cwd.*string"),
        (
            {"servers": [{"name": "x", "command": "/x", "timeout_seconds": 0}]},
            "positive and finite",
        ),
        (
            {"servers": [{"name": "x", "command": "/x", "command_sha256": "bad"}]},
            "64 hexadecimal",
        ),
        (
            {"servers": [{"name": "x", "command": "/x", "command_sha256": 1}]},
            "must be a string",
        ),
        (
            {
                "servers": [
                    {"name": "x", "command": "/x"},
                    {"name": "x", "command": "/y"},
                ]
            },
            "duplicate MCP server name",
        ),
    ],
)
def test_parse_mcp_config_rejects_ambiguous_or_malformed_policy(document: Any, match: str) -> None:
    with pytest.raises(McpConfigError, match=match):
        parse_mcp_config(document)


@pytest.mark.parametrize("timeout", [True, "1", float("inf"), float("nan")])
def test_parse_mcp_config_rejects_non_finite_or_non_numeric_timeout(timeout: Any) -> None:
    with pytest.raises(McpConfigError, match="timeout_seconds"):
        parse_mcp_config({"servers": [{"name": "x", "command": "/x", "timeout_seconds": timeout}]})
