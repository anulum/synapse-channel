# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — constructor-reachable attribute assignments
"""Inspect local initialisation calls and retain unresolved effects for review."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field


@dataclass
class Initialisation:
    """Known assignment targets and calls whose effects cannot be resolved."""

    attributes: set[str] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)


def _targets(node: ast.expr, receiver: str) -> Iterator[str]:
    """Yield instance attributes bound by an assignment target."""
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == receiver:
            yield node.attr
    elif isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            yield from _targets(element, receiver)
    elif isinstance(node, ast.Starred):
        yield from _targets(node.value, receiver)


def _methods(body: Iterable[ast.stmt]) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Yield same-class methods, including conditional definitions."""
    for member in body:
        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield member
        elif not isinstance(member, ast.ClassDef):
            for child in ast.iter_child_nodes(member):
                if isinstance(child, ast.stmt):
                    yield from _methods([child])
                elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                    yield from _methods(child.body)


def _setter(node: ast.Call) -> bool:
    """Identify literal built-in and object attribute-setter spellings."""
    callee = node.func
    return (isinstance(callee, ast.Name) and callee.id == "setattr") or (
        isinstance(callee, ast.Attribute)
        and isinstance(callee.value, ast.Name)
        and callee.value.id == "object"
        and callee.attr == "__setattr__"
    )


def _setter_target(node: ast.Call, receiver: str, result: Initialisation) -> bool:
    """Record a setter on this receiver, preserving unknown attribute names."""
    if not _setter(node) or len(node.args) < 2:
        return False
    instance, name = node.args[:2]
    if not isinstance(instance, ast.Name) or instance.id != receiver:
        return False
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        result.attributes.add(name.value)
    else:
        result.unresolved.add(f"{node.lineno}: dynamic attribute name")
    return True


def _assignments(
    method: ast.FunctionDef | ast.AsyncFunctionDef, receiver: str, result: Initialisation
) -> None:
    """Union direct assignment targets and retain setter uncertainty."""
    for node in ast.walk(method):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                result.attributes.update(_targets(target, receiver))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            result.attributes.update(_targets(node.target, receiver))
        elif isinstance(node, ast.Call):
            _setter_target(node, receiver, result)


def _given_receiver(node: ast.Call, receiver: str) -> bool:
    """Report passing this instance to a function outside the local call graph."""
    arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
    return any(isinstance(arg, ast.Name) and arg.id == receiver for arg in arguments)


def _calls(
    method: ast.FunctionDef | ast.AsyncFunctionDef, receiver: str, result: Initialisation
) -> Iterator[str]:
    """Yield bound helper names and flag calls outside the source-local graph."""
    for node in ast.walk(method):
        if not isinstance(node, ast.Call):
            continue
        if _setter(node):
            continue
        callee = node.func
        if (
            isinstance(callee, ast.Attribute)
            and isinstance(callee.value, ast.Name)
            and callee.value.id in {receiver, "cls"}
        ):
            yield callee.attr
        elif _given_receiver(node, receiver):
            result.unresolved.add(f"{node.lineno}: {ast.unparse(callee)} receives {receiver}")


def analyse_initialisation(owner: ast.ClassDef, constructor: ast.FunctionDef) -> Initialisation:
    """Inspect the transitive source-local constructor helper graph.

    Parameters
    ----------
    owner:
        Class whose direct and conditional method definitions are available.
    constructor:
        Constructor definition belonging to that class.

    Returns
    -------
    Initialisation
        The union of known instance targets and explicit unresolved effects.
        Recursion and repeated calls are visited once. Conditional paths are
        conservatively included. Inherited or dynamic calls stay unresolved;
        they are never represented as a proven zero-attribute effect.
    """
    methods: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    for member in _methods(owner.body):
        methods.setdefault(member.name, []).append(member)
    pending: list[ast.FunctionDef | ast.AsyncFunctionDef] = [constructor]
    seen: set[int] = set()
    result = Initialisation()
    while pending:
        method = pending.pop()
        if id(method) in seen:
            continue
        seen.add(id(method))
        arguments = method.args.posonlyargs + method.args.args
        if not arguments:
            continue
        receiver = arguments[0].arg
        _assignments(method, receiver, result)
        for name in _calls(method, receiver, result):
            if name in methods:
                pending.extend(methods[name])
            else:
                result.unresolved.add(f"{method.lineno}: unresolved method {name}")
    return result
