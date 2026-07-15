# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — intent-driven semantic claim suggestion
"""Suggest file-scope claim paths from a free-text intent.

This first slice uses deterministic token matching against repository paths,
module names, and directory names. It requires no network, no embeddings, and
no optional dependencies, so it stays local-first and runs everywhere the core
package does. Future slices may layer lightweight embedding expansion behind an
``--experimental-embedding`` flag.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from synapse_channel.git import test_ownership_map
from synapse_channel.terminal_text import shell_command_arg, shell_long_option

REPO_ROOT = Path.cwd()

DEFAULT_LIMIT = 10
"""Default number of suggestions to emit."""

IGNORED_PATH_SEGMENTS = frozenset(
    {
        ".git",
        ".github",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".coverage",
        "htmlcov",
        ".tox",
        ".venv",
        "venv",
        "node_modules",
        ".idea",
        ".vscode",
        "dist",
        "build",
    }
)
"""Directory names that are build artefacts and must never be suggested.

An ``*.egg-info`` directory is matched separately by suffix in
:func:`_is_ignored_segment`, since these names carry the package prefix.
"""

SOURCE_SUFFIXES = frozenset({".py", ".js", ".ts", ".rs", ".go", ".java", ".c", ".h"})
"""Source-like suffixes that receive a small scoring bonus."""


@dataclass(frozen=True)
class SuggestedPath:
    """One ranked path suggestion with its evidence."""

    path: str
    score: float
    matched_tokens: tuple[str, ...]


@dataclass(frozen=True)
class CliArgs:
    """Parsed command-line arguments for semantic claim suggestion."""

    repo_root: Path
    intent: str | None
    limit: int
    json_output: bool
    draft: bool
    draft_task_id: str


def _tokenize(text: str) -> tuple[str, ...]:
    """Return unique lowercase alphanumeric tokens from ``text``.

    Token boundaries are any non-alphanumeric characters, so camelCase is kept
    as one token but hyphenated or snake_case terms split. Duplicates are
    collapsed in first-seen order, so a repeated word cannot inflate a path's
    score. This matches how agents usually describe file-scope intent.
    """
    return tuple(dict.fromkeys(t for t in re.split(r"[^a-zA-Z0-9]+", text.lower()) if t))


def _is_ignored_segment(name: str) -> bool:
    """Return whether a directory or file name is a build artefact to skip."""
    return name in IGNORED_PATH_SEGMENTS or name.endswith(".egg-info")


def _iter_repo_files(repo_root: Path) -> Iterable[Path]:
    """Yield candidate repository files for intent matching.

    The walk is bounded: ignored directories are pruned *before* descending into
    them, so it never enters ``.git``, ``node_modules``, ``.venv``, an
    ``*.egg-info`` tree, or other build artefacts on a real checkout. Directory
    symlinks are not followed, so a link cannot pull external paths into scope.
    Symlinked files, files named like an artefact, files above a size cutoff, and
    files that cannot be stat-ed are all skipped.
    """
    max_file_bytes = 2 * 1024 * 1024
    if not repo_root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(repo_root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if not _is_ignored_segment(name))
        directory = Path(dirpath)
        for filename in sorted(filenames):
            if _is_ignored_segment(filename):
                continue
            path = directory / filename
            if path.is_symlink():
                continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            yield path


def _repo_relative(path: Path, repo_root: Path) -> str:
    """Return a repository-relative POSIX path."""
    return path.relative_to(repo_root).as_posix()


def _token_in_module_index(token: str, module_index: dict[str, str]) -> bool:
    """Return whether ``token`` exactly matches a module path segment."""
    for module in module_index:
        if any(token == part for part in module.lower().split(".")):
            return True
    return False


def _score_path(
    path: str,
    tokens: tuple[str, ...],
    module_index: dict[str, str],
) -> tuple[float, tuple[str, ...]]:
    """Score a path against intent tokens and return matched tokens.

    Matches are weighted by specificity and each token is scored once, at its
    highest-weighting position: an exact filename-stem match (12) beats a
    filename substring (8), an exact parent-directory name (5), a module
    dot-path segment (4), a parent-directory substring (3), and finally a bare
    path substring (1). The ``elif`` cascade is ordered by descending weight so a
    lower-weight position never shadows a higher one. Paths with no matched token
    receive a score of zero even if they are source files, so unrelated paths are
    never suggested.
    """
    path_lower = path.lower()
    parts = Path(path).parts
    filename = Path(path).stem.lower()
    matched: list[str] = []
    score = 0.0
    for token in tokens:
        if token == filename:
            score += 12.0
        elif token in filename:
            score += 8.0
        elif any(token == part.lower() for part in parts[:-1]):
            score += 5.0
        elif _token_in_module_index(token, module_index):
            score += 4.0
        elif any(token in part.lower() for part in parts[:-1]):
            score += 3.0
        elif token in path_lower:
            score += 1.0
        else:
            continue
        matched.append(token)
    if not matched:
        return 0.0, ()
    # Small source-code bonus so .py files beat generated artefacts of the same name.
    if Path(path).suffix in SOURCE_SUFFIXES:
        score += 0.5
    # Penalise very deep paths slightly to keep suggestions readable.
    score -= 0.1 * max(0, len(parts) - 4)
    return round(score, 2), tuple(matched)


def _build_module_index(repo_root: Path) -> dict[str, str]:
    """Map importable module names to their source paths.

    The ownership map already performs AST-based discovery; reusing it avoids
    duplicating source scanning logic and keeps module suggestions consistent
    with explicit ``--module`` selectors.
    """
    try:
        records = test_ownership_map.build_ownership_map(repo_root)
    except (OSError, ValueError, SyntaxError):
        return {}
    return {record.module: record.source for record in records}


def suggest_paths(
    repo_root: Path,
    intent: str,
    *,
    limit: int = DEFAULT_LIMIT,
) -> tuple[SuggestedPath, ...]:
    """Return ranked path suggestions for ``intent``.

    Parameters
    ----------
    repo_root : Path
        Repository root to inspect.
    intent : str
        Free-text description of the intended work, e.g. "auth file claims".
    limit : int, optional
        Maximum number of suggestions to return. Defaults to ``DEFAULT_LIMIT``.

    Returns
    -------
    tuple[SuggestedPath, ...]
        Ranked suggestions, highest score first. The result is empty when the
        repository root does not exist or no path matches any intent token.
    """
    tokens = _tokenize(intent)
    if not tokens:
        return ()
    repo_root = repo_root.resolve()
    module_index = _build_module_index(repo_root)
    scored: dict[str, SuggestedPath] = {}
    for absolute_path in _iter_repo_files(repo_root):
        relative = _repo_relative(absolute_path, repo_root)
        score, matched = _score_path(relative, tokens, module_index)
        if score <= 0:
            continue
        scored[relative] = SuggestedPath(
            path=relative,
            score=score,
            matched_tokens=matched,
        )
    ranked = sorted(scored.values(), key=lambda suggestion: (-suggestion.score, suggestion.path))
    return tuple(ranked[:limit])


def render_human(suggestions: Sequence[SuggestedPath]) -> str:
    """Render suggestions as one line per path with score and matched tokens."""
    if not suggestions:
        return "no paths matched the intent"
    lines: list[str] = []
    for suggestion in suggestions:
        tokens = ", ".join(suggestion.matched_tokens) or "-"
        lines.append(f"{suggestion.score:>6.1f}  {suggestion.path}  (tokens: {tokens})")
    return "\n".join(lines)


def render_json(suggestions: Sequence[SuggestedPath]) -> str:
    """Render suggestions as stable JSON."""
    payload = [
        {
            "path": suggestion.path,
            "score": suggestion.score,
            "matched_tokens": list(suggestion.matched_tokens),
        }
        for suggestion in suggestions
    ]
    return json.dumps(payload, indent=2, sort_keys=True)


def render_draft_claim(
    suggestions: Sequence[SuggestedPath],
    task_id: str,
    name: str = "USER",
    base: str = "main",
) -> str:
    """Render a draft ``synapse git-claim`` command from suggestions."""
    paths = [suggestion.path for suggestion in suggestions]
    if not paths:
        return "# no paths matched the intent; add --paths manually"
    args = " ".join(shell_long_option("--paths", path) for path in paths)
    return (
        f"synapse git-claim {shell_command_arg(task_id)} "
        f"{shell_long_option('--name', name)} "
        f"{shell_long_option('--base', base)} "
        f"{args}"
    ).strip()


def parse_args(argv: Sequence[str] | None = None) -> CliArgs:
    """Parse command-line arguments for semantic claim suggestion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to inspect. Defaults to the current checkout.",
    )
    parser.add_argument(
        "--intent",
        default=None,
        help="Free-text intent describing the files the claim should cover.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum number of paths to suggest (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="Emit a draft synapse git-claim command instead of ranked paths.",
    )
    parser.add_argument(
        "--draft-task-id",
        default="TASK-001",
        help="Task id used in --draft output (default: TASK-001).",
    )
    namespace = parser.parse_args(argv)
    return CliArgs(
        repo_root=namespace.repo_root,
        intent=namespace.intent,
        limit=max(1, namespace.limit),
        json_output=bool(namespace.json_output),
        draft=bool(namespace.draft),
        draft_task_id=namespace.draft_task_id,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    suggest: Callable[..., tuple[SuggestedPath, ...]] | None = None,
) -> int:
    """Run semantic claim suggestion and return a process exit code."""
    args = parse_args(argv)
    if not args.intent:
        print("--intent is required", file=sys.stderr)
        return 2
    repo_root = args.repo_root.resolve()
    if not repo_root.exists():
        print(f"repo root does not exist: {repo_root}", file=sys.stderr)
        return 2
    suggestions = (suggest if suggest is not None else suggest_paths)(
        repo_root,
        args.intent,
        limit=args.limit,
    )
    if args.draft:
        print(render_draft_claim(suggestions, args.draft_task_id))
    elif args.json_output:
        print(render_json(suggestions))
    else:
        print(render_human(suggestions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
