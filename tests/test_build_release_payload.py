# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the release recipe gives the same bytes whatever the caller's checkout
"""The release payload is built from the commit, with pinned tools, under one umask.

Every test builds a small setuptools project in a throwaway repository that has its
own committer identity; nothing is fetched, and the build runs without isolation
against the interpreter's own ``build``, ``setuptools`` and ``wheel``.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
import runpy
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "build_release_payload.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_release_payload", TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_release_payload"] = module
    spec.loader.exec_module(module)
    return module


TOOL = _load()
COMMIT_DATE = "2026-11-13T08:00:00+00:00"
EPOCH = int(datetime.fromisoformat(COMMIT_DATE).timestamp())


def _git(repository: Path, *args: str) -> str:
    environment = {**os.environ, "GIT_COMMITTER_DATE": COMMIT_DATE, "GIT_AUTHOR_DATE": COMMIT_DATE}
    done = subprocess.run(
        ["git", "-C", str(repository), *args],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _pins() -> str:
    lines = []
    for name in ("build", "setuptools", "wheel"):
        lines.append(
            f"{name}=={importlib.metadata.version(name)} \\\n    --hash=sha256:{'0' * 64}\n"
        )
    return "".join(lines)


def _project(
    root: Path, *, pins: str | None = None, pyproject: str | None = None, with_pins: bool = True
) -> Path:
    """Create and commit a tiny project; the repository carries its own identity."""
    repository = root / "project"
    repository.mkdir(parents=True)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "payload test")
    _git(repository, "config", "user.email", "payload-test@example.invalid")
    files = {
        "pyproject.toml": pyproject
        or (
            '[build-system]\nrequires = ["setuptools>=68", "wheel"]\n'
            'build-backend = "setuptools.build_meta"\n\n'
            '[project]\nname = "tiny-payload"\nversion = "1.0.0"\n\n'
            '[tool.setuptools]\npackages = ["tiny_payload"]\n'
        ),
        "tiny_payload/__init__.py": '"""A tiny package."""\n',
        "tiny_payload/helper.py": "VALUE = 1\n",
    }
    if with_pins:
        files[TOOL.REQUIREMENTS] = _pins() if pins is None else pins
    for name, text in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "tiny")
    return repository


@contextmanager
def _umask(value: int) -> Iterator[None]:
    previous = os.umask(value)
    try:
        yield
    finally:
        os.umask(previous)


def test_the_payload_is_the_same_whatever_the_caller_s_umask_path_or_working_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _project(tmp_path / "a")
    with _umask(0o002):
        first = TOOL.build_payload(repository, "HEAD", tmp_path / "out-1", tmp_path / "work-1")
    with _umask(0o077):
        clone = tmp_path / "b" / "deeper" / "clone"
        _git(tmp_path, "clone", "-q", str(repository), str(clone))
    (clone / "tiny_payload" / "helper.py").write_text("VALUE = 2  # not committed\n", "utf-8")
    with _umask(0o077):
        second = TOOL.build_payload(clone, "HEAD", tmp_path / "out-2", tmp_path / "work-2")
    printed = capsys.readouterr().out.splitlines()
    commit = _git(repository, "rev-parse", "HEAD")
    assert first == second
    assert [line.split("  ")[1] for line in first] == [
        "tiny_payload-1.0.0-py3-none-any.whl",
        "tiny_payload-1.0.0.tar.gz",
    ]
    assert (tmp_path / "out-1" / "SHA256SUMS").read_text("utf-8") == "".join(
        f"{line}\n" for line in first
    )
    assert printed[0] == f"commit {commit}, SOURCE_DATE_EPOCH {EPOCH}"
    assert printed[1] == ", ".join(
        f"{name} {importlib.metadata.version(name)}" for name in ("build", "setuptools", "wheel")
    )
    assert printed[2:4] == first


def test_the_archives_carry_the_commit_time_and_fixed_modes(tmp_path: Path) -> None:
    repository = _project(tmp_path)
    out = tmp_path / "out"
    with _umask(0o077):
        TOOL.build_payload(repository, "HEAD", out, tmp_path / "work")
    with zipfile.ZipFile(out / "tiny_payload-1.0.0-py3-none-any.whl") as wheel:
        infos = wheel.infolist()
        helper = wheel.read("tiny_payload/helper.py")
    with tarfile.open(out / "tiny_payload-1.0.0.tar.gz") as sdist:
        members = sdist.getmembers()
    assert helper == b"VALUE = 1\n"
    assert {info.date_time for info in infos} == {time.gmtime(EPOCH)[:6]}
    package = [info for info in infos if info.filename.startswith("tiny_payload/")]
    assert {(info.external_attr >> 16) & 0o777 for info in package} == {0o644}
    assert {member.mtime for member in members} == {EPOCH}


def test_the_build_installs_nothing_and_needs_no_package_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The pinned tools of the running interpreter build the payload; an isolated
    # build would have to install its backend, and here no index may be reached.
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/unreachable")
    repository = _project(tmp_path)
    lines = TOOL.build_payload(repository, "HEAD", tmp_path / "out", tmp_path / "work")
    assert len(lines) == 2


def test_a_ref_that_names_no_commit_and_a_used_output_are_refused(tmp_path: Path) -> None:
    repository = _project(tmp_path)
    with pytest.raises(TOOL.ReleasePayloadError) as unknown:
        TOOL.build_payload(repository, "v9.9.9", tmp_path / "out", tmp_path / "work")
    used = tmp_path / "used"
    used.mkdir()
    (used / "old.whl").write_bytes(b"")
    with pytest.raises(TOOL.ReleasePayloadError) as occupied:
        TOOL.build_payload(repository, "HEAD", used, tmp_path / "work")
    afile = tmp_path / "afile"
    afile.write_text("x", "utf-8")
    with pytest.raises(TOOL.ReleasePayloadError) as not_a_directory:
        TOOL.build_payload(repository, "HEAD", afile, tmp_path / "work")
    with pytest.raises(TOOL.ReleasePayloadError) as scratch:
        TOOL.build_payload(repository, "HEAD", tmp_path / "out", used)
    assert str(unknown.value) == f"the ref 'v9.9.9' does not name a commit in {repository}"
    assert str(scratch.value) == f"the scratch directory {used} is not empty"
    assert str(occupied.value) == f"the output directory {used} is not empty"
    assert str(not_a_directory.value) == f"the output directory {afile} is not empty"


def test_build_tools_other_than_the_pins_are_refused_before_any_build(tmp_path: Path) -> None:
    repository = _project(tmp_path)
    commit = _git(repository, "rev-parse", "HEAD")
    tree = tmp_path / "tree"
    TOOL.export_tree(repository, commit, tree, tmp_path / "index")
    pins = TOOL.pinned_versions((tree / TOOL.REQUIREMENTS).read_text("utf-8"))

    def older(name: str) -> str:
        if name == "wheel":
            raise importlib.metadata.PackageNotFoundError(name)
        return "0.1" if name == "setuptools" else pins[name]

    with pytest.raises(TOOL.ReleasePayloadError) as differ:
        TOOL.check_tools(tree, commit, older)
    assert str(differ.value) == (
        f"the build tools differ from the pins of {commit}: "
        f"setuptools 0.1 is installed, {pins['setuptools']} pinned; "
        f"wheel is not installed, {pins['wheel']} pinned; "
        "install them with: python -m pip install --require-hashes -r "
        ".github/requirements/requirements-tools.txt"
    )
    assert not (tmp_path / "raw-dist").exists()


def test_a_tree_without_pins_or_with_a_tool_unpinned_is_refused(tmp_path: Path) -> None:
    bare = _project(tmp_path / "bare", with_pins=False)
    with pytest.raises(TOOL.ReleasePayloadError) as missing:
        TOOL.build_payload(bare, "HEAD", tmp_path / "out-1", tmp_path / "work-1")
    commit = _git(bare, "rev-parse", "HEAD")
    partial = _project(tmp_path / "partial", pins="build==1.6.1\nwheel==0.48.0\n")
    with pytest.raises(TOOL.ReleasePayloadError) as unpinned:
        TOOL.build_payload(partial, "HEAD", tmp_path / "out-2", tmp_path / "work-2")
    assert (
        str(missing.value) == f".github/requirements/requirements-tools.txt is missing at {commit}"
    )
    assert str(unpinned.value) == (
        ".github/requirements/requirements-tools.txt pins no version of setuptools"
    )


def test_pins_are_read_from_hash_pinned_requirement_lines() -> None:
    text = (
        "# comment\nBuild==1.6.1 \\\n    --hash=sha256:aa\n"
        "setuptools==84.2.1 ; python_version>'3'\nwheel==0.48.0\\\nother>=1\n"
    )
    assert TOOL.pinned_versions(text) == {
        "build": "1.6.1",
        "setuptools": "84.2.1",
        "wheel": "0.48.0",
    }


def test_a_failing_build_and_a_wrong_number_of_archives_are_refused(tmp_path: Path) -> None:
    broken = _project(tmp_path / "broken", pyproject="[build-system\n")
    with pytest.raises(TOOL.ReleasePayloadError) as failed:
        TOOL.build_payload(broken, "HEAD", tmp_path / "out", tmp_path / "work")
    log = tmp_path / "work" / "build.log"
    assert str(failed.value) == f"the build failed (exit 1); its output is in {log}"
    assert log.read_text("utf-8")
    repository = _project(tmp_path / "good")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = tmp_path / "tree"
    TOOL.export_tree(repository, commit, tree, tmp_path / "index")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "stale-0.0.0-py3-none-any.whl").write_bytes(b"")
    with pytest.raises(TOOL.ReleasePayloadError) as counted:
        TOOL.run_build(tree, raw, EPOCH, tmp_path / "log")
    assert str(counted.value) == (
        "the build produced 2 wheel(s) and 1 source archive(s); expected one of each"
    )


def test_git_errors_are_reported_and_a_missing_git_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _project(tmp_path)
    with pytest.raises(TOOL.ReleasePayloadError) as unreadable:
        TOOL.export_tree(repository, "0" * 40, tmp_path / "tree", tmp_path / "index")
    assert str(unreadable.value).startswith(f"git read-tree failed for {'0' * 40}: ")
    monkeypatch.setattr(TOOL.shutil, "which", lambda _name: None)
    with pytest.raises(TOOL.ReleasePayloadError) as missing:
        TOOL.resolve_commit(repository, "HEAD")
    assert str(missing.value) == "git is not on PATH"


def test_the_command_builds_into_out_and_refuses_with_status_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _project(tmp_path)
    work = tmp_path / "scratch"
    argv = ["--repository", str(repository), "--out", str(tmp_path / "out"), "--work", str(work)]
    assert TOOL.main(argv) == 0
    assert (tmp_path / "out" / "SHA256SUMS").is_file()
    assert list(work.iterdir()) == []
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    again = ["--repository", str(repository), "--out", str(tmp_path / "out")]
    assert TOOL.main(again) == 1
    assert capsys.readouterr().err == (
        f"build_release_payload: the output directory {tmp_path / 'out'} is not empty\n"
    )


def test_the_tool_runs_as_a_script_and_as_a_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _project(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    argv = [str(TOOL_PATH), "--repository", str(repository), "--out", str(tmp_path / "out")]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as finished:
        runpy.run_path(str(TOOL_PATH), run_name="__main__")
    monkeypatch.syspath_prepend(str(ROOT))
    packaged = importlib.import_module("tools.build_release_payload")
    assert finished.value.code == 0
    assert packaged.REQUIREMENTS == TOOL.REQUIREMENTS
