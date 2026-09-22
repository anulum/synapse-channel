# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the Model Context Protocol bridge CLI command (mcp)

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

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
    assert args.name is None
    assert args.request_timeout == DEFAULT_REQUEST_TIMEOUT
    assert args.ready_timeout == 5.0

    custom = cli.build_parser().parse_args(
        ["mcp", "--request-timeout", "12.5", "--ready-timeout", "0.25"]
    )
    assert custom.request_timeout == 12.5
    assert custom.ready_timeout == 0.25


def test_parser_mcp_inbox_and_role_overrides() -> None:
    args = cli.build_parser().parse_args(
        [
            "mcp",
            "--role",
            "PROJ/reviewer",
            "--inbox-feed",
            "/state/feed.ndjson",
            "--inbox-cursor",
            "/state/cursor",
        ]
    )

    assert args.role == ["PROJ/reviewer"]
    assert args.inbox_feed == "/state/feed.ndjson"
    assert args.inbox_cursor == "/state/cursor"


def test_parser_mcp_accepts_owner_only_token_file() -> None:
    args = cli.build_parser().parse_args(["mcp", "--token-file", "/private/hub.token"])
    assert args.token_file == "/private/hub.token"


def _mcp_ns(**overrides: Any) -> argparse.Namespace:
    port = _free_port()
    base: dict[str, Any] = {
        "uri": f"ws://localhost:{port}",
        "name": "bridge",
        "token": None,
        "token_file": None,
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


def test_cmd_mcp_reports_a_missing_mcp_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A RuntimeError from the server (e.g. missing extra) prints and exits 1."""

    async def refuse(**_: object) -> int:
        msg = "MCP support needs the optional extra: pip install 'synapse-channel[mcp]'"
        raise RuntimeError(msg)

    monkeypatch.setattr(cli_mcp, "serve_stdio", refuse)
    assert cli_mcp._cmd_mcp(_mcp_ns()) == 1
    assert "pip install 'synapse-channel[mcp]'" in capsys.readouterr().err


def test_cmd_mcp_stops_cleanly_on_interrupt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C during the stdio server is a clean stop, not a traceback."""

    async def interrupted(**_: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mcp, "serve_stdio", interrupted)
    assert cli_mcp._cmd_mcp(_mcp_ns(name="bridge")) == 0
    assert "[bridge] MCP server stopped." in capsys.readouterr().out


def test_mcp_token_file_is_read_without_echoing_its_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "hub-token"
    secret.write_text("test-secret-value\n", encoding="utf-8")
    secret.chmod(0o600)
    received: list[str | None] = []

    async def capture(**options: object) -> int:
        received.append(cast(str | None, options["token"]))
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", capture)
    assert cli_mcp._cmd_mcp(_mcp_ns(token_file=str(secret))) == 0
    assert received == ["test-secret-value"]
    assert "test-secret-value" not in capsys.readouterr().err


def test_top_level_mcp_token_file_reaches_stdio_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = tmp_path / "hub-token"
    secret.write_text("test-secret-value\n", encoding="utf-8")
    secret.chmod(0o600)
    received: list[str | None] = []

    async def capture(**options: object) -> int:
        received.append(cast(str | None, options["token"]))
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", capture)
    assert cli.main(["mcp", "--name", "PROJ/codex", "--token-file", str(secret)]) == 0
    assert received == ["test-secret-value"]
    assert "test-secret-value" not in capsys.readouterr().err


def test_top_level_mcp_rejects_both_token_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["mcp", "--token", "one", "--token-file", "/no/file"]) == 2
    assert "either --token or --token-file" in capsys.readouterr().err


def test_mcp_refuses_ambiguous_token_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_mcp._cmd_mcp(_mcp_ns(token="x", token_file="/no/file")) == 2
    assert "either --token or --token-file" in capsys.readouterr().err
