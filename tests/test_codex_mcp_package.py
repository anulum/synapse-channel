# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — exact Codex host package lifecycle
"""Exercise the public CLI against Codex's real MCP config commands."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from synapse_channel import cli


def _exact_host() -> Path | None:
    candidate = os.environ.get("SYNAPSE_TEST_CODEX_BIN") or shutil.which("codex")
    if not candidate:
        return None
    host = Path(candidate)
    result = subprocess.run([str(host), "--version"], capture_output=True, text=True, timeout=10)
    return host if result.returncode == 0 and result.stdout.strip() == "codex-cli 0.156.0" else None


def _synapse_binary() -> Path | None:
    candidate = Path(__file__).resolve().parents[1] / ".venv/bin/synapse"
    if candidate.is_file():
        return candidate
    installed = shutil.which("synapse")
    return Path(installed) if installed else None


def _command(profile: Path, host: Path, action: str, *extra: str) -> int:
    return cli.main(
        [
            "integrations",
            action,
            "codex-cli",
            "--profile-root",
            str(profile),
            "--host-bin",
            str(host),
            *extra,
        ]
    )


def test_real_codex_lifecycle_preserves_unrelated_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    synapse = _synapse_binary()
    if synapse is None:
        pytest.skip("Synapse entrypoint unavailable")
    profile = tmp_path / "codex"
    profile.mkdir()
    config = profile / "config.toml"
    config.write_text('[mcp_servers.other]\ncommand = "true"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(profile))
    token = tmp_path / "private-token"
    token.write_text("private-token-value\n", encoding="utf-8")
    token.chmod(0o600)
    assert cli.main(["integrations", "inspect", "codex-cli", "--host-bin", str(host)]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert (
        _command(
            profile,
            host,
            "install",
            "--synapse-bin",
            str(synapse),
            "--identity",
            "C15/test",
            "--token-file",
            str(token),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert "private-token-value" not in config.read_text(encoding="utf-8")
    assert "other" in config.read_text(encoding="utf-8")
    assert _command(profile, host, "diagnose") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert _command(profile, host, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert config.read_text(encoding="utf-8") == '[mcp_servers.other]\ncommand = "true"\n'


def test_foreign_codex_entry_is_never_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    profile = tmp_path / "codex"
    profile.mkdir()
    config = profile / "config.toml"
    content = '[mcp_servers.synapse]\ncommand = "foreign"\n'
    config.write_text(content, encoding="utf-8")
    assert _command(profile, host, "uninstall") == 2
    assert json.loads(capsys.readouterr().out)["state"] == "error"
    assert config.read_text(encoding="utf-8") == content


def test_exact_host_preserves_incomplete_or_untrusted_entry_custody(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An ownership marker alone cannot grant removal of a Codex MCP entry."""
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    cases = (
        ("marker-only", "", {"schema": "synapse-codex-mcp.v1"}, "foreign"),
        (
            "wrong-schema",
            '[mcp_servers.synapse]\ncommand = "true"\n',
            {"schema": "untrusted", "entry_sha256": "0" * 64},
            "foreign",
        ),
        (
            "owner-removed",
            '[mcp_servers.synapse]\ncommand = "true"\n',
            {"schema": "synapse-codex-mcp.v1", "entry_sha256": "0" * 64},
            "modified",
        ),
    )
    for label, content, marker, state in cases:
        profile = tmp_path / label
        profile.mkdir()
        config = profile / "config.toml"
        if content:
            config.write_text(content, encoding="utf-8")
        marker_path = profile / ".synapse-codex-mcp.json"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        assert _command(profile, host, "inspect") == 0
        assert json.loads(capsys.readouterr().out)["state"] == state
        assert _command(profile, host, "uninstall") == 2
        assert "foreign or modified" in json.loads(capsys.readouterr().out)["reason"]
        assert marker_path.is_file()
        if content:
            assert config.read_text(encoding="utf-8") == content


