# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — installed Pi dependency pin CLI regressions
"""Exercise archive identity, integrity and path refusal through the public CLI."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools/sync_pi_dependency_pins.py"
RELATIVE = "node_modules/@earendil-works/pi-coding-agent/node_modules/brace-expansion"


def _fixture(root: Path, *, version: str = "5.0.12", unsafe: str = "") -> tuple[Path, Path]:
    """Create a real registry-shaped archive and an existing installed package."""
    archive = root / "registry.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in {
            "package/package.json": json.dumps(
                {"name": "brace-expansion", "version": version}
            ).encode(),
            "package/index.js": b"export default 'reviewed';\n",
        }.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            bundle.addfile(member, io.BytesIO(data))
        if unsafe:
            paths = {
                "parent": "package/../../escape",
                "backslash": "package/folder\\..\\..\\escape",
                "drive": "package/C:/escape",
                "link": "package/link",
            }
            member = tarfile.TarInfo(paths[unsafe])
            if unsafe == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
            bundle.addfile(member)
    directory = root / "integrations/pi"
    target = directory / RELATIVE
    target.mkdir(parents=True)
    (target / "package.json").write_text('{"name":"brace-expansion","version":"5.0.9"}')
    (target / "index.js").write_bytes(b"original package bytes\n")
    (target.parent / "unrelated-package.txt").write_bytes(b"preserve\n")
    lock = {
        "packages": {
            RELATIVE: {
                "version": "5.0.12",
                "resolved": "https://registry.npmjs.org/brace-expansion/-/brace-expansion-5.0.12.tgz",
                "integrity": "sha512-"
                + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode(),
            }
        }
    }
    (directory / "package-lock.json").write_text(json.dumps(lock))
    return archive, target


def _run(root: Path, archive: Path) -> subprocess.CompletedProcess[str]:
    """Use the installed operator CLI with a digest-verified local archive."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--archive", str(archive)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def test_cli_replaces_only_the_locked_package_and_is_idempotent(tmp_path: Path) -> None:
    """Only the declared dependency changes and a repeated verification is stable."""
    archive, target = _fixture(tmp_path)
    before_lock = (tmp_path / "integrations/pi/package-lock.json").read_bytes()
    for _ in range(2):
        result = _run(tmp_path, archive)
        assert result.returncode == 0, result.stderr
        assert "brace-expansion 5.0.12" in result.stdout
        assert json.loads((target / "package.json").read_text())["version"] == "5.0.12"
        assert (target / "index.js").read_bytes() == b"export default 'reviewed';\n"
        assert (target.parent / "unrelated-package.txt").read_bytes() == b"preserve\n"
        assert (tmp_path / "integrations/pi/package-lock.json").read_bytes() == before_lock
        assert not list(target.parent.glob(".synapse-pin-*"))


@pytest.mark.parametrize("unsafe", ["parent", "backslash", "drive", "link"])
def test_cli_refuses_unsafe_archives_before_changing_installed_bytes(
    tmp_path: Path, unsafe: str
) -> None:
    """An authenticated archive still cannot escape its package directory."""
    archive, target = _fixture(tmp_path, unsafe=unsafe)
    before = (target / "index.js").read_bytes()
    result = _run(tmp_path, archive)
    assert result.returncode == 1
    assert "unsafe path" in result.stderr
    assert (target / "index.js").read_bytes() == before
    assert not (tmp_path / "escape").exists()


def test_cli_refuses_wrong_archive_digest(tmp_path: Path) -> None:
    """Modified registry bytes cannot replace the existing package."""
    archive, target = _fixture(tmp_path)
    archive.write_bytes(archive.read_bytes() + b"changed")
    result = _run(tmp_path, archive)
    assert result.returncode == 1
    assert "locked SHA-512" in result.stderr
    assert (target / "index.js").read_bytes() == b"original package bytes\n"


def test_cli_refuses_another_package_version(tmp_path: Path) -> None:
    """A matching archive digest cannot conceal the wrong package identity."""
    archive, target = _fixture(tmp_path, version="5.0.11")
    result = _run(tmp_path, archive)
    assert result.returncode == 1
    assert "different package identity" in result.stderr
    assert json.loads((target / "package.json").read_text())["version"] == "5.0.9"
