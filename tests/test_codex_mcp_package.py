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
import sys
import tomllib
from pathlib import Path

import pytest

from synapse_channel import cli
from synapse_channel.codex_mcp_package import (
    MARKER_NAME,
    MAX_CONFIG_BYTES,
    CodexPackageError,
    apply_codex_package,
    inspect_codex_package,
)


def _malformed_peer(tmp_path: Path, mode: str) -> Path:
    """Run a deliberately broken process peer, never a replacement vendor qualification."""
    script = tmp_path / f"peer-{mode}.py"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"mode = {mode!r}\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('codex-cli ' + ('0.0.0' if mode == 'unverified' else '0.156.0'))\n"
        "elif mode == 'failure':\n"
        "    sys.exit(7)\n"
        "elif mode == 'invalid-json':\n"
        "    print('{broken')\n"
        "elif mode == 'invalid-entry':\n"
        "    print('[]')\n"
        "elif mode == 'invalid-transport':\n"
        '    print(\'{"transport":{"type":"http"}}\')\n',
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = script.with_suffix(".cmd")
        wrapper.write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf-8")
        return wrapper
    script.chmod(0o700)
    return script


def test_unavailable_host_refuses_without_creating_a_profile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "codex"
    assert _command(profile, tmp_path / "missing-host", "inspect") == 2
    assert json.loads(capsys.readouterr().out)["state"] == "host_unavailable"
    assert not profile.exists()
    with pytest.raises(CodexPackageError, match="executable is unavailable"):
        inspect_codex_package(profile, tmp_path / "missing-host")


def test_real_unrelated_executable_is_not_accepted_as_the_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = tmp_path / "codex"
    assert _command(profile, Path(sys.executable), "inspect") == 2
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "error"
    assert not profile.exists()


