# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge CLI command (mcp)

from __future__ import annotations

import argparse
from typing import Any

import pytest

from hub_e2e_helpers import _free_port
from synapse_channel import cli, cli_mcp
from synapse_channel.mcp.server import DEFAULT_REQUEST_TIMEOUT


def test_parser_mcp() -> None:
    args = cli.build_parser().parse_args(["mcp", "--uri", "ws://x", "--name", "bridge"])
    assert args.func is cli_mcp._cmd_mcp
    assert args.uri == "ws://x"
    assert args.name == "bridge"


def test_parser_mcp_timeouts() -> None:
    args = cli.build_parser().parse_args(["mcp"])
    assert args.request_timeout == DEFAULT_REQUEST_TIMEOUT
    assert args.ready_timeout == 5.0

    custom = cli.build_parser().parse_args(
        ["mcp", "--request-timeout", "12.5", "--ready-timeout", "0.25"]
    )
    assert custom.request_timeout == 12.5
    assert custom.ready_timeout == 0.25


def _mcp_ns(**overrides: Any) -> argparse.Namespace:
    port = _free_port()
    base: dict[str, Any] = {
        "uri": f"ws://localhost:{port}",
        "name": "bridge",
        "token": None,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
        "ready_timeout": 0.1,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_mcp_reports_unreachable_hub(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 1
    err = capsys.readouterr().err
    assert "could not reach hub" in err
    assert "bridge" in err


def test_cmd_mcp_preserves_distinct_name_in_unreachable_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli_mcp._cmd_mcp(_mcp_ns(name="adapter")) == 1
    assert "[adapter] could not reach hub" in capsys.readouterr().err
