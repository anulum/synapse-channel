# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — Java, C# and Ruby semantic diff integration
"""Exercise language-specific claim projection through real Git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from synapse_channel.git import semantic_diff


def _git(repo: Path, *args: str) -> str:
    """Run local Git against the disposable repository and require success."""
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    """Replace one test repository source file with an explicit revision."""
    (repo / relative).write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    """Commit the initial source revision in a disposable repository."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    for relative, source in files.items():
        _write(tmp_path, relative, source)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


@pytest.mark.parametrize(
    ("path", "language", "source", "symbol"),
    [
        ("Worker.java", "java", "class Worker {\n int old() {\n return 1;\n }\n}\n", "Worker.old"),
        ("Worker.cs", "csharp", "class Worker {\n int old() {\n return 1;\n }\n}\n", "Worker.old"),
        ("worker.rb", "ruby", "class Worker\n def old\n  1\n end\nend\n", "Worker.old"),
    ],
)
def test_added_languages_reserve_both_sides_and_widen_invalid_syntax(
    tmp_path: Path, path: str, language: str, source: str, symbol: str
) -> None:
    """Resolve real Git edits and renames without trusting invalid syntax."""
    repo, base = _repo(tmp_path, {path: source})
    _write(repo, path, source.replace("1", "2"))
    record = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert record.language == language
    assert record.narrowed
    assert record.symbols == (symbol,)
    assert record.claim_paths == (f"{path}/.synapse-symbol/Worker/old",)

    _write(repo, path, source.replace("old", "new"))
    _git(repo, "add", path)
    staged = semantic_diff.resolve_staged_diff(repo)[0]
    assert staged.symbols == (symbol, "Worker.new")
    assert staged.claim_paths == (
        f"{path}/.synapse-symbol/Worker/old",
        f"{path}/.synapse-symbol/Worker/new",
    )

    _write(repo, path, source + "\n'\n")
    invalid = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert not invalid.narrowed
    assert invalid.claim_paths == (path,)
