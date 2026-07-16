# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the outbound MCP CLI

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from synapse_channel import cli, cli_mcp_call
from synapse_channel.core.mcp_outbound import (
    McpDependencyError,
    McpServerSpec,
    McpToolError,
    OutboundMcpClient,
)


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
    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda config_path, **_kwargs: client)


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    return int(args.func(args))


def _config(tmp_path: Path) -> Path:
    executable = tmp_path / "mcp-server"
    shutil.copy2("/bin/true", executable)
    executable.chmod(0o700)
    config = tmp_path / "mcp.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "echo",
                        "command": str(executable),
                        "cwd": str(tmp_path),
                        "allowed_tools": ["echo"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    return config


def test_parsers_registered() -> None:
    parser = cli.build_parser()
    tools = parser.parse_args(["mcp-tools", "echo", "--config", "c.json"])
    assert tools.func is cli_mcp_call._cmd_mcp_tools
    call = parser.parse_args(["mcp-call", "echo", "echo", "--config", "c.json"])
    assert call.func is cli_mcp_call._cmd_mcp_call


def test_parser_accepts_outbound_mcp_trust_flags() -> None:
    args = cli.build_parser().parse_args(
        [
            "mcp-tools",
            "echo",
            "--config",
            "/operator/mcp.json",
            "--config-trust-bundle",
            "/operator/trust.json",
            "--allow-repo-mcp-config",
        ]
    )
    assert args.config_trust_bundle == "/operator/trust.json"
    assert args.allow_repo_mcp_config is True


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


def test_mcp_tools_forwards_config_trust_policy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    client = OutboundMcpClient(
        {"echo": McpServerSpec(name="echo", command="x", allowed_tools=frozenset({"echo"}))},
        session_opener=_fake_opener,
    )

    def build_client(path: str, **kwargs: object) -> OutboundMcpClient:
        captured.update({"path": path, **kwargs})
        return client

    monkeypatch.setattr(cli_mcp_call, "_build_client", build_client)
    config = _config(tmp_path)
    code = _run(
        [
            "mcp-tools",
            "echo",
            "--config",
            str(config),
            "--config-trust-bundle",
            "/operator/trust.json",
            "--allow-repo-mcp-config",
        ]
    )

    assert code == 0
    assert "echo: echoes" in capsys.readouterr().out
    assert captured == {
        "path": str(config),
        "trust_bundle_path": "/operator/trust.json",
        "allow_repo_config": True,
    }


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


def test_mcp_call_crosses_trusted_config_and_real_stdio_server(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", "/sensitive/parent-home")
    mcp_spec = importlib.util.find_spec("mcp")
    assert mcp_spec is not None and mcp_spec.origin is not None
    mcp_site_packages = str(Path(mcp_spec.origin).parent.parent)
    script = tmp_path / "echo_server.py"
    script.write_text(
        """import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("cli-echo-test")

@server.tool()
def echo(text: str) -> str:
    return f"echo: {text}; home={os.environ.get('HOME', 'missing')}"

if __name__ == "__main__":
    server.run()
""",
        encoding="utf-8",
    )
    config = tmp_path / "real-mcp.json"
    config.write_text(
        json.dumps(
            {
                "version": 1,
                "servers": [
                    {
                        "name": "echo",
                        "command": str(Path(sys.executable).resolve()),
                        "args": [str(script)],
                        "cwd": str(tmp_path),
                        "env": {"PYTHONPATH": mcp_site_packages},
                        "allowed_tools": ["echo"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    code = _run(
        [
            "mcp-call",
            "echo",
            "echo",
            "--config",
            str(config),
            "--arg",
            'text="real-boundary"',
        ]
    )

    assert code == 0
    output = capfd.readouterr().out
    assert "echo: real-boundary; home=" in output
    assert "/sensitive/parent-home" not in output


def test_mcp_human_output_makes_server_controls_visible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hostile = "remote\x1b]52;c;YQ==\x07\nforged\u202e"

    class HostileClient:
        async def list_tools(self, _server: str) -> list[dict[str, str]]:
            return [{"name": hostile, "description": hostile}]

        async def call_tool(self, _server: str, _tool: str, _arguments: dict[str, object]) -> str:
            return hostile

    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda _path, **_kwargs: HostileClient())

    assert _run(["mcp-tools", "echo", "--config", str(_config(tmp_path))]) == 0
    assert _run(["mcp-call", "echo", "echo", "--config", str(_config(tmp_path))]) == 0

    rendered = capsys.readouterr().out
    assert "remote\\x1b]52;c;YQ==\\x07\\nforged\\u202e" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\u202e" not in rendered


def test_mcp_call_denies_a_non_allowlisted_tool(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fake_client(monkeypatch, [])  # nothing allowlisted
    code = _run(
        ["mcp-call", "echo", "echo", "--config", str(_config(tmp_path)), "--arg", 'text="hi"']
    )
    assert code == 3
    assert "not allowed by the config" in capsys.readouterr().out


def test_mcp_tools_denies_an_unknown_server_with_access_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = _run(["mcp-tools", "missing", "--config", str(_config(tmp_path))])

    assert code == 3
    assert "not in the outbound MCP allowlist" in capsys.readouterr().out


def test_mcp_call_reports_a_tool_failure_with_operational_exit_code(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        async def call_tool(
            self,
            server: str,
            tool: str,
            arguments: dict[str, object],
        ) -> str:
            raise McpToolError(f"{server}/{tool} failed with {arguments!r}")

    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda _path, **_kwargs: FailingClient())

    code = _run(["mcp-call", "echo", "echo", "--config", str(_config(tmp_path))])

    assert code == 1
    assert "echo/echo failed" in capsys.readouterr().out


def test_mcp_tools_reports_a_bad_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "mcp.json"
    bad.write_text("{}", encoding="utf-8")
    bad.chmod(0o600)
    code = _run(["mcp-tools", "echo", "--config", str(bad)])
    assert code == 2
    assert "mcp-tools error" in capsys.readouterr().out


def test_mcp_tools_rejects_an_integer_too_large_for_float_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "huge-timeout.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "echo",
                        "command": str(Path("/bin/true").resolve()),
                        "cwd": str(tmp_path),
                        "timeout_seconds": 10**400,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)

    code = _run(["mcp-tools", "echo", "--config", str(config)])
    output = capsys.readouterr().out

    assert code == 2
    assert "at most 3600 seconds" in output
    assert "Traceback" not in output


def test_mcp_tools_sanitizes_an_integer_beyond_the_json_decoder_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "decoder-limit-timeout.json"
    config.write_text(
        '{"servers":[{"name":"echo","command":"/bin/true","cwd":"/tmp",'
        '"timeout_seconds":' + "9" * 5001 + "}]}",
        encoding="utf-8",
    )
    config.chmod(0o600)

    code = _run(["mcp-tools", "echo", "--config", str(config)])
    output = capsys.readouterr().out

    assert code == 2
    assert "invalid JSON numeric value" in output
    assert "ValueError" not in output
    assert "Exceeds the limit" not in output
    assert "Traceback" not in output


@pytest.mark.parametrize("verb", ["mcp-tools", "mcp-call"])
def test_real_stdio_startup_failure_has_a_stable_operational_boundary(
    verb: str,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    config = tmp_path / "failing-server.json"
    config.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "echo",
                        "command": str(Path("/bin/false").resolve()),
                        "cwd": str(tmp_path),
                        "allowed_tools": ["echo"],
                        "timeout_seconds": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o600)
    argv = [verb, "echo"]
    if verb == "mcp-call":
        argv.append("echo")
    argv.extend(["--config", str(config)])

    code = _run(argv)
    captured = capfd.readouterr()
    visible = captured.out + captured.err

    assert code == 1
    assert "failed during MCP startup or transport" in captured.out
    assert "ExceptionGroup" not in visible
    assert "Traceback" not in visible


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
        raise McpDependencyError("outbound MCP calls need the optional extra")

    async def __aexit__(self, *args: object) -> bool:
        return False


def _patch_failing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = McpServerSpec(name="echo", command="x", allowed_tools=frozenset({"echo"}))
    client = OutboundMcpClient({"echo": spec}, session_opener=_FailingOpener())
    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda config_path, **_kwargs: client)


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
