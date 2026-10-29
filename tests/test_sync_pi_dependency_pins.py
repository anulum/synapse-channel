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
import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools/sync_pi_dependency_pins.py"
RELATIVE = "node_modules/@earendil-works/pi-coding-agent/node_modules/"
VERSIONS = {"brace-expansion": "5.0.12", "undici": "8.12.3"}


def _fixture(
    root: Path,
    *,
    version: str | None = None,
    unsafe: str = "",
    dependency: str = "brace-expansion",
    content: bytes = b"export default 'reviewed';\n",
) -> tuple[Path, Path]:
    """Create a real registry-shaped archive and an existing installed package."""
    archive = root / "registry.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, data in {
            "package/package.json": json.dumps(
                {"name": dependency, "version": version or VERSIONS[dependency]}
            ).encode(),
            "package/index.js": content,
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
                "absolute": "/package/escape",
                "prefix": "foreign/escape",
                "duplicate": "package/index.js",
                "root-file": "package",
                "directory": "package/lib",
            }
            member = tarfile.TarInfo(paths[unsafe])
            if unsafe == "link":
                member.type = tarfile.SYMTYPE
                member.linkname = "../../outside"
            if unsafe == "directory":
                member.type = tarfile.DIRTYPE
            bundle.addfile(member)
    directory = root / "integrations/pi"
    relative = RELATIVE + dependency
    target = directory / relative
    target.mkdir(parents=True)
    previous = "5.0.9" if dependency == "brace-expansion" else "8.10.2"
    (target / "package.json").write_text(json.dumps({"name": dependency, "version": previous}))
    (target / "index.js").write_bytes(b"original package bytes\n")
    (target.parent / "unrelated-package.txt").write_bytes(b"preserve\n")
    lock = {
        "packages": {
            relative: {
                "version": VERSIONS[dependency],
                "resolved": (
                    f"https://registry.npmjs.org/{dependency}/-/"
                    f"{dependency}-{VERSIONS[dependency]}.tgz"
                ),
                "integrity": "sha512-"
                + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode(),
            }
        }
    }
    (directory / "package-lock.json").write_text(json.dumps(lock))
    return archive, target


def _run(
    root: Path, archive: Path, *, dependency: str = "brace-expansion"
) -> subprocess.CompletedProcess[str]:
    """Use the installed operator CLI with a digest-verified local archive."""
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--archive",
            str(archive),
            "--dependency",
            dependency,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize("dependency", list(VERSIONS))
def test_cli_replaces_only_the_locked_package_and_is_idempotent(
    tmp_path: Path, dependency: str
) -> None:
    """Only the declared dependency changes and a repeated verification is stable."""
    archive, target = _fixture(tmp_path, dependency=dependency)
    before_lock = (tmp_path / "integrations/pi/package-lock.json").read_bytes()
    for _ in range(2):
        result = _run(tmp_path, archive, dependency=dependency)
        assert result.returncode == 0, result.stderr
        assert f"{dependency} {VERSIONS[dependency]}" in result.stdout
        assert json.loads((target / "package.json").read_text())["version"] == VERSIONS[dependency]
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


@pytest.mark.parametrize(
    ("unsafe", "diagnostic"),
    [
        ("absolute", "unexpected package path"),
        ("prefix", "unexpected package path"),
        ("duplicate", "duplicate or oversized"),
        ("root-file", "duplicate or oversized"),
    ],
)
def test_cli_refuses_ambiguous_archive_paths_without_changing_a_package(
    tmp_path: Path, unsafe: str, diagnostic: str
) -> None:
    """Authenticated malformed tar names cannot overwrite or alias installed files."""
    archive, target = _fixture(tmp_path, unsafe=unsafe, dependency="undici")
    before = (target / "index.js").read_bytes()
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 1 and diagnostic in result.stderr
    assert (target / "index.js").read_bytes() == before
    assert not list(target.parent.glob(".synapse-pin-*"))


@pytest.mark.parametrize("dependency", list(VERSIONS))
def test_cli_uses_a_separate_unpacked_budget_for_each_reviewed_dependency(
    tmp_path: Path, dependency: str
) -> None:
    """The larger Undici release fits its bound without widening the brace bound."""
    content = b"x" * (2 * 1024 * 1024 + 65_536)
    archive, target = _fixture(tmp_path, dependency=dependency, content=content)
    result = _run(tmp_path, archive, dependency=dependency)
    if dependency == "undici":
        assert result.returncode == 0, result.stderr
        assert (target / "index.js").read_bytes() == content
    else:
        assert result.returncode == 1 and "oversized file" in result.stderr
        assert (target / "index.js").read_bytes() == b"original package bytes\n"
    assert not list(target.parent.glob(".synapse-pin-*"))