def test_real_host_launch_failure_does_not_create_a_profile(tmp_path: Path) -> None:
    host = tmp_path / ("invalid-host.exe" if os.name == "nt" else "invalid-host")
    host.write_text("invalid executable image", encoding="ascii")
    host.chmod(0o700)
    profile = tmp_path / "codex"
    with pytest.raises(CodexPackageError, match="host command failed"):
        inspect_codex_package(profile, host)
    assert not profile.exists()


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
            "ADAPTER/test",
            "--token-file",
            str(token),
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    assert "private-token-value" not in config.read_text(encoding="utf-8")
    assert "other" in config.read_text(encoding="utf-8")
    with pytest.raises(CodexPackageError, match="entry already exists"):
        apply_codex_package("install", profile=profile, host=host)
    assert _command(profile, host, "diagnose") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    for mode, reason in (
        ("failure", "validator refused"),
        ("invalid-json", "invalid JSON"),
        ("invalid-entry", "invalid entry"),
        ("invalid-transport", "transport is not stdio"),
    ):
        before = config.read_bytes()
        with pytest.raises(CodexPackageError, match=reason):
            apply_codex_package("diagnose", profile=profile, host=_malformed_peer(tmp_path, mode))
        assert config.read_bytes() == before
        assert inspect_codex_package(profile, host).state == "owned"
    with pytest.raises(CodexPackageError, match="removal did not complete"):
        apply_codex_package("uninstall", profile=profile, host=_malformed_peer(tmp_path, "failure"))
    assert inspect_codex_package(profile, host).state == "owned"
    assert _command(profile, host, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert config.read_text(encoding="utf-8") == '[mcp_servers.other]\ncommand = "true"\n'
    for mode, reason in (("failure", "installation failed"), ("silent", "unexpected entry")):
        with pytest.raises(CodexPackageError, match=reason):
            apply_codex_package(
                "install",
                profile=profile,
                host=_malformed_peer(tmp_path, mode),
                synapse_bin=synapse,
                identity="ADAPTER/test",
                uri="ws://127.0.0.1:1",
            )
        assert inspect_codex_package(profile, host).state == "absent"
        assert config.read_text(encoding="utf-8") == '[mcp_servers.other]\ncommand = "true"\n'
    with pytest.raises(CodexPackageError, match="unknown Codex package action"):
        apply_codex_package("invalid", profile=profile, host=host)
    with pytest.raises(CodexPackageError, match="0.0.0 is unverified"):
        apply_codex_package(
            "install", profile=profile, host=_malformed_peer(tmp_path, "unverified")
        )
    program = r"""
import sys
from pathlib import Path
from synapse_channel.codex_mcp_package import (
    MARKER_NAME, CodexPackageError, apply_codex_package,
)
profile, host, synapse = map(Path, sys.argv[1:])
profile.mkdir()
config = profile / "config.toml"
config.write_text('[mcp_servers.other]\ncommand = "true"\n')
marker = profile / MARKER_NAME
armed = True
def insert_owner_file(event, args):
    global armed
    if armed and event == "open" and args[0] == str(marker):
        armed = False
        marker.write_text("owner note")
sys.addaudithook(insert_owner_file)
try:
    apply_codex_package(
        "install", profile=profile, host=host, synapse_bin=synapse,
        identity="ADAPTER/test", uri="ws://127.0.0.1:1",
    )
except CodexPackageError as exc:
    assert "marker creation failed" in str(exc)
else:
    raise AssertionError("a concurrent owner file was overwritten")
assert not armed
assert marker.read_text() == "owner note"
assert config.read_text() == '[mcp_servers.other]\ncommand = "true"\n'
"""
    race = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path / "marker-race"), str(host), str(synapse)],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert race.returncode == 0, race.stdout + race.stderr


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
        (("--identity", "ADAPTER/test", "--uri", "http://127.0.0.1:8876"), "hub URI"),
        (("--identity", "ADAPTER/test", "--uri", "ws://127.0.0.1:8876/?key=secret"), "hub URI"),
        (("--identity", "ADAPTER/test"), "Synapse executable"),
        (
            (
                "--identity",
                "ADAPTER/test",
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
                "ADAPTER/test",
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
    assert apply_codex_package("uninstall", profile=profile, host=host).state == "absent"


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
        _command(
            profile, host, "install", "--synapse-bin", str(synapse), "--identity", "ADAPTER/test"
        )
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


def _contract_peer(directory: Path) -> Path:
    """Write an executable copy of the contract host."""
    source = Path(__file__).with_name("codex_contract_peer.py").read_text(encoding="utf-8")
    script = directory / "codex-contract-peer.py"
    script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    if os.name == "nt":
        # CreateProcess runs a batch file; it does not run a shebang script.
        launcher = directory / "codex-contract-peer.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return launcher
    script.chmod(0o700)
    return script


def _tool() -> Path:
    """Return a real executable the package can record as a command path."""
    # POSIX has `true`. Windows records the interpreter, which is a real executable.
    found = shutil.which("true") or sys.executable
    if not found:
        pytest.fail("a true executable is required to record a command path")
    return Path(found)


def _install(
    profile: Path,
    host: Path,
    *,
    uri: str = "ws://127.0.0.1:9",
    token: Path | None = None,
) -> int:
    """Install through the public CLI against one contract host."""
    extra: list[str] = []
    if token is not None:
        extra.extend(["--token-file", str(token)])
    return _command(
        profile,
        host,
        "install",
        "--synapse-bin",
        str(_tool()),
        "--identity",
        "ADAPTER/test",
        "--uri",
        uri,
        *extra,
    )


def test_cli_refuses_profiles_that_are_not_safe_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inspect refuses symlink, shape, size, and unreadable profile files."""
    host = _contract_peer(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("preserve", encoding="utf-8")
    oversized = "x" * (MAX_CONFIG_BYTES + 1)
    cases: tuple[tuple[str, str, str], ...] = (
        ("root-file", "root", "not a directory"),
        ("config-directory", "config-dir", "invalid or oversized"),
        ("config-link", "config-link", "symlinks"),
        ("marker-link", "marker-link", "symlinks"),
        ("oversized", oversized, "invalid or oversized"),
        ("bad-toml", "[mcp_servers\n", "config.toml is invalid"),
        ("bad-bytes", "bad-bytes", "config.toml is invalid"),
        ("unreadable", "unreadable", "config.toml is invalid"),
        ("bad-table", "mcp_servers = 'bad'\n", "server table is invalid"),
        ("bad-entry", "mcp_servers = { synapse = 'bad' }\n", "entry is invalid"),
    )
    for label, shape, expected in cases:
        # chmod 0 does not make a Windows file unreadable.
        if shape == "unreadable" and os.name == "nt":
            continue
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
                (profile / MARKER_NAME).symlink_to(outside)
            elif shape == "bad-bytes":
                config.write_bytes(b"\xff")
            elif shape == "unreadable":
                config.write_text("mcp_servers = {}\n", encoding="utf-8")
                config.chmod(0)
            else:
                config.write_text(shape, encoding="utf-8")
        try:
            assert _command(profile, host, "inspect") == 2
        finally:
            if shape == "unreadable":
                config.chmod(0o600)
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "error"
        assert expected in row["reason"]
    link = tmp_path / "linked-root"
    link.symlink_to(tmp_path / "root-file", target_is_directory=False)
    linked_dir = tmp_path / "real-dir"
    linked_dir.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(linked_dir, target_is_directory=True)
    assert _command(alias, host, "inspect") == 2
    assert "symlinks" in json.loads(capsys.readouterr().out)["reason"]
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_cli_keeps_entries_it_does_not_own(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent, foreign, and modified custody cannot be removed as owned."""
    host = _contract_peer(tmp_path)
    owned_env = (
        '[mcp_servers.synapse]\ncommand = "true"\n\n'
        '[mcp_servers.synapse.env]\nSYNAPSE_ADAPTER_OWNER = "synapse-channel"\n'
    )
    empty_marker: list[object] = []
    cases: tuple[tuple[str, str, dict[str, object] | list[object] | None, str], ...] = (
        ("absent", "", None, "absent"),
        ("marker-only", "", {"schema": "synapse-codex-mcp.v1"}, "foreign"),
        ("marker-list", "", empty_marker, "foreign"),
        (
            "wrong-schema",
            '[mcp_servers.synapse]\ncommand = "true"\n',
            {"schema": "untrusted"},
            "foreign",
        ),
        (
            "env-string",
            '[mcp_servers.synapse]\ncommand = "true"\nenv = "no"\n',
            {"schema": "synapse-codex-mcp.v1"},
            "modified",
        ),
        (
            "owner-missing",
            '[mcp_servers.synapse]\ncommand = "true"\n',
            {"schema": "synapse-codex-mcp.v1"},
            "modified",
        ),
        (
            "owner-wrong",
            owned_env.replace("synapse-channel", "someone-else"),
            {"schema": "synapse-codex-mcp.v1", "entry_sha256": "0" * 64},
            "modified",
        ),
        (
            "digest-wrong",
            owned_env,
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
        marker_path = profile / MARKER_NAME
        if marker is not None:
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
        before_config = config.read_bytes() if config.exists() else None
        before_marker = marker_path.read_bytes() if marker_path.exists() else None
        assert _command(profile, host, "inspect") == 0
        assert json.loads(capsys.readouterr().out)["state"] == state
        if state == "absent":
            assert _command(profile, host, "uninstall") == 0
            assert json.loads(capsys.readouterr().out)["state"] == "absent"
            continue
        assert _command(profile, host, "uninstall") == 2
        assert "foreign or modified" in json.loads(capsys.readouterr().out)["reason"]
        if before_config is None:
            assert not config.exists()
        else:
            assert config.read_bytes() == before_config
        if before_marker is None:
            assert not marker_path.exists()
        else:
            assert marker_path.read_bytes() == before_marker
    profile = tmp_path / "bad-marker"
    profile.mkdir()
    (profile / "config.toml").write_text(owned_env, encoding="utf-8")
    (profile / MARKER_NAME).write_text("not-json", encoding="utf-8")
    assert _command(profile, host, "inspect") == 2
    assert "ownership marker is invalid" in json.loads(capsys.readouterr().out)["reason"]


def test_cli_installs_an_owned_entry_and_refuses_to_replace_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Install records an owned stdio entry and a second install leaves it."""
    host = _contract_peer(tmp_path)
    token = tmp_path / "token"
    token.write_text("secret-value\n", encoding="utf-8")
    token.chmod(0o600)
    profile = tmp_path / "ws"
    assert _install(profile, host, token=token) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    config = (profile / "config.toml").read_text(encoding="utf-8")
    assert "secret-value" not in config
    assert "synapse-channel" in config
    # The TOML source escapes backslashes. The contract is the parsed command.
    assert tomllib.loads(config)["mcp_servers"]["synapse"]["command"] == str(_tool())
    before = (profile / "config.toml").read_bytes()
    assert _install(profile, host, token=token) == 2
    assert "already exists" in json.loads(capsys.readouterr().out)["reason"]
    assert (profile / "config.toml").read_bytes() == before
    secure = tmp_path / "wss"
    assert _install(secure, host, uri="wss://127.0.0.1:9") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"


def test_cli_refuses_install_input_before_writing_an_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bad identity, URI, binary, or token never creates a marker."""
    host = _contract_peer(tmp_path)
    tool = _tool()
    readable = tmp_path / "readable"
    readable.write_text("secret-value\n", encoding="utf-8")
    readable.chmod(0o644)
    inert = tmp_path / "inert"
    inert.write_text("not executable\n", encoding="utf-8")
    inert.chmod(0o644)
    cases: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("identity", ("--identity", "noslash", "--synapse-bin", str(tool)), "identity"),
        (
            "scheme",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "http://127.0.0.1:9",
            ),
            "hub URI",
        ),
        (
            "hostless",
            ("--identity", "ADAPTER/test", "--synapse-bin", str(tool), "--uri", "ws://"),
            "hub URI",
        ),
        (
            "user",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://user@127.0.0.1:9",
            ),
            "hub URI",
        ),
        (
            "query",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://127.0.0.1:9?q=1",
            ),
            "hub URI",
        ),
        (
            "fragment",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://127.0.0.1:9#frag",
            ),
            "hub URI",
        ),
        (
            "binary",
            ("--identity", "ADAPTER/test", "--uri", "ws://127.0.0.1:9"),
            "Synapse executable",
        ),
        (
            "missing-binary",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tmp_path / "missing-tool"),
                "--uri",
                "ws://127.0.0.1:9",
            ),
            "Synapse executable",
        ),
        (
            "inert-binary",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(inert),
                "--uri",
                "ws://127.0.0.1:9",
            ),
            "Synapse executable",
        ),
        (
            "missing-token",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://127.0.0.1:9",
                "--token-file",
                str(tmp_path / "missing-token"),
            ),
            "token file",
        ),
        (
            "open-token",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://127.0.0.1:9",
                "--token-file",
                str(readable),
            ),
            "owner-only",
        ),
        (
            "foreign-token",
            (
                "--identity",
                "ADAPTER/test",
                "--synapse-bin",
                str(tool),
                "--uri",
                "ws://127.0.0.1:9",
                "--token-file",
                "/etc/passwd",
            ),
            "owner-only",
        ),
    )
    for label, arguments, expected in cases:
        # Unix mode bits and /etc/passwd do not describe a Windows token file.
        if os.name == "nt" and label in {"open-token", "foreign-token"}:
            continue
        profile = tmp_path / label
        assert _command(profile, host, "install", *arguments) == 2
        row = json.loads(capsys.readouterr().out)
        assert row["state"] == "error"
        assert expected in row["reason"]
        assert not profile.exists()


