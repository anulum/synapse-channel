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


def test_parser_mcp() -> None:
    args = cli.build_parser().parse_args(["mcp", "--uri", "ws://x", "--name", "bridge"])
    assert args.func is cli_mcp._cmd_mcp
    assert args.uri == "ws://x"
    assert args.name == "bridge"


def _mcp_ns() -> argparse.Namespace:
    return argparse.Namespace(uri="ws://x", name="bridge", token=None)


def test_cmd_mcp_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**kwargs: Any) -> int:
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", fake)
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 0


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
