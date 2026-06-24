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

from synapse_channel import cli, cli_mcp
from synapse_channel.mcp.server import DEFAULT_REQUEST_TIMEOUT


def test_parser_mcp() -> None:
    args = cli.build_parser().parse_args(["mcp", "--uri", "ws://x", "--name", "bridge"])
    assert args.func is cli_mcp._cmd_mcp
    assert args.uri == "ws://x"
    assert args.name == "bridge"


def test_parser_mcp_request_timeout() -> None:
    assert cli.build_parser().parse_args(["mcp"]).request_timeout == DEFAULT_REQUEST_TIMEOUT
    args = cli.build_parser().parse_args(["mcp", "--request-timeout", "12.5"])
    assert args.request_timeout == 12.5


def _mcp_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "uri": "ws://x",
        "name": "bridge",
        "token": None,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_mcp_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", fake)
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 0


def test_cmd_mcp_forwards_request_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", fake)
    assert cli_mcp._cmd_mcp(_mcp_ns(request_timeout=9.0)) == 0
    assert captured["request_timeout"] == 9.0


def test_cmd_mcp_reports_missing_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(**kwargs: Any) -> int:
        raise RuntimeError("pip install 'synapse-channel[mcp]'")

    monkeypatch.setattr(cli_mcp, "serve_stdio", fake)
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 1
    assert "[mcp]" in capsys.readouterr().err


def test_cmd_mcp_handles_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mcp, "serve_stdio", fake)
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 0
