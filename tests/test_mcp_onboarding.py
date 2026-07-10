# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — MCP onboarding identity and entry-point tests

from __future__ import annotations

import sys
from typing import Any

import pytest

from synapse_channel import cli, cli_mcp, ergonomics
from synapse_channel.mcp.onboarding import McpIdentityResolution, resolve_mcp_identity


def test_explicit_name_beats_poisoned_ambient_identity() -> None:
    resolved = resolve_mcp_identity(
        "PROJ/codex",
        env={"SYN_PROJECT": "OTHER", "SYN_IDENTITY": "OTHER/foreign"},
    )

    assert resolved == McpIdentityResolution(
        name="PROJ/codex",
        project="PROJ",
        source="flag",
        note="explicit --name overrides ambient SYN_IDENTITY=OTHER/foreign",
    )


def test_explicit_name_without_ambient_state_has_no_warning() -> None:
    resolved = resolve_mcp_identity("PROJ/client", env={})

    assert resolved == McpIdentityResolution("PROJ/client", "PROJ", "flag")


def test_agreeing_environment_keeps_the_exact_client_identity() -> None:
    resolved = resolve_mcp_identity(
        None,
        env={"SYN_PROJECT": "PROJ", "SYN_IDENTITY": "PROJ/claude", "HOME": "/home/me"},
        cwd_basename="OTHER",
        home_basename="me",
    )

    assert resolved == McpIdentityResolution("PROJ/claude", "PROJ", "env")


def test_project_or_cwd_fallback_gets_a_stable_mcp_subidentity() -> None:
    from_project = resolve_mcp_identity(
        None,
        env={"SYN_PROJECT": "PROJ", "HOME": "/home/me"},
        cwd_basename="OTHER",
        home_basename="me",
    )
    from_cwd = resolve_mcp_identity(
        None,
        env={"HOME": "/home/me"},
        cwd_basename="REPO",
        home_basename="me",
    )

    assert from_project == McpIdentityResolution("PROJ/mcp", "PROJ", "env")
    assert from_cwd == McpIdentityResolution("REPO/mcp", "REPO", "cwd")


def test_unpaired_ambient_identity_is_ignored_visibly() -> None:
    resolved = resolve_mcp_identity(
        None,
        env={"SYN_IDENTITY": "FOREIGN/seat", "HOME": "/home/me"},
        cwd_basename="REPO",
        home_basename="me",
    )

    assert resolved.name == "REPO/mcp"
    assert "ignored ambient SYN_IDENTITY=FOREIGN/seat" in resolved.note


def test_blank_or_implausible_identity_is_refused() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        resolve_mcp_identity("  ", env={})
    with pytest.raises(ValueError, match="cannot derive a safe project identity"):
        resolve_mcp_identity(
            None,
            env={"HOME": "/home/anulum"},
            cwd_basename="anulum",
            home_basename="anulum",
        )


def test_lazy_cwd_resolution_uses_the_shared_git_aware_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ergonomics, "_cwd_basename", lambda: "GITROOT")

    resolved = resolve_mcp_identity(
        None,
        env={"HOME": "/home/me"},
        home_basename="me",
    )

    assert resolved.name == "GITROOT/mcp"


def test_dedicated_registry_entry_dispatches_to_synapse_mcp() -> None:
    calls: list[list[str] | None] = []

    def dispatch(argv: list[str] | None) -> int:
        calls.append(argv)
        return 7

    assert cli_mcp.main(["--name", "PROJ/client"], dispatcher=dispatch) == 7
    assert calls == [["mcp", "--name", "PROJ/client"]]


def test_dedicated_registry_entry_uses_process_arguments_and_live_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str] | None] = []

    def dispatch(argv: list[str] | None) -> int:
        calls.append(argv)
        return 8

    monkeypatch.setattr(cli, "main", dispatch)
    monkeypatch.setattr(sys, "argv", ["synapse-channel", "--name", "PROJ/client"])

    assert cli_mcp.main() == 8
    assert calls == [["mcp", "--name", "PROJ/client"]]


def test_cmd_refuses_an_unsafe_derived_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SYN_PROJECT", raising=False)
    monkeypatch.delenv("SYN_IDENTITY", raising=False)
    monkeypatch.setattr(ergonomics, "_cwd_basename", lambda: "home")

    namespace = type("Args", (), {"name": None})()

    assert cli_mcp._cmd_mcp(namespace) == 2
    assert "cannot derive a safe project identity" in capsys.readouterr().err


def test_cmd_reports_resolved_identity_only_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("SYN_IDENTITY", raising=False)

    async def stop(**kwargs: Any) -> int:
        assert kwargs["name"] == "PROJ/client"
        return 0

    monkeypatch.setattr(cli_mcp, "serve_stdio", stop)
    namespace = type(
        "Args",
        (),
        {
            "uri": "ws://localhost:1",
            "name": "PROJ/client",
            "token": None,
            "request_timeout": 1.0,
            "ready_timeout": 1.0,
            "role": ["PROJ/reviewer"],
            "inbox_feed": "/tmp/feed",
            "inbox_cursor": "/tmp/cursor",
        },
    )()

    assert cli_mcp._cmd_mcp(namespace) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[PROJ/client] MCP bridge identity resolved from flag" in captured.err
    assert "note:" not in captured.err


def test_cmd_surfaces_an_ambient_identity_override_only_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def stop(**_: Any) -> int:
        return 0

    monkeypatch.setenv("SYN_IDENTITY", "OTHER/foreign")
    monkeypatch.setattr(cli_mcp, "serve_stdio", stop)
    namespace = type(
        "Args",
        (),
        {
            "uri": "ws://localhost:1",
            "name": "PROJ/client",
            "token": None,
            "request_timeout": 1.0,
            "ready_timeout": 1.0,
        },
    )()

    assert cli_mcp._cmd_mcp(namespace) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "explicit --name overrides ambient SYN_IDENTITY=OTHER/foreign" in captured.err
