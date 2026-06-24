# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — file-scope overlap detection for work claims
"""Worktree- and path-scoped overlap detection for task claims.

The bus's core promise is that two agents never silently edit the same files.
A claim may declare *where* it works: a ``worktree`` label (agents in different
git worktrees over a shared ``.git`` are isolated and never conflict) and a set
of ``paths`` it intends to touch. This module decides, purely and
deterministically, whether two such scopes overlap.

The overlap model is the precise file-ownership-decomposition case: each declared
path is a file or a directory subtree, and two paths overlap when one is the
other or an ancestor directory of the other. Wildcard-glob algebra (``*``/``?``)
is intentionally out of scope for this version — declared paths are literal files
or directory prefixes — so the result is exact, never a heuristic guess. An empty
path set means the claim owns the *whole* worktree and therefore conflicts with
any other claim in that worktree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

DEFAULT_WORKTREE = ""
"""Label for the shared/default working tree when a claim names no worktree."""

MAX_DECLARED_PATHS = 512
"""Upper bound on distinct declared paths per claim before the scope is widened.

A claim that declares more distinct paths than this is treated as owning the whole
worktree (see :func:`normalize_paths`). The bound caps the ``O(n·m)`` cost of
pairwise overlap checks and widening — rather than dropping paths — keeps the
result conservative: it never *misses* a conflict, it only over-claims.
"""


def normalize_path(path: str) -> str:
    """Normalise a declared path to canonical segments for prefix comparison.

    The path is split on ``/`` and rebuilt: surrounding whitespace, empty
    segments (so ``//`` collapses), and ``.`` segments are dropped, and a ``..``
    segment pops the preceding real segment (so ``src/../tests`` becomes
    ``tests``). A leading ``..`` that would escape the tree root is kept literally,
    so an out-of-tree path (``../../etc/passwd``) never normalises to a root-level
    name and falsely overlaps an in-tree claim.

    Parameters
    ----------
    path : str
        A declared file or directory path.

    Returns
    -------
    str
        The normalised path (``""`` for a path that names the tree root).
    """
    segments: list[str] = []
    for segment in path.strip().split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if segments and segments[-1] != "..":
                segments.pop()
            else:
                segments.append("..")
            continue
        segments.append(segment)
    return "/".join(segments)


def paths_overlap(a: str, b: str) -> bool:
    """Return whether two declared paths cover any common file.

    Two paths overlap when, after normalisation, they are equal or one is an
    ancestor directory of the other. The empty (root) path covers the whole tree
    and therefore overlaps everything.

    Parameters
    ----------
    a, b : str
        Declared file or directory paths.

    Returns
    -------
    bool
        ``True`` if the paths share at least one file.
    """
    na, nb = normalize_path(a), normalize_path(b)
    if na == nb:
        return True
    if na == "" or nb == "":
        return True
    return nb.startswith(na + "/") or na.startswith(nb + "/")


def scopes_conflict(
    worktree_a: str,
    paths_a: Sequence[str],
    worktree_b: str,
    paths_b: Sequence[str],
) -> bool:
    """Return whether two claim scopes contend for the same files.

    Scopes in different worktrees never conflict. Within the same worktree, an
    empty path set means the claim owns the whole tree (conflicts with any other
    claim there); otherwise the scopes conflict when any declared path of one
    overlaps any declared path of the other.

    Parameters
    ----------
    worktree_a, worktree_b : str
        Worktree labels of the two claims.
    paths_a, paths_b : Sequence[str]
        Declared paths of the two claims (empty means the whole worktree).

    Returns
    -------
    bool
        ``True`` if the two scopes contend for at least one file.
    """
    if worktree_a != worktree_b:
        return False
    if not paths_a or not paths_b:
        return True
    return any(paths_overlap(a, b) for a in paths_a for b in paths_b)


def normalize_paths(paths: Iterable[str], max_declared_paths: int | None = None) -> tuple[str, ...]:
    """Normalise and de-duplicate a set of declared paths, preserving order.

    Empty results (paths that normalise to the tree root) collapse the set to a
    single root entry, since owning the root already implies the whole tree.

    Parameters
    ----------
    paths : Iterable[str]
        Raw declared paths.
    max_declared_paths : int or None, optional
        Most distinct paths to keep before the scope is widened to the whole
        worktree. Clamped up to ``1``. ``None`` (the default) uses
        :data:`MAX_DECLARED_PATHS`.

    Returns
    -------
    tuple[str, ...]
        Normalised, order-preserving, duplicate-free paths; ``("",)`` if any
        entry names the tree root or the distinct count exceeds the cap; ``()``
        if the input is empty.
    """
    cap = MAX_DECLARED_PATHS if max_declared_paths is None else max(1, int(max_declared_paths))
    seen: set[str] = set()
    out: list[str] = []
    for raw in paths:
        norm = normalize_path(raw)
        if norm == "":
            return ("",)
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
            # Too many distinct paths: widen to the whole worktree rather than pay
            # an unbounded pairwise overlap cost. Conservative — never misses a
            # conflict; an agent wanting many files should claim their parent dir.
            if len(out) > cap:
                return ("",)
    return tuple(out)
