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
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host = _exact_host()
    if host is None:
        pytest.skip("exact Codex 0.156.0 binary unavailable")
    synapse = Path(__file__).resolve().parents[1] / ".venv/bin/synapse"
    if not synapse.is_file():
        pytest.skip("Synapse entrypoint unavailable")
    profile = tmp_path / "codex"
    profile.mkdir()
    config = profile / "config.toml"
    config.write_text('[mcp_servers.other]\ncommand = "true"\n', encoding="utf-8")
    token = tmp_path / "private-token"
    token.write_text("private-token-value\n", encoding="utf-8")
    token.chmod(0o600)
    assert _command(profile, host, "inspect") == 0
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
