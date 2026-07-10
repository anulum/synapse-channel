# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — local tree-sitter declaration extraction for semantic claims
"""Load optional local grammars and extract named source declarations.

The grammar wheels are installed by the ``semantic`` extra. Imports stay lazy,
and there is no download path: an unavailable binding raises an explicit install
hint. This module owns syntax-tree traversal only; Git diff semantics live in
:mod:`synapse_channel.git.semantic_diff`.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEMANTIC_EXTRA_HINT = (
    "tree-sitter diff claims need the optional 'semantic' extra: "
    "pip install 'synapse-channel[semantic]'"
)
"""Install hint raised when a supported grammar is unavailable."""


@dataclass(frozen=True)
class Declaration:
    """One named tree-sitter declaration and its inclusive line span."""

    symbol: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class LanguageSpec:
    """Grammar binding and named declaration kinds for one language."""

    module: str
    factory: str
    declarations: frozenset[str]
    containers: frozenset[str]


_PYTHON = LanguageSpec(
    "tree_sitter_python",
    "language",
    frozenset({"function_definition", "class_definition"}),
    frozenset({"function_definition", "class_definition"}),
)
_JAVASCRIPT = LanguageSpec(
    "tree_sitter_javascript",
    "language",
    frozenset(
        {"function_declaration", "class_declaration", "method_definition", "variable_declarator"}
    ),
    frozenset({"function_declaration", "class_declaration", "method_definition"}),
)
_TYPESCRIPT_DECLARATIONS = frozenset(
    {
        "function_declaration",
        "class_declaration",
        "method_definition",
        "variable_declarator",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
    }
)
_TYPESCRIPT = LanguageSpec(
    "tree_sitter_typescript",
    "language_typescript",
    _TYPESCRIPT_DECLARATIONS,
    frozenset(
        {
            "function_declaration",
            "class_declaration",
            "method_definition",
            "interface_declaration",
            "enum_declaration",
        }
    ),
)
_TSX = LanguageSpec(
    "tree_sitter_typescript",
    "language_tsx",
    _TYPESCRIPT_DECLARATIONS,
    _TYPESCRIPT.containers,
)
_RUST = LanguageSpec(
    "tree_sitter_rust",
    "language",
    frozenset(
        {
            "function_item",
            "struct_item",
            "enum_item",
            "trait_item",
            "impl_item",
            "type_item",
            "mod_item",
        }
    ),
    frozenset({"function_item", "trait_item", "impl_item", "mod_item"}),
)
_GO = LanguageSpec(
    "tree_sitter_go",
    "language",
    frozenset({"function_declaration", "method_declaration", "type_spec"}),
    frozenset({"function_declaration", "method_declaration", "type_spec"}),
)

_EXTENSIONS: dict[str, tuple[str, LanguageSpec]] = {
    ".py": ("python", _PYTHON),
    ".pyi": ("python", _PYTHON),
    ".js": ("javascript", _JAVASCRIPT),
    ".jsx": ("javascript", _JAVASCRIPT),
    ".mjs": ("javascript", _JAVASCRIPT),
    ".cjs": ("javascript", _JAVASCRIPT),
    ".ts": ("typescript", _TYPESCRIPT),
    ".tsx": ("tsx", _TSX),
    ".rs": ("rust", _RUST),
    ".go": ("go", _GO),
}

ParserFactory = Callable[[LanguageSpec], Any]
"""Build a configured tree-sitter parser; injectable for boundary tests."""


def language_for_path(path: str) -> tuple[str, LanguageSpec] | None:
    """Return the supported language and grammar spec for ``path``."""
    return _EXTENSIONS.get(Path(path).suffix.lower())


def default_parser(spec: LanguageSpec) -> Any:
    """Load one locally installed grammar and return its configured parser."""
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        grammar = importlib.import_module(spec.module)
        capsule = getattr(grammar, spec.factory)()
        return tree_sitter.Parser(tree_sitter.Language(capsule))
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(SEMANTIC_EXTRA_HINT) from exc


def _node_text(node: Any, source: bytes) -> str:
    """Return one node's stripped UTF-8 source text."""
    return source[node.start_byte : node.end_byte].decode("utf-8").strip()


def _first_descendant_field(node: Any, field: str) -> Any | None:
    """Return the first descendant carrying ``field``."""
    found = node.child_by_field_name(field)
    if found is not None:
        return found
    for child in node.named_children:
        found = _first_descendant_field(child, field)
        if found is not None:
            return found
    return None


def _declaration_name(node: Any, source: bytes) -> str | None:
    """Return the declaration name, including Go receivers and Rust impl types."""
    if node.type == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is None or value.type not in {"arrow_function", "function_expression"}:
            return None
    field = "type" if node.type == "impl_item" else "name"
    name_node = node.child_by_field_name(field)
    if name_node is None:
        return None
    name = _node_text(name_node, source)
    if node.type == "method_declaration":
        receiver = node.child_by_field_name("receiver")
        if receiver is not None:
            receiver_type = _first_descendant_field(receiver, "type")
            if receiver_type is not None:
                return f"{_node_text(receiver_type, source)}.{name}"
    return name or None


def _inclusive_end_line(node: Any) -> int:
    """Translate tree-sitter's exclusive end point to a one-based line."""
    row, column = node.end_point
    return int(row + 1 if column else max(1, row))


def extract_declarations(
    source: bytes,
    spec: LanguageSpec,
    *,
    parser_factory: ParserFactory = default_parser,
) -> tuple[Declaration, ...]:
    """Parse ``source`` and return named declarations from outer to inner.

    A syntax-error tree returns an empty tuple. Callers treat that as a
    whole-file widening rather than trusting a recovered partial tree.
    """
    parser = parser_factory(spec)
    root = parser.parse(source).root_node
    if root.has_error:
        return ()
    declarations: list[Declaration] = []

    def visit(node: Any, parents: tuple[str, ...]) -> None:
        nested_parents = parents
        if node.type in spec.declarations:
            name = _declaration_name(node, source)
            if name is not None:
                parts = tuple(part for part in name.split(".") if part)
                symbol_parts = (*parents, *parts)
                declarations.append(
                    Declaration(
                        symbol=".".join(symbol_parts),
                        start_line=int(node.start_point[0]) + 1,
                        end_line=_inclusive_end_line(node),
                    )
                )
                if node.type in spec.containers:
                    nested_parents = symbol_parts
        for child in node.named_children:
            visit(child, nested_parents)

    visit(root, ())
    return tuple(declarations)
