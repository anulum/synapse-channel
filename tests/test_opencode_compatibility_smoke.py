# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — verified OpenCode archive installer and real ACP smoke tests
"""Exercise release archive installation and the gated real OpenCode ACP face."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Artifact,
        load_compatibility,
    )
    from tools.opencode_compatibility_smoke import (
        SmokeError,
        artifact_url,
        install_archive,
        smoke_binary,
        verify_archive,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Artifact,
        load_compatibility,
    )
    from tools.opencode_compatibility_smoke import (
        SmokeError,
        artifact_url,
        install_archive,
        smoke_binary,
        verify_archive,
    )

_PAYLOAD = b"real archive member bytes\n"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(path: Path, *, binary: str = "opencode") -> Artifact:
    return Artifact(
        key="test-platform",
        name=path.name,
        sha256=_digest(path),
        binary=binary,
        runner="",
        smoke=False,
    )


def _tar_archive(path: Path, *, member_type: bytes = tarfile.REGTYPE) -> Path:
    with tarfile.open(path, mode="w:gz") as archive:
        member = tarfile.TarInfo("opencode")
        member.mode = 0o755
        member.size = len(_PAYLOAD)
        member.type = member_type
        if member_type == tarfile.SYMTYPE:
            member.linkname = "../outside"
            member.size = 0
        archive.addfile(member, None if member.size == 0 else io.BytesIO(_PAYLOAD))
    return path


def _zip_archive(path: Path, *, symlink: bool = False) -> Path:
    member = zipfile.ZipInfo("opencode")
    member.external_attr = ((stat.S_IFLNK if symlink else stat.S_IFREG) | 0o755) << 16
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, b"../outside" if symlink else _PAYLOAD)
    return path


@pytest.mark.parametrize("archive_kind", ["tar", "zip"])
def test_installer_extracts_only_the_verified_root_binary(
    tmp_path: Path, archive_kind: str
) -> None:
    archive = (
        _tar_archive(tmp_path / "opencode-test.tar.gz")
        if archive_kind == "tar"
        else _zip_archive(tmp_path / "opencode-test.zip")
    )
    artifact = _artifact(archive)
    destination = tmp_path / "bin" / "opencode"

    install_archive(archive, artifact, destination)

    assert destination.read_bytes() == _PAYLOAD
    assert destination.stat().st_mode & stat.S_IXUSR
    with pytest.raises(SmokeError, match="already exists"):
        install_archive(archive, artifact, destination)


@pytest.mark.parametrize("archive_kind", ["tar", "zip"])
def test_installer_refuses_link_members(tmp_path: Path, archive_kind: str) -> None:
    archive = (
        _tar_archive(tmp_path / "opencode-link.tar.gz", member_type=tarfile.SYMTYPE)
        if archive_kind == "tar"
        else _zip_archive(tmp_path / "opencode-link.zip", symlink=True)
    )
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match="regular file"):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


def test_archive_verification_refuses_digest_drift(tmp_path: Path) -> None:
    archive = _tar_archive(tmp_path / "opencode-test.tar.gz")
    artifact = _artifact(archive)
    changed = Artifact(
        key=artifact.key,
        name=artifact.name,
        sha256="0" * 64,
        binary=artifact.binary,
        runner=artifact.runner,
        smoke=artifact.smoke,
    )

    with pytest.raises(SmokeError, match="digest mismatch"):
        verify_archive(archive, changed)


@pytest.mark.parametrize("contents", [b"not a zip archive", b""])
def test_installer_removes_partial_destination_for_malformed_archives(
    tmp_path: Path, contents: bytes
) -> None:
    archive = tmp_path / "opencode-test.zip"
    archive.write_bytes(contents)
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match="cannot inspect OpenCode archive"):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


@pytest.mark.parametrize(
    ("member", "payload", "message"),
    [
        ("nested/opencode", _PAYLOAD, "exactly one root"),
        ("opencode", b"", "empty binary"),
    ],
)
def test_installer_refuses_missing_or_empty_root_binary(
    tmp_path: Path, member: str, payload: bytes, message: str
) -> None:
    archive = tmp_path / "opencode-test.zip"
    with zipfile.ZipFile(archive, mode="w") as bundle:
        bundle.writestr(member, payload)
    destination = tmp_path / "opencode"

    with pytest.raises(SmokeError, match=message):
        install_archive(archive, _artifact(archive), destination)

    assert not destination.exists()


def test_offline_install_cli_uses_the_same_manifest_and_archive_contract(tmp_path: Path) -> None:
    archive = _tar_archive(tmp_path / "opencode-linux-x64.tar.gz")
    data = cast(dict[str, Any], json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8")))
    artifacts = cast(dict[str, dict[str, Any]], data["artifacts"])
    artifacts["linux-x64"]["sha256"] = _digest(archive)
    manifest = tmp_path / "compatibility.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    destination = tmp_path / "installed" / "opencode"
    report = tmp_path / "install-report.json"

    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "tools.opencode_compatibility_smoke",
            "--manifest",
            str(manifest),
            "install",
            "--platform",
            "linux-x64",
            "--archive",
            str(archive),
            "--destination",
            str(destination),
            "--report",
            str(report),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert destination.read_bytes() == _PAYLOAD
    assert json.loads(completed.stdout)["archive_sha256"] == _digest(archive)
    assert json.loads(report.read_text(encoding="utf-8"))["platform"] == "linux-x64"


def test_official_artifact_url_is_immutable() -> None:
    contract = load_compatibility()
    artifact = contract.artifact("windows-x64")

    assert artifact_url(contract, artifact) == (
        "https://github.com/anomalyco/opencode/releases/download/v1.17.20/opencode-windows-x64.zip"
    )

    with pytest.raises(SmokeError, match="official repository"):
        artifact_url(replace(contract, repository="example.invalid/opencode"), artifact)


def test_smoke_refuses_non_regular_or_wrong_version_executables(tmp_path: Path) -> None:
    contract = load_compatibility()
    with pytest.raises(SmokeError, match="regular file"):
        smoke_binary(tmp_path, contract)
    with pytest.raises(SmokeError, match="version mismatch"):
        smoke_binary(Path(sys.executable).resolve(), contract)


def test_real_pinned_opencode_negotiates_the_expected_acp_face() -> None:
    configured = os.environ.get("OPENCODE_BIN", "").strip()
    discovered = configured or shutil.which("opencode") or ""
    if not discovered:
        pytest.skip("real OpenCode compatibility smoke requires OPENCODE_BIN")
    binary = Path(discovered).resolve()
    version = subprocess.run(  # nosec B603
        [str(binary), "--version"],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    contract = load_compatibility()
    if version.returncode != 0 or version.stdout.strip() != contract.version:
        pytest.skip(f"real OpenCode compatibility smoke requires {contract.version}")

    assert smoke_binary(binary, contract) == {
        "agent": "OpenCode",
        "mcp_http": True,
        "mcp_sse": True,
        "protocol_version": 1,
        "terminal_auth": True,
        "version": "1.17.20",
    }