@pytest.mark.parametrize("field", ["version", "resolved", "integrity-prefix", "integrity-length"])
def test_cli_refuses_unreviewed_lock_identity_or_integrity_before_installing(
    tmp_path: Path, field: str
) -> None:
    """A local lock mutation cannot change the reviewed registry identity or digest format."""
    archive, target = _fixture(tmp_path, dependency="undici")
    path = tmp_path / "integrations/pi/package-lock.json"
    lock = json.loads(path.read_text())
    entry = lock["packages"][RELATIVE + "undici"]
    if field in {"version", "resolved"}:
        entry[field] = "unreviewed"
    elif field == "integrity-prefix":
        entry["integrity"] = "sha256-" + "a" * 64
    else:
        entry["integrity"] = "sha512-" + base64.b64encode(b"too-short").decode()
    path.write_text(json.dumps(lock))
    before = (target / "index.js").read_bytes()
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 1 and "Pi dependency pin refused:" in result.stderr
    assert (target / "index.js").read_bytes() == before


def test_cli_requires_an_explicit_dependency_for_a_single_archive(tmp_path: Path) -> None:
    """One offline archive cannot silently stand in for the complete reviewed dependency set."""
    archive, target = _fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp_path), "--archive", str(archive)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2 and "one explicit --dependency" in result.stderr
    assert (target / "index.js").read_bytes() == b"original package bytes\n"


def test_cli_replaces_an_unexpected_installed_symlink_without_following_it(tmp_path: Path) -> None:
    """A verified file set cannot conceal an additional installed link to foreign bytes."""
    archive, target = _fixture(tmp_path, dependency="undici")
    first = _run(tmp_path, archive, dependency="undici")
    assert first.returncode == 0, first.stderr
    outside = tmp_path / "foreign"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"foreign bytes preserved")
    os.symlink(outside, target / "unexpected-link", target_is_directory=True)
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 0, result.stderr
    assert not (target / "unexpected-link").exists()
    assert (outside / "sentinel").read_bytes() == b"foreign bytes preserved"
    assert (target / "index.js").read_bytes() == b"export default 'reviewed';\n"


def test_cli_accepts_directory_records_and_empty_regular_files(tmp_path: Path) -> None:
    """Valid tar directories do not become files or require executable content."""
    archive, target = _fixture(tmp_path, dependency="undici", unsafe="directory", content=b"")
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 0, result.stderr
    assert (target / "index.js").read_bytes() == b""
    assert json.loads((target / "package.json").read_text())["version"] == "8.12.3"


@pytest.mark.parametrize("bound", ["compressed", "expanded", "member-count", "unpacked"])
def test_cli_bounds_authenticated_archive_work_before_installing(
    tmp_path: Path, bound: str
) -> None:
    """A matching digest does not authorize unbounded decompression, members or bytes."""
    archive, target = _fixture(tmp_path, dependency="undici")
    if bound == "compressed":
        archive.write_bytes(os.urandom(2 * 1024 * 1024 + 1))
    elif bound == "expanded":
        archive.write_bytes(
            gzip.compress(gzip.decompress(archive.read_bytes()) + b"\0" * 6_291_456)
        )
    else:
        with tarfile.open(archive, "w:gz") as bundle:
            manifest = json.dumps({"name": "undici", "version": "8.12.3"}).encode()
            member = tarfile.TarInfo("package/package.json")
            member.size = len(manifest)
            bundle.addfile(member, io.BytesIO(manifest))
            for index in range(1025 if bound == "member-count" else 2):
                member = tarfile.TarInfo(f"package/item-{index}")
                if bound == "member-count":
                    member.type = tarfile.DIRTYPE
                    bundle.addfile(member)
                else:
                    content = b"x" * (2 * 1024 * 1024 + 65_536)
                    member.size = len(content)
                    bundle.addfile(member, io.BytesIO(content))
    path = tmp_path / "integrations/pi/package-lock.json"
    lock = json.loads(path.read_text())
    lock["packages"][RELATIVE + "undici"]["integrity"] = (
        "sha512-" + base64.b64encode(hashlib.sha512(archive.read_bytes()).digest()).decode()
    )
    path.write_text(json.dumps(lock))
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 1 and "Pi dependency pin refused:" in result.stderr
    diagnostic = {
        "compressed": "locked SHA-512",
        "expanded": "expanded archive limit",
        "member-count": "member limit",
        "unpacked": "unpacked byte limit",
    }[bound]
    assert diagnostic in result.stderr
    assert (target / "index.js").read_bytes() == b"original package bytes\n"
    assert not list(target.parent.glob(".synapse-pin-*"))


