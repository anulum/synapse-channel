# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — syntactic indicators of a second responsibility
"""Measure indicators of a second responsibility in Python sources.

A long file is not a defect while it holds one responsibility. What points at a
second one can be counted from the syntax tree: a class configured by many
values, a class that builds its own collaborators, a function with many
branches, a module that reaches into another object's private members.

This module only measures. It parses sources with :mod:`ast` and never imports
them. Judging the figures against a ledger is the job of
``tools/check_structure_budget.py``.

Unit keys are ``<repository-relative path>::<qualified name>`` for classes and
functions and the repository-relative path for modules. A nested definition is
``outer.<locals>.inner``; a second definition of the same qualified name in one
module gets the suffix ``#2``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

__all__ = ["MEASURES", "measure_roots", "measure_source"]

MEASURES = (
    "constructor_parameters",
    "init_attributes",
    "init_constructions",
    "function_body_lines",
    "function_branches",
    "module_internal_imports",
    "private_access",
)
"""Every measure, in the order the ledger lists them."""

Figures = dict[str, dict[str, int]]
"""Measure name to unit key to figure."""

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_BRANCHES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
    ast.match_case,
)
_NOT_COLLABORATORS = ("Error", "Exception", "Warning")
_OWN_NAMES = frozenset({"self", "cls"})


def _definitions(
    body: Iterable[ast.stmt], prefix: str
) -> Iterator[tuple[str, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Yield every class and function below ``body`` with its qualified name."""
    for node in body:
        if isinstance(node, ast.ClassDef):
            name = prefix + node.name
            yield name, node
            yield from _definitions(node.body, name + ".")
        elif isinstance(node, _FUNCTIONS):
            name = prefix + node.name
            yield name, node
            yield from _definitions(node.body, name + ".<locals>.")
        else:
            yield from _definitions(_child_statements(node), prefix)


def _child_statements(node: ast.stmt) -> Iterator[ast.stmt]:
    """Yield the statements held in the blocks of a compound statement."""
    for field in ("body", "orelse", "finalbody", "handlers", "cases"):
        for child in getattr(node, field, ()):
            if isinstance(child, ast.stmt):
                yield child
            else:
                yield from getattr(child, "body", ())


