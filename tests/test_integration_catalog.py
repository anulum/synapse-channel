# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — public integration catalog checks
"""Keep accepted capabilities explicit and unsupported hosts fail closed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.integration_catalog import HOSTS, host_capability


def _exact_pi_host() -> Path | None:
    """Locate the installed 0.87.1 RPC host used by the public diagnosis command."""
    candidate = os.environ.get("SYNAPSE_TEST_PI_BIN")
    host = (
        Path(candidate)
        if candidate
        else Path(__file__).resolve().parents[1] / "integrations/pi/node_modules/.bin/pi"
    )
    if not host.is_file():
        return None
    result = subprocess.run([str(host), "--version"], capture_output=True, text=True, timeout=10)
    return host if result.returncode == 0 and result.stdout.strip() == "0.87.1" else None


def test_public_catalog_lists_each_reviewed_host(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["integrations", "list"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["host"] for row in rows} == {host.key for host in HOSTS}
    assert host_capability("codex-cli").verified_version == "0.156.0"
    assert host_capability("pi").verified_version == "0.87.1"
    assert host_capability("claude-code").verified_version == "2.1.280"
    assert host_capability("opencode").verified_version == "1.18.32"
    assert all(
        row["version_accepted"] is False for row in rows if row["capability"] == "unsupported"
    )


@pytest.mark.parametrize("host", ["gemini-cli", "claude-desktop"])
def test_unsupported_package_operations_are_explicit(
    host: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for action in ("install", "diagnose", "uninstall"):
        assert cli.main(["integrations", action, host]) == 2
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "unsupported"
        assert action not in row["lifecycle"]


def test_missing_and_unknown_host_return_clear_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["integrations", "inspect"]) == 2
    assert "host is required" in capsys.readouterr().err
    assert cli.main(["integrations", "inspect", "unknown-host"]) == 2
    assert "unknown integration host" in capsys.readouterr().err


def test_opencode_status_parsing_ignores_path_words() -> None:
    from synapse_channel.cli_integration_catalog import _opencode_state, _row

    row = _row(host_capability("opencode"), "opencode 1.18.32")
    assert row["verified_version"] == "1.18.32"
    assert row["version_accepted"] is True
    assert (
        _opencode_state("config: installed (/tmp/absent)\nplugin: installed (/tmp/absent)")
        == "installed"
    )
    assert (
        _opencode_state("config: absent (/tmp/installed)\nplugin: absent (/tmp/installed)")
        == "absent"
    )
    assert _opencode_state("config: installed (x)\nplugin: absent (y)") == "partial"


@pytest.mark.parametrize("host", ["claude-code", "opencode"])
def test_native_inspect_uses_isolated_profile_without_a_host_binary(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The public inventory must inspect a clean profile without touching the user's profile."""
    profile = tmp_path / host
    assert (
        cli.main(
            [
                "integrations",
                "inspect",
                host,
                "--profile-root",
                str(profile),
                "--host-bin",
                str(tmp_path / "missing-host"),
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "absent"
    assert row["observed_version"] is None
    assert not profile.exists()


@pytest.mark.parametrize(
    ("host", "environment"),
    [("claude-code", "CLAUDE_CONFIG_DIR"), ("opencode", "XDG_CONFIG_HOME")],
)
def test_native_inspect_honours_isolated_host_default_profile(
    host: str,
    environment: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / host
    monkeypatch.setenv(environment, str(profile))
    assert (
        cli.main(["integrations", "inspect", host, "--host-bin", str(tmp_path / "missing-host")])
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "absent"
    assert not profile.exists()


@pytest.mark.parametrize("host", ["claude-code", "opencode", "codex-cli", "pi"])
def test_install_or_diagnose_requires_an_admitted_host_version(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unavailable binary cannot turn an accepted catalog row into an installation."""
    action = "diagnose" if host == "pi" else "install"
    assert (
        cli.main(
            [
                "integrations",
                action,
                host,
                "--profile-root",
                str(tmp_path / host),
                "--host-bin",
                str(tmp_path / "missing-host"),
            ]
        )
        == 2
    )
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "unverified_version"
    assert not (tmp_path / host).exists()


@pytest.mark.parametrize("host", ["codex-cli", "pi"])
def test_manual_and_bound_hosts_report_unavailable_without_mutation(
    host: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / host
    assert cli.main(
        [
            "integrations",
            "inspect",
            host,
            "--profile-root",
            str(profile),
            "--host-bin",
            str(tmp_path / "missing-host"),
        ]
    ) == (2 if host == "codex-cli" else 0)
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == "host_unavailable"
    assert not profile.exists()


@pytest.mark.parametrize(("host", "state"), [("claude-code", "absent"), ("opencode", "ok")])
def test_native_uninstall_of_an_absent_isolated_profile_is_idempotent(
    host: str, state: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / host
    assert (
        cli.main(
            [
                "integrations",
                "uninstall",
                host,
                "--profile-root",
                str(profile),
                "--host-bin",
                str(tmp_path / "missing-host"),
            ]
        )
        == 0
    )
    row = json.loads(capsys.readouterr().out)
    assert row["state"] == state
    assert not profile.exists()


def test_public_pi_diagnosis_loads_the_exact_extension_in_a_real_offline_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnose the shipped guard through Pi RPC without a model or hub request."""
    host = _exact_pi_host()
    if host is None:
        pytest.skip("exact Pi 0.87.1 host unavailable")
    profile = tmp_path / "agent"
    profile.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(profile))
    (profile / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "ollama": {
                        "baseUrl": "http://127.0.0.1:11434/v1",
                        "api": "openai-completions",
                        "apiKey": "local",
                        "models": [{"id": "gemma3:1b"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    extension = Path(__file__).resolve().parents[1] / "integrations/pi/index.ts"
    common = [
        "--host-bin",
        str(host),
        "--pi-model",
        "ollama/gemma3:1b",
    ]
    assert cli.main(["integrations", "diagnose", "pi", *common]) == 2
    missing = json.loads(capsys.readouterr().out)
    assert missing["state"] == "error"
    assert "--pi-extension" in missing["reason"]
    assert (
        cli.main(
            [
                "integrations",
                "diagnose",
                "pi",
                *common,
                "--pi-extension",
                str(tmp_path / "absent.ts"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["state"] == "error"
    writable = tmp_path / "writable.ts"
    writable.write_text(extension.read_text(encoding="utf-8"), encoding="utf-8")
    writable.chmod(0o666)
    assert (
        cli.main(["integrations", "diagnose", "pi", *common, "--pi-extension", str(writable)]) == 2
    )
    unsafe = json.loads(capsys.readouterr().out)
    assert unsafe["state"] == "error"
    assert "private regular file" in unsafe["reason"]
    no_guard = tmp_path / "no-guard.ts"
    no_guard.write_text("export default function () {}\n", encoding="utf-8")
    no_guard.chmod(0o600)
    assert (
        cli.main(["integrations", "diagnose", "pi", *common, "--pi-extension", str(no_guard)]) == 2
    )
    unloaded = json.loads(capsys.readouterr().out)
    assert unloaded["state"] == "error"
    assert "claim guard did not load" in unloaded["reason"]
    assert (
        cli.main(["integrations", "diagnose", "pi", *common, "--pi-extension", str(extension)]) == 0
    )
    loaded = json.loads(capsys.readouterr().out)
    assert loaded["state"] == "loaded"
    assert loaded["observed_version"] == "0.87.1"
    assert not (profile / "sessions").exists()


def test_public_claude_lifecycle_validates_and_removes_an_isolated_plugin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the accepted host package through its public lifecycle verbs."""
    selected = shutil.which("claude")
    if selected is None:
        pytest.skip("exact Claude Code host unavailable")
    host = Path(selected)
    version = subprocess.run([str(host), "--version"], capture_output=True, text=True, timeout=10)
    if version.returncode or version.stdout.strip() != "2.1.280 (Claude Code)":
        pytest.skip("exact Claude Code 2.1.280 host unavailable")
    synapse = Path(__file__).resolve().parents[1] / ".venv/bin/synapse"
    assert synapse.is_file()
    profile = tmp_path / "claude"
    profile.mkdir()
    unrelated = profile / "keep.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    common = ["--profile-root", str(profile), "--host-bin", str(host)]
    assert cli.main(["integrations", "inspect", "claude-code", *common]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert (
        cli.main(
            [
                "integrations",
                "install",
                "claude-code",
                *common,
                "--synapse-bin",
                str(synapse),
                "--identity",
                "C25/review",
                "--uri",
                "ws://127.0.0.1:8876",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert cli.main(["integrations", "diagnose", "claude-code", *common]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert cli.main(["integrations", "uninstall", "claude-code", *common]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert unrelated.read_text(encoding="utf-8") == "preserve"
