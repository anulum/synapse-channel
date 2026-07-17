# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed-wheel console-script integrity tests
"""Exercise console-script metadata, wrappers, origins, and a real built wheel."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import sysconfig
from importlib.metadata import EntryPoint
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from tools import check_installed_console_scripts as checker

import synapse_channel

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
TOOL = REPO_ROOT / "tools" / "check_installed_console_scripts.py"


def _source_module_root() -> Path:
    package_file = synapse_channel.__file__
    assert package_file is not None
    return Path(package_file).resolve().parent.parent


def _scripts_dir() -> Path:
    return Path(sysconfig.get_path("scripts"))


def test_current_distribution_loads_every_declared_console_script() -> None:
    expected = checker.declared_console_scripts(PYPROJECT)

    loaded = checker.verify_installed_console_scripts(
        checker.DEFAULT_DISTRIBUTION,
        expected,
        module_root=_source_module_root(),
        scripts_dir=_scripts_dir(),
    )

    assert {record.name: record.target for record in loaded} == expected
    assert all(record.wrapper.is_file() for record in loaded)


def test_console_script_check_rejects_declared_target_drift() -> None:
    expected = checker.declared_console_scripts(PYPROJECT)
    drifted = dict(expected)
    drifted["syn-commit"] = "synapse_channel.ergonomics:alias_ack"

    with pytest.raises(checker.ConsoleScriptCheckError, match="target drift"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            drifted,
            module_root=_source_module_root(),
            scripts_dir=_scripts_dir(),
        )


def test_console_script_check_rejects_undeclared_installed_script() -> None:
    expected = checker.declared_console_scripts(PYPROJECT)
    expected.pop("syn-commit")

    with pytest.raises(checker.ConsoleScriptCheckError, match="unexpected console scripts"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            expected,
            module_root=_source_module_root(),
            scripts_dir=_scripts_dir(),
        )


def test_console_script_check_rejects_missing_installed_script() -> None:
    expected = checker.declared_console_scripts(PYPROJECT)
    expected["syn-missing"] = "synapse_channel.ergonomics:alias_name"

    with pytest.raises(checker.ConsoleScriptCheckError, match="missing console scripts"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            expected,
            module_root=_source_module_root(),
            scripts_dir=_scripts_dir(),
        )


def test_console_script_check_rejects_missing_wrappers(tmp_path: Path) -> None:
    expected = checker.declared_console_scripts(PYPROJECT)

    with pytest.raises(checker.ConsoleScriptCheckError, match="no executable wrapper"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            expected,
            module_root=_source_module_root(),
            scripts_dir=tmp_path,
        )


def test_console_script_check_rejects_wrapper_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = checker.declared_console_scripts(PYPROJECT)
    outside = tmp_path / "outside-wrapper"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    outside.chmod(0o755)
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir()
    monkeypatch.setattr(
        "tools.check_installed_console_scripts.shutil.which",
        lambda *_args, **_kwargs: os.fspath(outside),
    )

    with pytest.raises(checker.ConsoleScriptCheckError, match="wrapper escaped"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            expected,
            module_root=_source_module_root(),
            scripts_dir=wrappers,
        )


def test_console_script_check_rejects_source_tree_origin(tmp_path: Path) -> None:
    expected = checker.declared_console_scripts(PYPROJECT)

    with pytest.raises(checker.ConsoleScriptCheckError, match="loaded outside"):
        checker.verify_installed_console_scripts(
            checker.DEFAULT_DISTRIBUTION,
            expected,
            module_root=tmp_path,
            scripts_dir=_scripts_dir(),
        )


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("[project]\nname = 'empty'\n", "no non-empty"),
        ("[project.scripts]\nsynapse = 7\n", "malformed console scripts"),
    ],
)
def test_declared_console_scripts_rejects_malformed_metadata(
    tmp_path: Path,
    content: str,
    error: str,
) -> None:
    metadata = tmp_path / "pyproject.toml"
    metadata.write_text(content, encoding="utf-8")

    with pytest.raises(checker.ConsoleScriptCheckError, match=error):
        checker.declared_console_scripts(metadata)


@pytest.mark.parametrize(
    "entries",
    [
        (),
        (
            EntryPoint(name="duplicate", value="sys:exit", group="console_scripts"),
            EntryPoint(name="duplicate", value="sys:exit", group="console_scripts"),
        ),
    ],
)
def test_console_script_check_rejects_empty_or_duplicate_installed_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: tuple[EntryPoint, ...],
) -> None:
    fake_distribution = SimpleNamespace(entry_points=entries)
    monkeypatch.setattr(checker, "distribution", lambda _name: fake_distribution)

    with pytest.raises(checker.ConsoleScriptCheckError):
        checker.verify_installed_console_scripts(
            "invalid-distribution",
            {},
            module_root=tmp_path,
            scripts_dir=tmp_path,
        )


@pytest.mark.parametrize(
    ("target", "error"),
    [
        ("missing_console_script_module:main", "failed to load"),
        ("installed_console_script_target:value", "not callable"),
    ],
)
def test_console_script_check_rejects_unloadable_or_noncallable_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    error: str,
) -> None:
    module = tmp_path / "installed_console_script_target.py"
    module.write_text("value = 7\n", encoding="utf-8")
    monkeypatch.syspath_prepend(tmp_path)
    wrapper = tmp_path / "script"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    entry = EntryPoint(name="script", value=target, group="console_scripts")
    fake_distribution = SimpleNamespace(entry_points=(entry,))
    monkeypatch.setattr(checker, "distribution", lambda _name: fake_distribution)

    with pytest.raises(checker.ConsoleScriptCheckError, match=error):
        checker.verify_installed_console_scripts(
            "invalid-target-distribution",
            {"script": target},
            module_root=tmp_path,
            scripts_dir=tmp_path,
        )


def test_console_script_check_rejects_target_module_without_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = tmp_path / "script"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    entry = EntryPoint(name="script", value="sys:exit", group="console_scripts")
    fake_distribution = SimpleNamespace(entry_points=(entry,))
    monkeypatch.setattr(checker, "distribution", lambda _name: fake_distribution)

    with pytest.raises(checker.ConsoleScriptCheckError, match="has no file origin"):
        checker.verify_installed_console_scripts(
            "originless-target-distribution",
            {"script": "sys:exit"},
            module_root=tmp_path,
            scripts_dir=tmp_path,
        )


def test_console_script_check_ignores_namespace_module_without_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = checker.declared_console_scripts(PYPROJECT)
    monkeypatch.setitem(sys.modules, "synapse_channel.namespace_only", ModuleType("namespace_only"))

    loaded = checker.verify_installed_console_scripts(
        checker.DEFAULT_DISTRIBUTION,
        expected,
        module_root=_source_module_root(),
        scripts_dir=_scripts_dir(),
    )

    assert len(loaded) == len(expected)


def test_toml_loader_falls_back_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def without_stdlib_toml(module_name: str) -> ModuleType:
        if module_name == "tomllib":
            raise ModuleNotFoundError(name=module_name)
        return real_import(module_name)

    monkeypatch.setattr(importlib, "import_module", without_stdlib_toml)
    assert checker._load_toml_module().loads("[project]\nname='x'\n")["project"]["name"] == "x"

    def without_toml(module_name: str) -> ModuleType:
        raise ModuleNotFoundError(name=module_name)

    monkeypatch.setattr(importlib, "import_module", without_toml)
    with pytest.raises(ModuleNotFoundError, match="tomllib or tomli"):
        checker._load_toml_module()

    def missing_nested_dependency(module_name: str) -> ModuleType:
        raise ModuleNotFoundError(f"{module_name} dependency missing", name="nested-dependency")

    monkeypatch.setattr(importlib, "import_module", missing_nested_dependency)
    with pytest.raises(ModuleNotFoundError, match="dependency missing"):
        checker._load_toml_module()


def test_console_script_check_cli_reports_success_and_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = [
        "--project-metadata",
        os.fspath(PYPROJECT),
        "--site-packages",
        os.fspath(_source_module_root()),
        "--scripts-dir",
        os.fspath(_scripts_dir()),
    ]

    assert checker.main(common) == 0
    assert "13 scripts loaded" in capsys.readouterr().out
    assert checker.main(["--distribution", "missing-distribution", *common]) == 1
    assert "No package metadata was found" in capsys.readouterr().err


@pytest.fixture(scope="module")
def installed_wheel_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("installed-wheel-console-scripts")
    dist_dir = root / "dist"
    build_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            os.fspath(dist_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert build_result.returncode == 0, build_result.stderr
    wheel = next(dist_dir.glob("*.whl"))
    environment = root / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", os.fspath(environment)],
        check=True,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    install_result = subprocess.run(
        [
            os.fspath(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--force-reinstall",
            os.fspath(wheel),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert install_result.returncode == 0, install_result.stderr
    return python


def test_real_built_wheel_loads_all_scripts_from_its_site_packages(
    installed_wheel_python: Path,
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            os.fspath(installed_wheel_python),
            os.fspath(TOOL),
            "--project-metadata",
            os.fspath(PYPROJECT),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "installed-console-scripts OK: 13 scripts loaded" in result.stdout
