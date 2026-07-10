# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — encode symbol scopes inside the existing path-claim algebra
"""Encode function and type scopes as conservative synthetic claim paths.

The hub already understands literal path ancestry: a claim on ``src/a.py``
conflicts with every descendant path, while two different descendants can
coexist. A semantic scope therefore needs no wire change. ``C.method`` becomes
``src/a.py/.synapse-symbol/C/method``; a directory, whole-file, class, or same
method claim still conflicts, but ``C.other`` does not.

These paths are coordination identifiers, never filesystem paths. Encoding is
canonical and reversible so receipts and dashboards can explain them without
consulting source code or importing a parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from synapse_channel.core.scoping import MAX_PATH_LENGTH, normalize_path

SEMANTIC_SCOPE_SEGMENT = ".synapse-symbol"
"""Reserved path segment separating a source file from its symbol scope."""


@dataclass(frozen=True)
class SemanticScope:
    """Decoded semantic scope.

    Attributes
    ----------
    source : str
        Canonical repository-relative source path.
    symbol : str
        Dot-qualified declaration name.
    """

    source: str
    symbol: str


def semantic_scope_path(source: str, symbol: str) -> str:
    """Return the canonical synthetic path for ``source`` and ``symbol``.

    Parameters
    ----------
    source : str
        Repository-relative source file.
    symbol : str
        Dot-qualified declaration name. Each component becomes one descendant
        segment, so a class claim is an ancestor of its methods.

    Returns
    -------
    str
        Synthetic path understood by the existing hub scope algebra.

    Raises
    ------
    ValueError
        If either value is empty, traversal-like, root-wide, or ambiguous with
        the reserved semantic segment.
    """
    canonical_source = normalize_path(source)
    if (
        not source.isprintable()
        or not canonical_source
        or f"/{SEMANTIC_SCOPE_SEGMENT}/" in f"/{canonical_source}/"
    ):
        raise ValueError("invalid semantic scope source")
    components = tuple(component.strip() for component in symbol.split("."))
    if (
        not symbol.isprintable()
        or not components
        or any(not component or component in {".", ".."} for component in components)
    ):
        raise ValueError("invalid semantic scope symbol")
    encoded = "/".join(quote(component, safe="-_~") for component in components)
    path = f"{canonical_source}/{SEMANTIC_SCOPE_SEGMENT}/{encoded}"
    if len(path) > MAX_PATH_LENGTH:
        msg = "invalid semantic scope: encoded path exceeds the claim-path ceiling"
        raise ValueError(msg)
    return path


def parse_semantic_scope(path: str) -> SemanticScope | None:
    """Decode ``path`` when it is a canonical semantic scope.

    Non-semantic or non-canonical strings return ``None`` rather than raising;
    callers can safely use this as a display probe on arbitrary claim paths.
    """
    marker = f"/{SEMANTIC_SCOPE_SEGMENT}/"
    source, separator, encoded = path.rpartition(marker)
    if not separator or not source or not encoded:
        return None
    try:
        components = tuple(unquote(component, errors="strict") for component in encoded.split("/"))
        symbol = ".".join(components)
        if semantic_scope_path(source, symbol) != path:
            return None
    except (UnicodeDecodeError, ValueError):
        return None
    return SemanticScope(source=source, symbol=symbol)