def _body_lines(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count the lines from the first statement after the docstring to the end."""
    first = function.body[0]
    documented = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    if documented and len(function.body) == 1:
        return 0
    start = function.body[1].lineno if documented else first.lineno
    return (function.end_lineno or start) - start + 1


def _branches(function: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count branch points of a function, nested functions included."""
    count = 0
    for node in ast.walk(function):
        if isinstance(node, _BRANCHES):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
    return count


def _parameters(function: ast.FunctionDef) -> int:
    """Count the parameters of a constructor without ``self``."""
    arguments = function.args
    named = len(arguments.posonlyargs) + len(arguments.args) + len(arguments.kwonlyargs)
    variadic = (arguments.vararg is not None) + (arguments.kwarg is not None)
    return max(named - 1, 0) + variadic


def _self_targets(target: ast.expr) -> Iterator[str]:
    """Yield the ``self.<name>`` names an assignment target binds."""
    if isinstance(target, ast.Attribute):
        if isinstance(target.value, ast.Name) and target.value.id == "self":
            yield target.attr
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            yield from _self_targets(element)


def _init_attributes(function: ast.FunctionDef) -> int:
    """Count the distinct ``self`` attributes a constructor assigns."""
    names: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(_self_targets(target))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names.update(_self_targets(node.target))
    return len(names)


def _init_constructions(function: ast.FunctionDef) -> int:
    """Count the distinct capitalised callees a constructor calls."""
    names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
        if name[:1].isupper() and not name.endswith(_NOT_COLLABORATORS):
            names.add(name)
    return len(names)


def _internal_imports(tree: ast.Module, package: str) -> int:
    """Count the distinct modules of ``package`` that a module imports."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                modules.add("." * node.level + (node.module or ""))
            elif node.module and node.module.split(".")[0] == package:
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(
                alias.name for alias in node.names if alias.name.split(".")[0] == package
            )
    return len(modules)


def _private_access(tree: ast.Module, allowed: frozenset[str]) -> int:
    """Count accesses to a private member of an object other than ``self`` or ``cls``."""
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        attribute = node.attr
        private = attribute.startswith("_") and not (
            attribute.startswith("__") and attribute.endswith("__")
        )
        owner = node.value.id
        if private and owner not in _OWN_NAMES and f"{owner}.{attribute}" not in allowed:
            count += 1
    return count


def measure_source(
    text: str,
    *,
    module_key: str,
    package: str,
    allowed_private: frozenset[str] = frozenset(),
) -> Figures:
    """Measure one module's source text.

    Parameters
    ----------
    text : str
        Python source.
    module_key : str
        Repository-relative path of the module; the prefix of its unit keys.
    package : str
        Top-level package name whose modules count as internal imports.
    allowed_private : frozenset[str], optional
        ``name._attr`` spellings that are not counted as private access, such
        as a private type of the standard library used in an annotation.

    Returns
    -------
    dict[str, dict[str, int]]
        Every measure of :data:`MEASURES` with the figures of this module's
        units. Class measures are present only for classes that define
        ``__init__``.

    Raises
    ------
    SyntaxError
        When the text is not valid Python.
    """
    tree = ast.parse(text)
    figures: Figures = {measure: {} for measure in MEASURES}
    figures["module_internal_imports"][module_key] = _internal_imports(tree, package)
    figures["private_access"][module_key] = _private_access(tree, allowed_private)
    seen: dict[str, int] = {}
    for name, node in _definitions(tree.body, ""):
        seen[name] = seen.get(name, 0) + 1
        key = f"{module_key}::{name}" + (f"#{seen[name]}" if seen[name] > 1 else "")
        if isinstance(node, _FUNCTIONS):
            figures["function_body_lines"][key] = _body_lines(node)
            figures["function_branches"][key] = _branches(node)
            continue
        for member in node.body:
            if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                figures["constructor_parameters"][key] = _parameters(member)
                figures["init_attributes"][key] = _init_attributes(member)
                figures["init_constructions"][key] = _init_constructions(member)
                break
    return figures


def measure_roots(
    repository: Path,
    roots: Iterable[str],
    *,
    allowed_private: frozenset[str] = frozenset(),
) -> Figures:
    """Measure every Python module below the given roots of a repository.

    Parameters
    ----------
    repository : Path
        Repository root; unit keys are relative to it.
    roots : Iterable[str]
        Repository-relative directories to measure. The last path component
        of a root is the package name used for its internal imports.
    allowed_private : frozenset[str], optional
        See :func:`measure_source`.

    Returns
    -------
    dict[str, dict[str, int]]
        The merged figures of all modules.

    Raises
    ------
    FileNotFoundError
        When a root is not a directory.
    SyntaxError, UnicodeDecodeError
        When a module cannot be read as Python source. The caller reports the
        file and fails; a module that cannot be measured is never skipped.
    """
    merged: Figures = {measure: {} for measure in MEASURES}
    for root in roots:
        directory = repository / root
        if not directory.is_dir():
            raise FileNotFoundError(f"measured root is not a directory: {root}")
        package = Path(root).name
        for path in sorted(directory.rglob("*.py")):
            key = path.relative_to(repository).as_posix()
            try:
                figures = measure_source(
                    path.read_text(encoding="utf-8"),
                    module_key=key,
                    package=package,
                    allowed_private=allowed_private,
                )
            except SyntaxError as error:
                raise SyntaxError(f"{key}: {error.msg} (line {error.lineno})") from error
            for measure, units in figures.items():
                merged[measure].update(units)
    return merged
