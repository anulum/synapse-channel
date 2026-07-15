# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the wheel/sdist parity release-integrity check
"""Exercise the wheel/sdist parity contract: matching modules, tests refusal."""

from __future__ import annotations

import io
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tools.check_wheel_sdist_parity import main, parity_problems
else:
    # ``tools`` is release/build tooling outside the installed package, so the
    # repository root must be on the path before importing it (the same shim the
    # other tools-backed tests use).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.check_wheel_sdist_parity import main, parity_problems


def _make_wheel(path: Path, modules: tuple[str, ...]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for module in modules:
            archive.writestr(f"synapse_channel/{module}", b"# module\n")
        archive.writestr("synapse_channel-0.1.dist-info/METADATA", b"Name: synapse-channel\n")
    return path


def _make_sdist(path: Path, modules: tuple[str, ...], *, tests: tuple[str, ...] = ()) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for module in modules:
            _add(archive, f"synapse_channel-0.1/src/synapse_channel/{module}", b"# module\n")
        for test in tests:
            _add(archive, f"synapse_channel-0.1/tests/{test}", b"# test\n")
        _add(archive, "synapse_channel-0.1/PKG-INFO", b"Name: synapse-channel\n")
    return path


def _add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


def test_matching_wheel_and_sdist_have_no_problems(tmp_path: Path) -> None:
    wheel = _make_wheel(tmp_path / "w.whl", ("__init__.py", "foo.py"))
    sdist = _make_sdist(tmp_path / "s.tar.gz", ("__init__.py", "foo.py"))
    assert parity_problems(wheel, sdist) == []
    assert main([str(wheel), str(sdist)]) == 0


def test_refuses_a_sdist_that_ships_tests(tmp_path: Path) -> None:
    wheel = _make_wheel(tmp_path / "w.whl", ("__init__.py",))
    sdist = _make_sdist(tmp_path / "s.tar.gz", ("__init__.py",), tests=("test_x.py",))
    problems = parity_problems(wheel, sdist)
    assert any("tests/" in problem for problem in problems)
    assert main([str(wheel), str(sdist)]) == 1


def test_flags_a_module_missing_from_the_sdist(tmp_path: Path) -> None:
    wheel = _make_wheel(tmp_path / "w.whl", ("__init__.py", "foo.py"))
    sdist = _make_sdist(tmp_path / "s.tar.gz", ("__init__.py",))
    assert any("missing package modules" in p for p in parity_problems(wheel, sdist))


def test_flags_a_module_only_in_the_sdist(tmp_path: Path) -> None:
    wheel = _make_wheel(tmp_path / "w.whl", ("__init__.py",))
    sdist = _make_sdist(tmp_path / "s.tar.gz", ("__init__.py", "stowaway.py"))
    assert any("absent from the wheel" in p for p in parity_problems(wheel, sdist))


def test_flags_a_wheel_with_no_package_modules(tmp_path: Path) -> None:
    wheel = tmp_path / "w.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("synapse_channel-0.1.dist-info/METADATA", b"Name: x\n")
    sdist = _make_sdist(tmp_path / "s.tar.gz", ())
    assert any("no synapse_channel package modules" in p for p in parity_problems(wheel, sdist))


def test_main_reports_a_usage_error_for_wrong_argument_count(tmp_path: Path) -> None:
    assert main([str(tmp_path / "only-one.whl")]) == 2


def test_main_prints_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    wheel = _make_wheel(tmp_path / "w.whl", ("__init__.py",))
    sdist = _make_sdist(tmp_path / "s.tar.gz", ("__init__.py",))
    assert main([str(wheel), str(sdist)]) == 0
    assert "distribution-integrity OK" in capsys.readouterr().out
