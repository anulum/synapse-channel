# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic scope path-algebra regressions
"""Prove symbol separation remains conservative under ordinary path claims."""

from __future__ import annotations

import pytest

from synapse_channel.core.scoping import paths_overlap, scopes_conflict
from synapse_channel.git.semantic_scope import (
    SemanticScope,
    parse_semantic_scope,
    semantic_scope_path,
)


def test_scope_is_reversible_and_escapes_each_qualified_component() -> None:
    path = semantic_scope_path("./src//worker.py", "Worker.handle request")

    assert path == "src/worker.py/.synapse-symbol/Worker/handle%20request"
    assert parse_semantic_scope(path) == SemanticScope(
        source="src/worker.py",
        symbol="Worker.handle request",
    )


def test_existing_path_ancestry_enforces_function_level_separation() -> None:
    first = semantic_scope_path("src/worker.py", "Worker.first")
    second = semantic_scope_path("src/worker.py", "Worker.second")
    other_file = semantic_scope_path("src/other.py", "Worker.first")

    assert not paths_overlap(first, second)
    assert not paths_overlap(first, other_file)
    assert paths_overlap(first, semantic_scope_path("src/worker.py", "Worker"))
    assert paths_overlap(first, "src/worker.py")
    assert paths_overlap(first, "src")
    assert scopes_conflict("main", (first,), "main", (first,))
    assert not scopes_conflict("main", (first,), "main", (second,))
    assert not scopes_conflict("main", (first,), "other", (first,))


@pytest.mark.parametrize(
    ("source", "symbol"),
    [
        ("", "fn"),
        ("/rooted.py", "fn"),
        ("../outside.py", "fn"),
        ("src/.synapse-symbol/file.py", "fn"),
        ("src/a.py", ""),
        ("src/a.py", "C..method"),
        ("src/a.py", ".."),
        ("src/a.py\n", "fn"),
        ("src/a.py", "fn\n"),
        ("src/a.py", "x" * 5000),
    ],
)
def test_invalid_or_ambiguous_scopes_are_refused(source: str, symbol: str) -> None:
    with pytest.raises(ValueError, match="invalid semantic scope"):
        semantic_scope_path(source, symbol)


@pytest.mark.parametrize(
    "path",
    [
        "src/a.py",
        "src/a.py/.synapse-symbol/",
        "/.synapse-symbol/name",
        "src/a.py/.synapse-symbol/name%2fpart",
        "src/a.py/.synapse-symbol/name//part",
        "src/a.py/.synapse-symbol/..",
    ],
)
def test_noncanonical_paths_are_not_misreported_as_semantic(path: str) -> None:
    assert parse_semantic_scope(path) is None
