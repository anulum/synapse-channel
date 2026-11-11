# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the forward-origin context keeps the whole handler context surface
"""``ForwardOriginContext`` declares every ``HandlerContext`` member, signature for signature.

``HandlerContext`` exists only for the type checker, so ``ForwardOriginContext``
repeats its members as a runtime ``Protocol``. A member added, removed or changed
on one side only would go unnoticed until some context combined the two. These
guards read both class bodies from the source and also ask mypy whether every
forward-origin context is a handler context.
"""

from __future__ import annotations

import ast
from pathlib import Path

from mypy_process import run_mypy

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "src" / "synapse_channel" / "core"

Signature = tuple[str, ...]


def _class_body(path: Path, name: str) -> list[ast.stmt]:
    """Return the body of the one class called ``name`` anywhere in ``path``."""
    found = [
        node
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(found) == 1, f"{path.name} must define exactly one {name}"
    return found[0].body


def _members(path: Path, name: str) -> dict[str, Signature]:
    """Map each declared member of a class to its kind, decorators and exact signature text."""
    members: dict[str, Signature] = {}
    for node in _class_body(path, name):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            members[node.target.id] = ("attribute", ast.unparse(node.annotation))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            kind = "async" if isinstance(node, ast.AsyncFunctionDef) else "def"
            decorators = tuple(ast.unparse(item) for item in node.decorator_list)
            returns = ast.unparse(node.returns) if node.returns is not None else ""
            members[node.name] = (kind, *decorators, ast.unparse(node.args), returns)
    return members


HANDLER = _members(CORE / "handler_context.py", "HandlerContext")
FORWARD = _members(CORE / "message_forward_origin.py", "ForwardOriginContext")


def test_the_handler_context_surface_is_read_from_its_source() -> None:
    """The guard reads a real member list, not an empty or partial class."""
    assert {"broadcast", "journal", "send_json", "state", "system"} <= set(HANDLER)
    assert all(signature[0] in {"attribute", "async", "def"} for signature in HANDLER.values())


def test_forward_origin_context_declares_every_handler_context_member() -> None:
    """No HandlerContext member is missing from the forward-origin context."""
    assert sorted(set(HANDLER) - set(FORWARD)) == []


def test_every_shared_member_has_the_same_kind_and_signature() -> None:
    """Each shared member keeps its decorators, parameters, defaults and return annotation."""
    changed = {
        name: (signature, FORWARD[name])
        for name, signature in HANDLER.items()
        if name in FORWARD and FORWARD[name] != signature
    }
    assert changed == {}


def test_mypy_accepts_a_forward_origin_context_as_a_handler_context(tmp_path: Path) -> None:
    """Strict mypy accepts a forward-origin context wherever a handler context is required."""
    consumer = tmp_path / "forward_origin_consumer.py"
    consumer.write_text(
        "from synapse_channel.core.handler_context import HandlerContext\n"
        "from synapse_channel.core.message_forward_origin import ForwardOriginContext\n"
        "\n"
        "def as_handler(context: ForwardOriginContext) -> HandlerContext:\n"
        "    return context\n",
        encoding="utf-8",
    )
    stdout, stderr, status = run_mypy(
        [
            "--strict",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / "mypy"),
            "--no-error-summary",
            str(consumer),
        ]
    )
    assert status == 0, stdout + stderr
