# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local tree-sitter declaration extraction regressions
"""Exercise every grammar binding without network or parser downloads."""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from synapse_channel.git import semantic_tree_sitter
from synapse_channel.git.semantic_tree_sitter import (
    SEMANTIC_EXTRA_HINT,
    default_parser,
    extract_declarations,
    language_for_path,
)


@pytest.mark.parametrize(
    ("path", "source", "symbols"),
    [
        (
            "worker.py",
            b"def top():\n    def inner():\n        return 1\n    return inner()\n"
            b"\nclass C:\n    def method(self):\n        return 2\n",
            ("top", "top.inner", "C", "C.method"),
        ),
        (
            "worker.js",
            b"function top() { return 1; }\nconst arrow = () => 2;\n"
            b"const value = 3;\nclass C { method() { return 4; } }\n",
            ("top", "arrow", "C", "C.method"),
        ),
        (
            "worker.ts",
            b"interface I { x: number }\ntype Alias = string;\n"
            b"function top(): number { return 1; }\n"
            b"class C { method(): number { return 2; } }\n",
            ("I", "Alias", "top", "C", "C.method"),
        ),
        (
            "worker.tsx",
            b"export function View() { return <div/>; }\n",
            ("View",),
        ),
        (
            "worker.rs",
            b"fn top() -> i32 { 1 }\nstruct C { x: i32 }\n"
            b"impl C { fn method(&self) -> i32 { self.x } }\n",
            ("top", "C", "C", "C.method"),
        ),
        (
            "worker.go",
            b"package p\nfunc top() int { return 1 }\ntype C struct { x int }\n"
            b"func (c C) method() int { return c.x }\n",
            ("top", "C", "C.method"),
        ),
    ],
)
def test_real_local_grammars_extract_qualified_declarations(
    path: str, source: bytes, symbols: tuple[str, ...]
) -> None:
    language = language_for_path(path)
    assert language is not None

    declarations = extract_declarations(source, language[1])

    assert tuple(declaration.symbol for declaration in declarations) == symbols
    assert all(declaration.start_line <= declaration.end_line for declaration in declarations)


@pytest.mark.parametrize("path", ["a.py", "a.PYI", "a.jsx", "a.mjs", "a.cjs", "a.tsx"])
def test_supported_extension_aliases_are_case_insensitive(path: str) -> None:
    assert language_for_path(path) is not None


def test_unknown_extension_and_syntax_error_do_not_invent_declarations() -> None:
    assert language_for_path("README.md") is None
    language = language_for_path("broken.py")
    assert language is not None
    assert extract_declarations(b"def broken(:\n", language[1]) == ()


def test_missing_binding_raises_actionable_optional_extra_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    language = language_for_path("worker.py")
    assert language is not None

    def refuse(_name: str) -> Any:
        raise ImportError("not installed")

    monkeypatch.setattr(importlib, "import_module", refuse)
    with pytest.raises(RuntimeError, match="synapse-channel\\[semantic\\]"):
        default_parser(language[1])
    assert SEMANTIC_EXTRA_HINT.startswith("tree-sitter diff claims")


class _FakeNode:
    """Minimal tree-sitter node double for declaration boundary branches."""

    def __init__(
        self,
        node_type: str,
        *,
        fields: dict[str, _FakeNode] | None = None,
        children: tuple[_FakeNode, ...] = (),
        start_byte: int = 0,
        end_byte: int = 0,
    ) -> None:
        self.type = node_type
        self._fields = fields or {}
        self.named_children = children
        self.start_byte = start_byte
        self.end_byte = end_byte

    def child_by_field_name(self, field: str) -> _FakeNode | None:
        """Return a configured field child."""
        return self._fields.get(field)


def test_descendant_and_missing_name_boundaries_do_not_invent_symbols() -> None:
    name = _FakeNode("identifier", end_byte=4)
    nested = _FakeNode("wrapper", fields={"type": name})
    receiver = _FakeNode("parameters", children=(_FakeNode("empty"), nested))

    assert semantic_tree_sitter._first_descendant_field(receiver, "type") is name
    assert semantic_tree_sitter._first_descendant_field(_FakeNode("empty"), "type") is None
    assert (
        semantic_tree_sitter._declaration_name(_FakeNode("function_declaration"), b"name") is None
    )

    method_without_receiver = _FakeNode("method_declaration", fields={"name": name})
    assert semantic_tree_sitter._declaration_name(method_without_receiver, b"name") == "name"

    receiver_without_type = _FakeNode("parameters")
    method_without_receiver_type = _FakeNode(
        "method_declaration",
        fields={"name": name, "receiver": receiver_without_type},
    )
    assert semantic_tree_sitter._declaration_name(method_without_receiver_type, b"name") == "name"
    empty_name = _FakeNode("identifier")
    assert (
        semantic_tree_sitter._declaration_name(
            _FakeNode("function_declaration", fields={"name": empty_name}),
            b"",
        )
        is None
    )