def test_cli_diagnose_accepts_only_the_recorded_stdio_transport(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagnose fails closed unless the validator repeats the owned stdio entry."""
    host = _contract_peer(tmp_path)
    bare = tmp_path / "bare"
    assert _command(bare, host, "diagnose") == 2
    assert "not owned" in json.loads(capsys.readouterr().out)["reason"]
    healthy = tmp_path / "healthy"
    assert _install(healthy, host) == 0
    capsys.readouterr()
    assert _command(healthy, host, "diagnose") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "owned"
    refusals = (
        ("get-fail", "validator refused"),
        ("get-bad-json", "invalid JSON"),
        ("get-list", "invalid entry"),
        ("get-http", "transport is not stdio"),
        ("get-wrong-command", "transport is not stdio"),
        ("get-wrong-args", "transport is not stdio"),
        ("get-drop", "transport is not stdio"),
    )
    for mode, expected in refusals:
        profile = tmp_path / mode
        monkeypatch.delenv("SYNAPSE_CODEX_PEER", raising=False)
        assert _install(profile, host) == 0
        capsys.readouterr()
        marker = (profile / MARKER_NAME).read_bytes()
        monkeypatch.setenv("SYNAPSE_CODEX_PEER", mode)
        assert _command(profile, host, "diagnose") == 2
        assert expected in json.loads(capsys.readouterr().out)["reason"]
        assert (profile / MARKER_NAME).read_bytes() == marker


def test_cli_uninstall_removes_only_a_committed_owned_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed or partial remove leaves the owned entry and its marker."""
    host = _contract_peer(tmp_path)
    original = '[mcp_servers.other]\ncommand = "true"\n'
    refusals = (
        ("remove-fail", "removal did not complete"),
        ("remove-keep", "removal did not complete"),
    )
    for mode, expected in refusals:
        profile = tmp_path / mode
        profile.mkdir()
        (profile / "config.toml").write_text(original, encoding="utf-8")
        monkeypatch.delenv("SYNAPSE_CODEX_PEER", raising=False)
        assert _install(profile, host) == 0
        capsys.readouterr()
        before = (profile / "config.toml").read_bytes()
        monkeypatch.setenv("SYNAPSE_CODEX_PEER", mode)
        assert _command(profile, host, "uninstall") == 2
        assert expected in json.loads(capsys.readouterr().out)["reason"]
        assert (profile / "config.toml").read_bytes() == before
        assert (profile / MARKER_NAME).is_file()
    profile = tmp_path / "removed"
    profile.mkdir()
    (profile / "config.toml").write_text(original, encoding="utf-8")
    monkeypatch.delenv("SYNAPSE_CODEX_PEER", raising=False)
    assert _install(profile, host) == 0
    capsys.readouterr()
    assert _command(profile, host, "uninstall") == 0
    assert json.loads(capsys.readouterr().out)["state"] == "absent"
    assert not (profile / MARKER_NAME).exists()
    assert (profile / "config.toml").read_text(encoding="utf-8") == original


def test_cli_refuses_a_host_that_does_not_commit_the_owned_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Install failure and a host that omits ownership leave no marker."""
    host = _contract_peer(tmp_path)
    original = '[mcp_servers.other]\ncommand = "true"\n'
    for mode, expected, changed in (
        ("add-fail", "installation failed", False),
        ("add-silent", "unexpected entry", False),
        ("add-no-owner", "unexpected entry", True),
    ):
        profile = tmp_path / mode
        profile.mkdir()
        config = profile / "config.toml"
        config.write_text(original, encoding="utf-8")
        monkeypatch.setenv("SYNAPSE_CODEX_PEER", mode)
        assert _install(profile, host) == 2
        assert expected in json.loads(capsys.readouterr().out)["reason"]
        assert not (profile / MARKER_NAME).exists()
        text = config.read_text(encoding="utf-8")
        assert ("synapse-channel" not in text) if changed else text == original


def test_cli_refuses_a_version_probe_that_is_not_the_reviewed_host(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-zero probe, a foreign banner, and a changed second probe are refused."""
    host = _contract_peer(tmp_path)
    monkeypatch.setenv("SYNAPSE_CODEX_PEER", "version-status")
    status = tmp_path / "status"
    assert _command(status, host, "inspect") == 2
    assert "version probe failed" in json.loads(capsys.readouterr().out)["reason"]
    assert not status.exists()
    monkeypatch.setenv("SYNAPSE_CODEX_PEER", "bad-version")
    foreign = tmp_path / "foreign"
    assert _command(foreign, host, "inspect") == 2
    assert "version probe failed" in json.loads(capsys.readouterr().out)["reason"]
    monkeypatch.setenv("SYNAPSE_CODEX_PEER", "flip-version")
    flipped = tmp_path / "flipped"
    assert (
        _command(
            flipped,
            host,
            "install",
            "--identity",
            "ADAPTER/test",
            "--synapse-bin",
            str(_tool()),
        )
        == 2
    )
    assert "0.0.0 is unverified" in json.loads(capsys.readouterr().out)["reason"]
    assert not flipped.exists()
    plain = tmp_path / "not-executable"
    plain.write_text("#!/bin/sh\n", encoding="utf-8")
    plain.chmod(0o644)
    assert _command(tmp_path / "cli-plain", plain, "inspect") == 2
    assert json.loads(capsys.readouterr().out)["state"] == "host_unavailable"
    with pytest.raises(CodexPackageError, match="executable is unavailable"):
        inspect_codex_package(tmp_path / "direct-plain", plain)
    with pytest.raises(CodexPackageError, match="unknown Codex package action"):
        apply_codex_package("invalid", profile=tmp_path / "unknown", host=host)


def test_a_marker_collision_removes_the_entry_the_host_just_added(tmp_path: Path) -> None:
    """An existing marker file rolls the new entry back and keeps the intruder text."""
    host = _contract_peer(tmp_path)
    program = r"""
import sys
from pathlib import Path
from synapse_channel.codex_mcp_package import (
    MARKER_NAME, CodexPackageError, apply_codex_package,
)
profile, host, tool = map(Path, sys.argv[1:])
profile.mkdir()
config = profile / "config.toml"
original = "[mcp_servers.other]\ncommand = \"true\"\n"
config.write_text(original)
marker = profile / MARKER_NAME
armed = True
def insert_owner_file(event, args):
    global armed
    if armed and event == "open" and args[0] == str(marker):
        armed = False
        marker.write_text("owner note")
sys.addaudithook(insert_owner_file)
try:
    apply_codex_package(
        "install", profile=profile, host=host, synapse_bin=tool,
        identity="ADAPTER/test", uri="ws://127.0.0.1:9",
    )
except CodexPackageError as exc:
    assert "marker creation failed" in str(exc)
else:
    raise AssertionError("a concurrent owner file was overwritten")
assert not armed
assert marker.read_text() == "owner note"
assert config.read_text() == original
"""
    env = os.environ.copy()
    root = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(root / "src")
    env.pop("SYNAPSE_CODEX_PEER", None)
    race = subprocess.run(
        [
            sys.executable,
            "-c",
            program,
            str(tmp_path / "marker-race"),
            str(host),
            str(_tool()),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )
    assert race.returncode == 0, race.stdout + race.stderr


def test_token_without_posix_or_windows_identity_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host with neither a uid nor Windows still refuses the token."""
    host = _contract_peer(tmp_path)
    token = tmp_path / "token"
    token.write_text("secret-value\n", encoding="utf-8")
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.setattr(os, "name", "java")
    with pytest.raises(CodexPackageError, match="token file must be owner-only"):
        apply_codex_package(
            "install",
            profile=tmp_path / "profile",
            host=host,
            synapse_bin=_tool(),
            identity="ADAPTER/test",
            uri="ws://127.0.0.1:9",
            token_file=token,
        )
    assert not (tmp_path / "profile").exists()


def test_windows_token_shared_with_everyone_is_refused(tmp_path: Path) -> None:
    """A Windows token Everyone can read is not owner-only."""
    if os.name != "nt":
        pytest.skip("Windows token ACLs are measured on Windows")
    from synapse_channel.core.secure_path import apply_owner_only_file

    host = _contract_peer(tmp_path)
    token = tmp_path / "token"
    token.write_text("secret-value\n", encoding="utf-8")
    apply_owner_only_file(token)
    subprocess.run(
        ["icacls", os.fspath(token), "/grant", "*S-1-1-0:(R)"],
        check=True,
        capture_output=True,
    )
    with pytest.raises(CodexPackageError, match="token file must be owner-only"):
        apply_codex_package(
            "install",
            profile=tmp_path / "profile",
            host=host,
            synapse_bin=_tool(),
            identity="ADAPTER/test",
            uri="ws://127.0.0.1:9",
            token_file=token,
        )
    assert not (tmp_path / "profile").exists()
