# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — conservative tree-sitter Git-diff claim regressions
"""Drive semantic diff inference through real temporary Git repositories."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.git import semantic_diff
from synapse_channel.git.semantic_tree_sitter import Declaration, default_parser, language_for_path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "semantic_diff_claims.py"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("semantic_diff_claims_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


semantic_diff_tool = _load_tool()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    for relative, text in files.items():
        _write(tmp_path, relative, text)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def test_working_tree_function_edit_narrows_to_smallest_declaration(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "src/worker.py": (
                "def outer():\n"
                "    def inner():\n"
                "        return 1\n"
                "    return inner()\n\n"
                "def other():\n"
                "    return 2\n"
            )
        },
    )
    _write(
        repo,
        "src/worker.py",
        "def outer():\n"
        "    def inner():\n"
        "        return 3\n"
        "    return inner()\n\n"
        "def other():\n"
        "    return 2\n",
    )

    records = semantic_diff.resolve_git_diff(repo, base=base)

    assert records == (
        semantic_diff.SemanticDiffRecord(
            status="M",
            source="src/worker.py",
            old_source="src/worker.py",
            language="python",
            symbols=("outer.inner",),
            semantic_scopes=("src/worker.py/.synapse-symbol/outer/inner",),
            claim_paths=("src/worker.py/.synapse-symbol/outer/inner",),
            narrowed=True,
            reason="all changed lines map to named declarations",
        ),
    )


def test_symbol_rename_claims_both_old_and_new_names(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def old():\n    return 1\n"})
    _write(repo, "worker.py", "def new():\n    return 1\n")

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.symbols == ("old", "new")
    assert record.claim_paths == (
        "worker.py/.synapse-symbol/old",
        "worker.py/.synapse-symbol/new",
    )


@pytest.mark.parametrize(
    ("path", "before", "after", "reason"),
    [
        (
            "worker.py",
            "VALUE = 1\n\ndef run():\n    return VALUE\n",
            "VALUE = 2\n\ndef run():\n    return VALUE\n",
            "outside a named declaration",
        ),
        ("README.md", "old\n", "new\n", "language is not supported"),
        ("broken.py", "def run():\n    return 1\n", "def run(:\n", "outside a named declaration"),
    ],
)
def test_incomplete_semantic_evidence_widens_to_whole_file(
    tmp_path: Path,
    path: str,
    before: str,
    after: str,
    reason: str,
) -> None:
    repo, base = _repo(tmp_path, {path: before})
    _write(repo, path, after)

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.claim_paths == (path,)
    assert record.narrowed is False
    assert reason in record.reason


def test_add_delete_and_rename_are_always_file_wide(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "delete.py": "def removed():\n    return 1\n",
            "rename.py": "def moved():\n    return 2\n",
        },
    )
    (repo / "delete.py").unlink()
    _git(repo, "mv", "rename.py", "renamed.py")
    _write(repo, "added.py", "def added():\n    return 3\n")
    _git(repo, "add", "-A")

    records = semantic_diff.resolve_git_diff(repo, base=base)
    by_status = {record.status: record for record in records}

    assert {record.status for record in records} == {"A", "D", "R"}
    assert by_status["A"].claim_paths == ("added.py",)
    assert by_status["D"].claim_paths == ("delete.py",)
    assert by_status["R"].claim_paths == ("renamed.py",)
    assert all(not record.narrowed for record in records)


def test_committed_head_and_path_filter_ignore_later_worktree_changes(tmp_path: Path) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "a.py": "def a():\n    return 1\n",
            "b.py": "def b():\n    return 1\n",
        },
    )
    _write(repo, "a.py", "def a():\n    return 2\n")
    _write(repo, "b.py", "def b():\n    return 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "a.py", "def a():\n    return 99\n")

    records = semantic_diff.resolve_git_diff(repo, base=base, head=head, paths=("b.py",))

    assert len(records) == 1
    assert records[0].source == "b.py"
    assert records[0].symbols == ("b",)


def test_mode_only_and_oversized_changes_widen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    os.chmod(repo / "worker.py", 0o755)
    mode_record = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert mode_record.reason == "diff has no textual hunks"

    os.chmod(repo / "worker.py", 0o644)
    _write(repo, "worker.py", "def run():\n    return 200\n")
    monkeypatch.setattr(semantic_diff, "MAX_SEMANTIC_SOURCE_BYTES", 4)
    size_record = semantic_diff.resolve_git_diff(repo, base=base)[0]
    assert "size ceiling" in size_record.reason


def test_parser_failure_and_invalid_revisions_are_fail_visible(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    _write(repo, "worker.py", "def run():\n    return 2\n")

    def refuse(_spec: object) -> Any:
        raise RuntimeError("parser unavailable")

    with pytest.raises(RuntimeError, match="parser unavailable"):
        semantic_diff.resolve_git_diff(repo, base=base, parser_factory=refuse)
    with pytest.raises(ValueError, match="must not be blank"):
        semantic_diff.resolve_git_diff(repo, base=" ")
    with pytest.raises(ValueError, match="git semantic diff failed"):
        semantic_diff.resolve_git_diff(repo, base="missing-ref")


def test_missing_git_binary_is_fail_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    with pytest.raises(ValueError, match="git is not installed"):
        semantic_diff._git(tmp_path, ("status",))


def test_non_utf8_declaration_name_widens_instead_of_crashing(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    (repo / "worker.py").write_bytes(b"def r\xffn():\n    return 2\n")

    record = semantic_diff.resolve_git_diff(repo, base=base)[0]

    assert record.claim_paths == ("worker.py",)
    assert record.narrowed is False


def test_non_regular_working_source_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="not a regular file"):
        semantic_diff._working_source(tmp_path, "missing.py")
    (tmp_path / "target.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to("target.py")
    with pytest.raises(OSError, match="not a regular file"):
        semantic_diff._working_source(tmp_path, "linked.py")


def test_parser_boundary_failures_widen_instead_of_escaping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    _write(repo, "worker.py", "def run():\n    return 2\n")

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "_working_source",
            lambda _root, _path: (_ for _ in ()).throw(OSError("not regular")),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "source side is not a regular file"

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "extract_declarations",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                UnicodeDecodeError("utf-8", b"x", 0, 1, "invalid")
            ),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "declaration name is not valid UTF-8"

    with monkeypatch.context() as patch:
        patch.setattr(
            semantic_diff,
            "extract_declarations",
            lambda *_args, **_kwargs: (Declaration("unsafe\nname", 1, 2),),
        )
        record = semantic_diff.resolve_git_diff(repo, base=base)[0]
        assert record.reason == "declaration cannot form a safe semantic path"


def test_empty_internal_ranges_keep_the_defensive_whole_file_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    language = language_for_path("worker.py")
    assert language is not None
    changed = semantic_diff.ChangedFile("M", "worker.py", "worker.py")
    monkeypatch.setattr(semantic_diff, "_revision_source", lambda *_args: b"pass\n")
    monkeypatch.setattr(semantic_diff, "_working_source", lambda *_args: b"pass\n")
    monkeypatch.setattr(semantic_diff, "extract_declarations", lambda *_args, **_kwargs: ())

    record = semantic_diff._narrow_modified(
        tmp_path,
        changed,
        base="main",
        head=None,
        language=language[0],
        spec=language[1],
        parser_factory=default_parser,
    )

    assert record.reason == "changed line is outside a named declaration"


def test_empty_and_json_evidence_shapes_are_stable(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})
    assert semantic_diff.resolve_git_diff(repo, base=base) == ()

    _write(repo, "worker.py", "def run():\n    return 2\n")
    payload = semantic_diff.records_to_json(semantic_diff.resolve_git_diff(repo, base=base))

    assert payload[0]["kind"] == "diff"
    assert payload[0]["narrowed"] is True
    assert payload[0]["semantic_scopes"] == ["worker.py/.synapse-symbol/run"]


def test_tool_combines_symbol_scope_with_test_and_generated_companions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _repo(
        tmp_path,
        {
            "src/synapse_channel/worker.py": "def run():\n    return 1\n",
            "tests/test_worker.py": (
                "from synapse_channel.worker import run\n\ndef test_run():\n    assert run() == 1\n"
            ),
            "README.md": "# Project\n",
            "docs/_generated/capability_manifest.json": "{}\n",
            "tools/capability_manifest.py": "",
            "tools/capability_manifest.toml": "",
            "pyproject.toml": "",
        },
    )
    _write(repo, "src/synapse_channel/worker.py", "def run():\n    return 2\n")

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["claim_paths"] == [
        "src/synapse_channel/worker.py/.synapse-symbol/run",
        "tests/test_worker.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    ]
    assert "widens to a whole-file claim" in document["note"]

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--claim-args"]) == 0
    claim_args = capsys.readouterr().out
    assert "--paths src/synapse_channel/worker.py/.synapse-symbol/run" in claim_args
    assert "--paths tests/test_worker.py" in claim_args


def test_tool_human_check_empty_and_error_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, base = _repo(tmp_path, {"worker.py": "def run():\n    return 1\n"})

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--check"]) == 0
    assert "0 file(s), 0 narrowed, 0 whole-file" in capsys.readouterr().out

    _write(repo, "worker.py", "def run():\n    return 2\n")
    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", base, "--check"]) == 0
    output = capsys.readouterr().out
    assert "M worker.py: symbols=run" in output
    assert "1 file(s), 1 narrowed" in output

    assert semantic_diff_tool.main(["--repo-root", str(repo), "--base", "missing-ref"]) == 2
    assert "semantic diff claim error" in capsys.readouterr().err