def test_exact_host_refuses_invalid_install_inputs_before_profile_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    synapse = _synapse_binary()
    assert synapse is not None
    profile = tmp_path / "codex"
    readable_token = tmp_path / "readable-token"
    readable_token.write_text("private-token-value\n", encoding="utf-8")
    readable_token.chmod(0o644)
    cases = (
        ((), "identity"),
        (("--identity", "C15/test", "--uri", "http://127.0.0.1:8876"), "hub URI"),
        (("--identity", "C15/test", "--uri", "ws://127.0.0.1:8876/?key=secret"), "hub URI"),
        (("--identity", "C15/test"), "Synapse executable"),
        (
            (
                "--identity",
                "C15/test",
                "--synapse-bin",
                str(synapse),
                "--token-file",
                str(tmp_path / "missing-token"),
            ),
            "token file",
        ),
        (
            (
                "--identity",
                "C15/test",
                "--synapse-bin",
                str(synapse),
                "--token-file",
                str(readable_token),
            ),
            "owner-only",
        ),
    )
    for arguments, message in cases:
        assert _command(profile, host, "install", *arguments) == 2
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "error"
        assert message in row["reason"]
        assert not profile.exists()


def test_exact_host_refuses_malformed_or_symlinked_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    profile = tmp_path / "codex"
    profile.mkdir()
    (profile / "config.toml").write_text("[mcp_servers\n", encoding="utf-8")
    assert _command(profile, host, "inspect") == 2
    assert "config.toml is invalid" in json.loads(capsys.readouterr().out)["reason"]
    link = tmp_path / "linked"
    link.symlink_to(profile, target_is_directory=True)
    assert _command(link, host, "inspect") == 2
    assert "symlinks" in json.loads(capsys.readouterr().out)["reason"]


def test_exact_host_refuses_unsafe_profile_file_shapes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real Codex probe cannot read or change malformed profile custody files."""
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    outside = tmp_path / "outside"
    outside.write_text("preserve", encoding="utf-8")
    cases = (
        ("root-file", "root", "not a directory"),
        ("config-directory", "config-dir", "invalid or oversized"),
        ("config-link", "config-link", "symlinks"),
        ("marker-link", "marker-link", "symlinks"),
        ("invalid-table", "mcp_servers = 'bad'\n", "server table is invalid"),
        ("invalid-entry", "mcp_servers = { synapse = 'bad' }\n", "entry is invalid"),
    )
    for label, shape, expected in cases:
        profile = tmp_path / label
        if shape == "root":
            profile.write_text("preserve", encoding="utf-8")
        else:
            profile.mkdir()
            config = profile / "config.toml"
            if shape == "config-dir":
                config.mkdir()
            elif shape == "config-link":
                config.symlink_to(outside)
            elif shape == "marker-link":
                (profile / ".synapse-codex-mcp.json").symlink_to(outside)
            else:
                config.write_text(shape, encoding="utf-8")
        assert _command(profile, host, "inspect") == 2
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "error"
        assert expected in row["reason"]
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_exact_host_diagnose_requires_owned_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    profile = tmp_path / "codex"
    assert _command(profile, host, "diagnose") == 2
    assert "not owned" in json.loads(capsys.readouterr().out)["reason"]
    assert _command(profile, host, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"


def test_exact_host_refuses_to_remove_an_entry_with_a_changed_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    synapse = _synapse_binary()
    if synapse is None:
        pytest.skip("Synapse entrypoint unavailable")
    profile = tmp_path / "codex"
    assert (
        _command(profile, host, "install", "--synapse-bin", str(synapse), "--identity", "C15/test")
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    config = profile / "config.toml"
    original = config.read_text(encoding="utf-8")
    marker = profile / ".synapse-codex-mcp.json"
    custody = json.loads(marker.read_text(encoding="utf-8"))
    custody["entry_sha256"] = "0" * 64
    marker.write_text(json.dumps(custody), encoding="utf-8")
    assert _command(profile, host, "inspect") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "modified"
    assert _command(profile, host, "uninstall") == 2
    assert "foreign or modified" in json.loads(capsys.readouterr().out)["reason"]
    assert config.read_text(encoding="utf-8") == original
    marker.write_text("not-json", encoding="utf-8")
    assert _command(profile, host, "inspect") == 2
    assert "ownership marker is invalid" in json.loads(capsys.readouterr().out)["reason"]
