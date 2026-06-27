# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — import merge-risk radar regressions

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "import_merge_risk.py"


def _load_tool(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


import_merge_risk = _load_tool("import_merge_risk", TOOL)


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_tool(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo_root), *args], check=True, capture_output=True)


def _build_temp_repo(root: Path) -> None:
    _write(
        root / "src" / "synapse_channel" / "a.py",
        """
from synapse_channel import b


def alpha():
    return b.beta()
""",
    )
    _write(
        root / "src" / "synapse_channel" / "b.py",
        """
def beta():
    return "b"
""",
    )
    _write(
        root / "src" / "synapse_channel" / "c.py",
        """
def gamma():
    return "c"
""",
    )
    _write(
        root / "tests" / "test_shared.py",
        """
import synapse_channel.a
import synapse_channel.b


def test_shared():
    assert synapse_channel.a.alpha() == "b"
    assert synapse_channel.b.beta() == "b"
""",
    )
    _write(
        root / ".github" / "CODEOWNERS",
        """
* @global
/src/synapse_channel/a.py @alpha
src/synapse_channel/b.py @beta @review
""",
    )


def test_cli_json_reports_import_neighbour_shared_tests_and_codeowners(
    tmp_path: Path,
) -> None:
    _build_temp_repo(tmp_path)

    result = _run_tool(
        "--repo-root",
        str(tmp_path),
        "--changed",
        "src/synapse_channel/a.py",
        "--claimed",
        "src/synapse_channel/b.py",
        "--json",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    kinds = {entry["kind"] for entry in payload}
    assert kinds == {"import-neighbour", "shared-test-owner"}
    assert payload[0]["changed_path"] == "src/synapse_channel/a.py"
    assert payload[0]["claimed_path"] == "src/synapse_channel/b.py"
    assert sorted({owner for entry in payload for owner in entry["owners"]}) == [
        "@alpha",
        "@beta",
        "@review",
    ]
    assert {test for entry in payload for test in entry["tests"]} == {"tests/test_shared.py"}
    assert any(
        entry["reason"] == "synapse_channel.a imports synapse_channel.b" for entry in payload
    )


def test_check_fails_for_direct_overlap(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)

    exit_code = import_merge_risk.main(
        [
            "--repo-root",
            str(tmp_path),
            "--changed",
            "src/synapse_channel/a.py",
            "--claimed",
            "src/synapse_channel/a.py",
            "--check",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "direct-overlap" in captured.out
    assert "src/synapse_channel/a.py" in captured.out


def test_check_passes_when_changed_and_claimed_paths_are_independent(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)

    exit_code = import_merge_risk.main(
        [
            "--repo-root",
            str(tmp_path),
            "--changed",
            "src/synapse_channel/c.py",
            "--claimed",
            "README.md",
            "--check",
        ]
    )

    assert exit_code == 0
    assert "import merge-risk radar passed: no risks found" in capsys.readouterr().out

    assert (
        import_merge_risk.main(
            [
                "--repo-root",
                str(tmp_path),
                "--changed",
                "src/synapse_channel/c.py",
                "--claimed",
                "src/synapse_channel/b.py",
                "--check",
            ]
        )
        == 0
    )


def test_claims_json_supplies_claimed_paths(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    claims_json = tmp_path / "claims.json"
    claims_json.write_text(
        json.dumps({"claims": [{"task": "T-1", "paths": ["src/synapse_channel/b.py"]}]}),
        encoding="utf-8",
    )

    records = import_merge_risk.find_merge_risks(
        tmp_path,
        changed_paths=(Path("src/synapse_channel/a.py"),),
        claimed_paths=(),
        claims_json_paths=(claims_json,),
    )

    assert {record.kind for record in records} == {"import-neighbour", "shared-test-owner"}
    assert records[0].claimed_path == "src/synapse_channel/b.py"


def test_cli_reads_git_branch_diff(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.invalid")
    _run_git(tmp_path, "config", "user.name", "Test User")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "initial")
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _write(
        tmp_path / "src" / "synapse_channel" / "a.py",
        """
from synapse_channel import b


def alpha():
    return b.beta().upper()
""",
    )
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", "change a")

    result = _run_tool(
        "--repo-root",
        str(tmp_path),
        "--base",
        base,
        "--head",
        "HEAD",
        "--claimed",
        "src/synapse_channel/b.py",
        "--json",
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert {entry["changed_path"] for entry in json.loads(result.stdout)} == {
        "src/synapse_channel/a.py"
    }
    direct_records = import_merge_risk.find_merge_risks(
        tmp_path,
        claimed_paths=(Path("src/synapse_channel/b.py"),),
        base=base,
        head="HEAD",
    )
    assert {record.changed_path for record in direct_records} == {"src/synapse_channel/a.py"}


def test_codeowners_last_match_and_missing_file_branch(tmp_path: Path) -> None:
    _write(
        tmp_path / "CODEOWNERS",
        """
*.py @python
docs/
src/synapse_channel/a.py @first
src/synapse_channel/a.py @last @owner
docs/ @docs
""",
    )

    rules = import_merge_risk.load_codeowners(tmp_path)

    assert import_merge_risk.codeowners_for_path(rules, "src/synapse_channel/a.py") == (
        "@last",
        "@owner",
    )
    assert import_merge_risk.codeowners_for_path(rules, "docs/git-claims.md") == ("@docs",)
    assert import_merge_risk.load_codeowners(tmp_path / "empty") == ()
    assert import_merge_risk.codeowners_for_path((), "src/synapse_channel/a.py") == ()


def test_import_graph_handles_package_init_import_forms_and_empty_roots(
    tmp_path: Path,
) -> None:
    assert import_merge_risk.build_import_graph(tmp_path) == ()
    _write(
        tmp_path / "src" / "synapse_channel" / "__init__.py",
        """
from . import local
from pathlib import Path
from synapse_channel.b import *
from synapse_channel.b import beta
import json
import synapse_channel.b
""",
    )
    _write(tmp_path / "src" / "synapse_channel" / "b.py", "def beta():\n    return 'b'\n")

    records = import_merge_risk.build_import_graph(tmp_path)

    assert records == (
        import_merge_risk.SourceImportRecord(
            source="src/synapse_channel/__init__.py",
            module="synapse_channel",
            imports=("synapse_channel.b",),
        ),
        import_merge_risk.SourceImportRecord(
            source="src/synapse_channel/b.py",
            module="synapse_channel.b",
            imports=(),
        ),
    )


def test_reverse_import_neighbour_and_json_helpers(tmp_path: Path) -> None:
    _build_temp_repo(tmp_path)

    records = import_merge_risk.find_merge_risks(
        tmp_path,
        changed_paths=(Path("src/synapse_channel/b.py"),),
        claimed_paths=(Path("src/synapse_channel/a.py"),),
    )

    assert any(record.reason == "synapse_channel.a imports synapse_channel.b" for record in records)
    payload = import_merge_risk.records_to_json(records)
    assert payload[0]["changed_path"] == "src/synapse_channel/b.py"
    assert import_merge_risk._repo_relative(tmp_path.parent / "outside.py", tmp_path).endswith(
        "outside.py"
    )
    assert import_merge_risk._paths_from_claim_payload(123) == ()


def test_human_render_covers_records_without_owners_and_related_only() -> None:
    direct = import_merge_risk.MergeRiskRecord(
        kind="direct-overlap",
        changed_path="README.md",
        claimed_path="README.md",
        reason="changed path overlaps claimed path",
        related_paths=(),
        owners=(),
        tests=(),
    )
    related = import_merge_risk.MergeRiskRecord(
        kind="import-neighbour",
        changed_path="src/a.py",
        claimed_path="src/b.py",
        reason="a imports b",
        related_paths=("src/a.py", "src/b.py"),
        owners=(),
        tests=(),
    )

    rendered = import_merge_risk.render_human((direct, related))

    assert "direct-overlap: README.md <-> README.md" in rendered
    assert "related src/a.py, src/b.py" in rendered


def test_invalid_claims_json_is_cli_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)
    claims_json = tmp_path / "claims.json"
    claims_json.write_text("{not-json", encoding="utf-8")

    exit_code = import_merge_risk.main(
        [
            "--repo-root",
            str(tmp_path),
            "--changed",
            "src/synapse_channel/a.py",
            "--claims-json",
            str(claims_json),
        ]
    )

    assert exit_code == 2
    assert "invalid claims JSON" in capsys.readouterr().err


def test_missing_claims_json_and_bad_git_diff_are_cli_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)

    assert (
        import_merge_risk.main(
            [
                "--repo-root",
                str(tmp_path),
                "--changed",
                "src/synapse_channel/a.py",
                "--claims-json",
                str(tmp_path / "missing.json"),
            ]
        )
        == 2
    )
    assert "cannot read claims JSON" in capsys.readouterr().err

    assert (
        import_merge_risk.main(
            [
                "--repo-root",
                str(tmp_path),
                "--base",
                "missing-base",
                "--claimed",
                "src/synapse_channel/a.py",
            ]
        )
        == 2
    )
    assert capsys.readouterr().err


def test_json_output_for_empty_inputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _run_tool("--repo-root", str(tmp_path), "--json")

    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout) == []
    assert import_merge_risk.main(["--repo-root", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert import_merge_risk.find_merge_risks(tmp_path) == ()


def test_docs_wire_import_merge_risk_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "git-claims.md").read_text(encoding="utf-8"),
        ]
    )

    assert "tools/import_merge_risk.py --changed" in combined
    assert "import graph merge-risk radar" in combined
