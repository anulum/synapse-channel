# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the outbound MCP CLI

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel import cli, cli_mcp_call
from synapse_channel.core.mcp_outbound import McpServerSpec, OutboundMcpClient


class _FakeSession:
    """A session that advertises one tool and echoes calls, no subprocess."""

    async def list_tools(self) -> Any:
        return SimpleNamespace(tools=[SimpleNamespace(name="echo", description="echoes")])

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        return SimpleNamespace(
            content=[SimpleNamespace(text=f"ran {name} {json.dumps(arguments or {})}")],
            isError=False,
        )


@asynccontextmanager
async def _fake_opener(spec: McpServerSpec) -> AsyncIterator[_FakeSession]:
    yield _FakeSession()


def _patch_fake_client(monkeypatch: pytest.MonkeyPatch, allowed: list[str]) -> None:
    spec = McpServerSpec(name="echo", command="x", allowed_tools=frozenset(allowed))
    client = OutboundMcpClient({"echo": spec}, session_opener=_fake_opener)
    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda config_path: client)


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    return int(args.func(args))


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps({"servers": [{"name": "echo", "command": "x", "allowed_tools": ["echo"]}]}),
        encoding="utf-8",
    )
    return config


def test_parsers_registered() -> None:
    parser = cli.build_parser()
    tools = parser.parse_args(["mcp-tools", "echo", "--config", "c.json"])
    assert tools.func is cli_mcp_call._cmd_mcp_tools
    call = parser.parse_args(["mcp-call", "echo", "echo", "--config", "c.json"])
    assert call.func is cli_mcp_call._cmd_mcp_call


def test_parse_arguments_decodes_values_and_merges_json() -> None:
    parsed = cli_mcp_call._parse_arguments(["count=5", "name=jo", 'raw="x"'], '{"base": true}')
    assert parsed == {"base": True, "count": 5, "name": "jo", "raw": "x"}


def test_parse_arguments_rejects_bad_pair() -> None:
    with pytest.raises(Exception, match="key=value"):
        cli_mcp_call._parse_arguments(["noequals"], "")


def test_parse_arguments_rejects_non_object_json() -> None:
    with pytest.raises(Exception, match="JSON object"):
        cli_mcp_call._parse_arguments([], "[1, 2]")


def test_build_client_reads_the_config(tmp_path: Path) -> None:
    client = cli_mcp_call._build_client(str(_config(tmp_path)))
    assert client.server_names() == ["echo"]


def test_mcp_tools_lists_allowed_tools(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, ["echo"])
    code = _run(["mcp-tools", "echo", "--config", str(_config(tmp_path))])
    out = capsys.readouterr().out
    assert code == 0
    assert "echo: echoes" in out


def test_mcp_tools_json_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, ["echo"])
    code = _run(["mcp-tools", "echo", "--config", str(_config(tmp_path)), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["name"] == "echo"


def test_mcp_call_invokes_a_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, ["echo"])
    code = _run(
        ["mcp-call", "echo", "echo", "--config", str(_config(tmp_path)), "--arg", 'text="hi"']
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "ran echo" in out
    assert '"text": "hi"' in out


def test_mcp_call_denies_a_non_allowlisted_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, [])  # nothing allowlisted
    code = _run(
        ["mcp-call", "echo", "echo", "--config", str(_config(tmp_path)), "--arg", 'text="hi"']
    )
    assert code == 2
    assert "not allowed by the config" in capsys.readouterr().out


def test_mcp_tools_reports_a_bad_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "mcp.json"
    bad.write_text("{}", encoding="utf-8")
    code = _run(["mcp-tools", "echo", "--config", str(bad)])
    assert code == 2
    assert "mcp-tools error" in capsys.readouterr().out


def test_mcp_call_reports_a_bad_arg(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, ["echo"])
    code = _run(
        ["mcp-call", "echo", "echo", "--config", str(_config(tmp_path)), "--arg", "noequals"]
    )
    assert code == 2
    assert "mcp-call error" in capsys.readouterr().out


class _FailingOpener:
    """An opener whose session entry raises, as the missing-SDK path would."""

    def __call__(self, spec: McpServerSpec) -> _FailingOpener:
        return self

    async def __aenter__(self) -> Any:
        raise RuntimeError("outbound MCP calls need the optional extra")

    async def __aexit__(self, *args: object) -> bool:
        return False


def _patch_failing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = McpServerSpec(name="echo", command="x", allowed_tools=frozenset({"echo"}))
    client = OutboundMcpClient({"echo": spec}, session_opener=_FailingOpener())
    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda config_path: client)


def test_mcp_tools_reports_a_missing_sdk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_failing_client(monkeypatch)
    code = _run(["mcp-tools", "echo", "--config", str(_config(tmp_path))])
    assert code == 2
    assert "optional extra" in capsys.readouterr().out


def test_mcp_call_reports_a_missing_sdk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_failing_client(monkeypatch)
    code = _run(
        ["mcp-call", "echo", "echo", "--config", str(_config(tmp_path)), "--arg", 'text="hi"']
    )
    assert code == 2
    assert "optional extra" in capsys.readouterr().out
