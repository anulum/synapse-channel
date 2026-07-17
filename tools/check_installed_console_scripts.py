#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — installed-wheel console-script integrity check
"""Load every declared console script from an installed distribution.

Source-tree imports can hide an incomplete wheel: one entry point may work while
another references a module that the artifact omitted. This validator compares
the installed distribution metadata with ``pyproject.toml``, requires every
generated wrapper, loads every target, and proves all loaded package modules came
from the selected interpreter's site-packages directory.
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import sys
import sysconfig
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import EntryPoint, PackageNotFoundError, distribution
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

DEFAULT_DISTRIBUTION = "synapse-channel"


class _TomlModule(Protocol):
    TOMLDecodeError: type[Exception]
    loads: Callable[[str], dict[str, Any]]


def _load_toml_module() -> _TomlModule:
    for module_name in ("tomllib", "tomli"):
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        return cast(_TomlModule, module)
    raise ModuleNotFoundError("tomllib or tomli is required", name="tomli")


tomllib = _load_toml_module()


class ConsoleScriptCheckError(RuntimeError):
    """Report one or more installed console-script contract failures."""


@dataclass(frozen=True)
class LoadedConsoleScript:
    """Record one wrapper and callable loaded from the installed distribution."""

    name: str
    target: str
    wrapper: Path
    module_origin: Path


def declared_console_scripts(pyproject_path: Path) -> dict[str, str]:
    """Return the canonical ``[project.scripts]`` mapping.

    Parameters
    ----------
    pyproject_path : pathlib.Path
        Project metadata whose script declarations define the expected wheel
        surface.

    Returns
    -------
    dict[str, str]
        Console-script name to ``module:callable`` target.

    Raises
    ------
    ConsoleScriptCheckError
        If the project or scripts table is absent, empty, or malformed.
    """
    data: dict[str, Any] = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict) or not scripts:
        raise ConsoleScriptCheckError("pyproject has no non-empty [project.scripts] table")
    malformed = [
        repr(name)
        for name, target in scripts.items()
        if not isinstance(name, str) or not name or not isinstance(target, str) or not target
    ]
    if malformed:
        raise ConsoleScriptCheckError(f"pyproject has malformed console scripts: {malformed}")
    return {str(name): str(target) for name, target in scripts.items()}


def _installed_console_scripts(distribution_name: str) -> dict[str, EntryPoint]:
    entries = [
        entry
        for entry in distribution(distribution_name).entry_points
        if entry.group == "console_scripts"
    ]
    scripts: dict[str, EntryPoint] = {}
    duplicates: list[str] = []
    for entry in entries:
        if entry.name in scripts:
            duplicates.append(entry.name)
        scripts[entry.name] = entry
    if duplicates:
        raise ConsoleScriptCheckError(
            f"installed distribution has duplicate console scripts: {sorted(set(duplicates))}"
        )
    if not scripts:
        raise ConsoleScriptCheckError("installed distribution declares no console scripts")
    return scripts


def _module_origin(module_name: str) -> Path:
    module: ModuleType | None = sys.modules.get(module_name)
    module_file = getattr(module, "__file__", None)
    if not isinstance(module_file, str):
        raise ConsoleScriptCheckError(f"loaded target module {module_name!r} has no file origin")
    return Path(module_file).resolve(strict=True)


def _outside(path: Path, root: Path) -> bool:
    return not path.is_relative_to(root)


def verify_installed_console_scripts(
    distribution_name: str,
    expected: Mapping[str, str],
    *,
    module_root: Path,
    scripts_dir: Path,
) -> tuple[LoadedConsoleScript, ...]:
    """Verify and load the complete installed console-script surface.

    Parameters
    ----------
    distribution_name : str
        Installed distribution queried through :mod:`importlib.metadata`.
    expected : Mapping[str, str]
        Canonical script-to-target mapping from project metadata.
    module_root : pathlib.Path
        Directory every loaded package module must reside beneath.
    scripts_dir : pathlib.Path
        Directory that must contain each generated executable wrapper.

    Returns
    -------
    tuple[LoadedConsoleScript, ...]
        Verified scripts sorted by command name.

    Raises
    ------
    ConsoleScriptCheckError
        If declarations drift, a wrapper is absent, a target cannot load, or
        any package module resolves outside ``module_root``.
    """
    root = module_root.resolve(strict=True)
    wrappers = scripts_dir.resolve(strict=True)
    installed = _installed_console_scripts(distribution_name)
    problems: list[str] = []

    missing = sorted(set(expected) - set(installed))
    extra = sorted(set(installed) - set(expected))
    if missing:
        problems.append(f"installed distribution is missing console scripts: {missing}")
    if extra:
        problems.append(f"installed distribution has unexpected console scripts: {extra}")

    loaded: list[LoadedConsoleScript] = []
    for name, entry in sorted(installed.items()):
        declared_target = expected.get(name)
        if declared_target is not None and entry.value != declared_target:
            problems.append(
                f"console script {name!r} target drift: installed {entry.value!r}, "
                f"expected {declared_target!r}"
            )
        wrapper_text = shutil.which(name, path=os.fspath(wrappers))
        if wrapper_text is None:
            problems.append(f"console script {name!r} has no executable wrapper in {wrappers}")
            continue
        wrapper = Path(wrapper_text).resolve(strict=True)
        if _outside(wrapper, wrappers):
            problems.append(f"console script {name!r} wrapper escaped {wrappers}: {wrapper}")
            continue
        try:
            target = entry.load()
            origin = _module_origin(entry.module)
        except Exception as exc:
            problems.append(f"console script {name!r} failed to load: {type(exc).__name__}: {exc}")
            continue
        if not callable(target):
            problems.append(f"console script {name!r} target {entry.value!r} is not callable")
        if _outside(origin, root):
            problems.append(f"console script {name!r} loaded outside {root}: {origin}")
        loaded.append(LoadedConsoleScript(name, entry.value, wrapper, origin))

    leaked_modules: list[str] = []
    for module_name, module in sorted(sys.modules.items()):
        if module_name != "synapse_channel" and not module_name.startswith("synapse_channel."):
            continue
        module_file = getattr(module, "__file__", None)
        if isinstance(module_file, str):
            origin = Path(module_file).resolve(strict=True)
            if _outside(origin, root):
                leaked_modules.append(f"{module_name}={origin}")
    if leaked_modules:
        problems.append(f"package modules loaded outside {root}: {leaked_modules}")
    if problems:
        raise ConsoleScriptCheckError("; ".join(problems))
    return tuple(loaded)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the installed console-script integrity check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default=DEFAULT_DISTRIBUTION)
    parser.add_argument("--project-metadata", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--site-packages", type=Path)
    parser.add_argument("--scripts-dir", type=Path)
    args = parser.parse_args(argv)
    module_root = args.site_packages or Path(sysconfig.get_path("purelib"))
    scripts_dir = args.scripts_dir or Path(sysconfig.get_path("scripts"))
    try:
        expected = declared_console_scripts(args.project_metadata)
        loaded = verify_installed_console_scripts(
            args.distribution,
            expected,
            module_root=module_root,
            scripts_dir=scripts_dir,
        )
    except (ConsoleScriptCheckError, OSError, PackageNotFoundError, tomllib.TOMLDecodeError) as exc:
        print(f"installed-console-scripts FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        f"installed-console-scripts OK: {len(loaded)} scripts loaded from "
        f"{module_root.resolve(strict=True)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
