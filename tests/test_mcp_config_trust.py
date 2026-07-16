# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-file and repository provenance for outbound MCP policy

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel.core.mcp_config import McpConfigError
from synapse_channel.core.mcp_config_signing import sign_mcp_config_document
from synapse_channel.core.mcp_config_trust import (
    discover_repository_root,
    load_trusted_mcp_config,
)


def _executable(path: Path) -> tuple[Path, str]:
    shutil.copy2("/bin/true", path)
    path.chmod(0o700)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _write_owner_json(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


def _document(command: Path, *, digest: str = "", **server_overrides: Any) -> dict[str, Any]:
    server: dict[str, Any] = {
        "name": "echo",
        "command": str(command),
        "cwd": str(command.parent),
        "allowed_tools": ["echo"],
    }
    if digest:
        server["command_sha256"] = digest
    server.update(server_overrides)
    return {"version": 1, "servers": [server]}


def _trust_bundle(private_key: Ed25519PrivateKey) -> dict[str, Any]:
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return {
        "version": 1,
        "keys": [
            {
                "key_id": "ops",
                "public_key": base64.b64encode(public_key).decode("ascii"),
                "revoked": False,
            }
        ],
    }


def test_load_trusted_config_enforces_owner_file_and_absolute_executable(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    executable, digest = _executable(tmp_path / "mcp-server")
    config = _write_owner_json(
        tmp_path / "operator" / "mcp.json",
        _document(executable, digest=digest, inherit_env=["LANG"]),
    )

    servers, report = load_trusted_mcp_config(config, repository_root=repository)

    assert set(servers) == {"echo"}
    assert report.outside_repository is True
    assert report.trust_bundle_outside_repository is None
    assert report.signed_by == ""
    assert report.unhashed_servers == ()
    assert report.executables[0].sha256 == digest
    assert report.inherited_environment == ("echo:LANG",)


def test_signed_config_verifies_against_owner_trust_bundle(tmp_path: Path) -> None:
    executable, digest = _executable(tmp_path / "mcp-server")
    private_key = Ed25519PrivateKey.generate()
    signed = sign_mcp_config_document(
        _document(executable, digest=digest), key_id="ops", private_key=private_key
    )
    config = _write_owner_json(tmp_path / "config.json", signed)
    trust = _write_owner_json(tmp_path / "trust.json", _trust_bundle(private_key))

    _servers, report = load_trusted_mcp_config(
        config,
        trust_bundle_path=trust,
        repository_root=tmp_path / "unrelated-repository",
    )

    assert report.signed_by == "ops"
    assert report.trust_bundle_outside_repository is True
    assert report.unhashed_servers == ()


def test_signed_document_requires_explicit_trust_root(tmp_path: Path) -> None:
    executable, _digest = _executable(tmp_path / "mcp-server")
    signed = sign_mcp_config_document(
        _document(executable), key_id="ops", private_key=Ed25519PrivateKey.generate()
    )
    config = _write_owner_json(tmp_path / "config.json", signed)

    with pytest.raises(McpConfigError, match="no --config-trust-bundle"):
        load_trusted_mcp_config(config, repository_root=tmp_path / "repo")


def test_repository_local_config_requires_explicit_override(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    executable, _digest = _executable(tmp_path / "mcp-server")
    config = _write_owner_json(repository / "mcp.json", _document(executable))

    with pytest.raises(McpConfigError, match="inside the active repository"):
        load_trusted_mcp_config(config, repository_root=repository)

    _servers, report = load_trusted_mcp_config(
        config, repository_root=repository, allow_repo_config=True
    )
    assert report.outside_repository is False


def test_outside_config_hardlinked_into_repository_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    executable, _digest = _executable(tmp_path / "mcp-server")
    config = _write_owner_json(tmp_path / "operator" / "mcp.json", _document(executable))
    (repository / "mcp-hardlink.json").hardlink_to(config)

    with pytest.raises(McpConfigError, match="hard links"):
        load_trusted_mcp_config(config, repository_root=repository)


def test_cwd_is_required_outside_repository_and_arguments_are_reported(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    repository.chmod(0o700)
    (repository / ".git").mkdir()
    executable, _digest = _executable(tmp_path / "mcp-server")

    missing_cwd = _document(executable)
    del missing_cwd["servers"][0]["cwd"]
    missing_config = _write_owner_json(tmp_path / "missing-cwd.json", missing_cwd)
    with pytest.raises(McpConfigError, match="cwd is required"):
        load_trusted_mcp_config(missing_config, repository_root=repository)

    local_config = _write_owner_json(
        tmp_path / "local-cwd.json",
        _document(executable, cwd=str(repository), args=["-m", "server"]),
    )
    with pytest.raises(McpConfigError, match="cwd.*inside the active"):
        load_trusted_mcp_config(local_config, repository_root=repository)

    _servers, report = load_trusted_mcp_config(
        local_config,
        repository_root=repository,
        allow_repo_config=True,
    )
    assert report.repository_local_cwds == ("echo",)
    assert report.unbound_arguments == ("echo:0", "echo:1")


def test_repository_local_trust_bundle_is_rejected(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    (repository / ".git").mkdir(parents=True)
    executable, _digest = _executable(tmp_path / "mcp-server")
    private_key = Ed25519PrivateKey.generate()
    config = _write_owner_json(
        tmp_path / "config.json",
        sign_mcp_config_document(_document(executable), key_id="ops", private_key=private_key),
    )
    trust = _write_owner_json(repository / "trust.json", _trust_bundle(private_key))

    with pytest.raises(McpConfigError, match="trust bundle.*inside the active repository"):
        load_trusted_mcp_config(
            config,
            trust_bundle_path=trust,
            repository_root=repository,
        )

    _servers, report = load_trusted_mcp_config(
        config,
        trust_bundle_path=trust,
        repository_root=repository,
        allow_repo_config=True,
    )
    assert report.trust_bundle_outside_repository is False


def test_config_file_floor_rejects_loose_mode_symlink_and_bad_json(tmp_path: Path) -> None:
    executable, _digest = _executable(tmp_path / "mcp-server")
    loose = _write_owner_json(tmp_path / "loose.json", _document(executable))
    loose.chmod(0o644)
    with pytest.raises(McpConfigError, match="accessible by other users"):
        load_trusted_mcp_config(loose, repository_root=tmp_path / "repo")

    target = _write_owner_json(tmp_path / "target.json", _document(executable))
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(McpConfigError, match="cannot securely open"):
        load_trusted_mcp_config(link, repository_root=tmp_path / "repo")

    documents = (
        ('{"version":1,"servers":[],"servers":[]}', "duplicate JSON key"),
        ("{bad", "invalid JSON"),
        ('{"version":1,"timeout_seconds":' + "9" * 5001 + "}", "invalid JSON numeric value"),
        ("[]", "document must be a JSON object"),
    )
    for index, (content, match) in enumerate(documents):
        path = tmp_path / f"bad-{index}.json"
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        with pytest.raises(McpConfigError, match=match):
            load_trusted_mcp_config(path, repository_root=tmp_path / "repo")


def test_discover_repository_root_handles_files_worktrees_and_absence(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    nested = repository / "a" / "b"
    nested.mkdir(parents=True)
    (repository / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert discover_repository_root(nested) == repository.resolve()
    marker = nested / "file.txt"
    marker.write_text("x", encoding="utf-8")
    assert discover_repository_root(marker) == repository.resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    assert discover_repository_root(outside) is None


def test_config_load_discovers_repository_or_operates_without_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    nested = repository / "nested"
    nested.mkdir(parents=True)
    (repository / ".git").mkdir()
    executable, _digest = _executable(tmp_path / "mcp-server")
    config = _write_owner_json(tmp_path / "operator" / "config.json", _document(executable))

    monkeypatch.chdir(nested)
    _servers, discovered = load_trusted_mcp_config(config)
    assert discovered.repository_root == str(repository.resolve())

    no_repo = tmp_path / "no-repo"
    no_repo.mkdir()
    monkeypatch.chdir(no_repo)
    _servers, absent = load_trusted_mcp_config(config)
    assert absent.repository_root == ""