@pytest.mark.parametrize("collision", [False, True])
def test_public_replacement_restores_or_retains_original_after_real_rename_failure(
    tmp_path: Path, collision: bool
) -> None:
    """Real interrupted publication never deletes the only original package copy."""
    archive, target = _fixture(tmp_path, dependency="undici")
    worker = r"""
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import FrameType

script, root_text, archive_text, collision_text = sys.argv[1:]
root = Path(root_text)
archive = Path(archive_text)
collision = collision_text == "True"
spec = importlib.util.spec_from_file_location("reviewed_pi_pins", script)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
target = root / "integrations/pi/node_modules/@earendil-works/pi-coding-agent/node_modules/undici"
changed = False


def observe(frame: FrameType, event: str, argument: object) -> None:
    "Interrupt the actual completed rename before the next publication operation."
    global changed
    if changed or event != "c_return" or argument is not os.rename:
        return
    if (
        frame.f_globals.get("__name__") != "reviewed_pi_pins"
        or frame.f_code.co_name != "_replace_directory"
    ):
        return
    original = frame.f_locals["original"]
    assert isinstance(original, Path)
    if not original.exists() or target.exists():
        return
    changed = True
    if collision:
        target.mkdir()
        (target / "successor").write_bytes(b"preserve successor")
    else:
        os.rename(frame.f_locals["staging"], root / "interrupted-staging")


sys.setprofile(observe)
try:
    try:
        module.synchronize(root, archive, dependency="undici")
    except OSError:
        refused = True
    else:
        refused = False
finally:
    sys.setprofile(None)
print(json.dumps({"changed": changed, "refused": refused}))
"""
    result = subprocess.run(
        [sys.executable, "-c", worker, str(SCRIPT), str(tmp_path), str(archive), str(collision)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"changed": True, "refused": True}
    directories = list(target.parent.glob(".synapse-pin-*"))
    if collision:
        assert (target / "successor").read_bytes() == b"preserve successor"
        assert len(directories) == 1
        assert (directories[0] / "original/index.js").read_bytes() == b"original package bytes\n"
    else:
        assert (target / "index.js").read_bytes() == b"original package bytes\n"
        assert directories == []


@pytest.mark.parametrize("kind", ["missing", "regular-file", "directory-symlink", "parent-symlink"])
def test_cli_refuses_a_non_directory_installation_target(tmp_path: Path, kind: str) -> None:
    """A reviewed archive cannot replace a foreign leaf or traverse a linked package parent."""
    archive, target = _fixture(tmp_path, dependency="undici")
    original = target.with_name("preserved-original")
    target.rename(original)
    if kind == "regular-file":
        target.write_bytes(b"foreign leaf preserved")
    elif kind == "directory-symlink":
        target.symlink_to(original, target_is_directory=True)
    elif kind == "parent-symlink":
        target.parent.rename(target.parent.with_name("preserved-parent"))
        target.parent.symlink_to(
            target.parent.with_name("preserved-parent"), target_is_directory=True
        )
        original = target.parent / "preserved-original"
        original.rename(target)
    result = _run(tmp_path, archive, dependency="undici")
    assert result.returncode == 1 and "installed ordinary package directory" in result.stderr
    preserved = target if kind == "parent-symlink" else original
    assert (preserved / "index.js").read_bytes() == b"original package bytes\n"
    if kind == "regular-file":
        assert target.read_bytes() == b"foreign leaf preserved"
    elif kind == "directory-symlink":
        assert target.is_symlink() and target.resolve() == original
    elif kind == "missing":
        assert not target.exists()
    else:
        assert target.parent.is_symlink()
    assert not list(target.parent.glob(".synapse-pin-*"))
