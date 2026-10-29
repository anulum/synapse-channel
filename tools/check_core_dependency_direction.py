# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — trusted-core dependency direction check
"""Keep the trusted core free of imports from the extensible edges.

The core (``synapse_channel.core``) owns identity, authority, claims, the
operation life cycle and the durable record. Vendor integrations, adapters,
protocol bridges, the dashboard and the CLI build on it; it must never depend
on them. This checker reads every module under ``src/synapse_channel/core``
and refuses any import of ``synapse_channel`` other than
``synapse_channel.core`` itself and the package version
(``from synapse_channel import __version__``). Absolute, relative,
``TYPE_CHECKING`` and literal ``importlib.import_module`` / ``__import__``
imports are all checked. Imports whose module name is computed at run time
cannot be read statically and are outside this check.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "synapse_channel"
CORE = "synapse_channel.core"
ALLOWED_ROOT_NAMES = frozenset({"__version__"})


@dataclass(frozen=True)
class Violation:
    """One forbidden dependency from the core towards an edge module.

    Attributes
    ----------
    path : Path
        Core source file that contains the import, relative to the repository root.
    line : int
        One-based source line of the import.
    target : str
        Imported module (and name for ``from synapse_channel import ...``).
    """

    path: Path
    line: int
    target: str

    def render(self) -> str:
        """Return the ``path:line: target`` form used in the refusal report.

        Returns
        -------
        str
            One human-readable report line.
        """
        return f"{self.path.as_posix()}:{self.line}: core imports {self.target}"


def _module_name(source: Path, root: Path) -> str:
    """Return the dotted module name of ``source`` below ``root / 'src'``."""
    parts = source.relative_to(root / "src").with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _package_of(source: Path, module: str) -> str:
    """Return the package that relative imports in ``source`` start from."""
    return module if source.name == "__init__.py" else module.rpartition(".")[0]


def _resolve_relative(package: str, level: int, name: str | None) -> str:
    """Resolve one relative ``from`` import against its containing package."""
    parts = package.split(".")
    if level > len(parts):
        raise ValueError(f"relative import beyond the top-level package from {package}")
    base = parts[: len(parts) - level + 1]
    return ".".join([*base, name] if name else base)


def _is_core(target: str) -> bool:
    return target == CORE or target.startswith(CORE + ".")


def _literal_dynamic_import(node: ast.Call) -> str | None:
    """Return the literal module of ``import_module``/``__import__``, positional or ``name=``."""
    function = node.func
    called = (
        function.attr
        if isinstance(function, ast.Attribute)
        else function.id
        if isinstance(function, ast.Name)
        else None
    )
    if called not in {"import_module", "__import__"}:
        return None
    named = [keyword.value for keyword in node.keywords if keyword.arg == "name"]
    first = node.args[0] if node.args else (named[0] if named else None)
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _forbidden(target: str) -> bool:
    return (target == PACKAGE or target.startswith(PACKAGE + ".")) and not _is_core(target)


def module_violations(source: Path, root: Path) -> list[Violation]:
    """Return every edge dependency in one core module.

    Parameters
    ----------
    source : Path
        Python file below ``root / 'src/synapse_channel/core'``.
    root : Path
        Repository root containing ``src``.

    Returns
    -------
    list of Violation
        Forbidden imports in source order; empty when the module conforms.

    Raises
    ------
    SyntaxError
        If the module is not valid Python.
    ValueError
        If a relative import climbs above the top-level package.
    """
    module = _module_name(source, root)
    package = _package_of(source, module)
    relative = source.relative_to(root)
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _forbidden(alias.name):
                    found.append(Violation(relative, node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            target = (
                _resolve_relative(package, node.level, node.module)
                if node.level
                else node.module or ""
            )
            if target == PACKAGE:
                for alias in node.names:
                    if alias.name not in ALLOWED_ROOT_NAMES:
                        found.append(Violation(relative, node.lineno, f"{PACKAGE}.{alias.name}"))
            elif _forbidden(target):
                found.append(Violation(relative, node.lineno, target))
        elif isinstance(node, ast.Call):
            literal = _literal_dynamic_import(node)
            if literal is not None and _forbidden(literal):
                found.append(Violation(relative, node.lineno, literal))
    return sorted(found, key=lambda item: item.line)


def core_violations(root: Path) -> list[Violation]:
    """Return every edge dependency of the trusted core below ``root``.

    Parameters
    ----------
    root : Path
        Repository root containing ``src/synapse_channel/core``.

    Returns
    -------
    list of Violation
        All violations, ordered by file and line.

    Raises
    ------
    FileNotFoundError
        If the core package directory does not exist.
    """
    core = root / "src" / "synapse_channel" / "core"
    if not core.is_dir():
        raise FileNotFoundError(f"trusted core package not found: {core}")
    found: list[Violation] = []
    for source in sorted(core.rglob("*.py")):
        found.extend(module_violations(source, root))
    return found


def main(argv: list[str] | None = None) -> int:
    """Check the trusted core and report every edge dependency.

    Parameters
    ----------
    argv : list of str or None, optional
        Command-line arguments; ``--root`` selects the repository root.

    Returns
    -------
    int
        Zero when the core depends on no edge module; one otherwise or when the
        core cannot be read.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args(argv)
    try:
        violations = core_violations(arguments.root)
    except (OSError, SyntaxError, ValueError) as error:
        print(f"core dependency direction: cannot check: {error}", file=sys.stderr)
        return 1
    for violation in violations:
        print(violation.render(), file=sys.stderr)
    if violations:
        print(
            f"core dependency direction: FAIL ({len(violations)} edge import(s))", file=sys.stderr
        )
        return 1
    print("core dependency direction: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
