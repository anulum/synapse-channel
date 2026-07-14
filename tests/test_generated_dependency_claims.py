# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — generated dependency claim regressions

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "generated_dependency_claims.py"


def _load_tool() -> ModuleType:
    return importlib.import_module("tools.generated_dependency_claims")


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def claims_tool() -> ModuleType:
    if not TOOL.exists():
        pytest.fail(f"missing generated dependency claim tool: {TOOL}")
    return _load_tool()


def test_current_repo_source_filter_reports_generated_outputs() -> None:
    result = _run_tool("--json", "--source", "src/synapse_channel/core/receipts.py")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert {entry["generated"] for entry in payload} == {
        "README.md",
        "docs/_generated/capability_manifest.json",
    }
    for entry in payload:
        assert "src/synapse_channel/**/*.py" in entry["dependencies"]


def test_current_repo_claim_args_are_stable_for_source_filter() -> None:
    result = _run_tool("--claim-args", "--source", "tests/test_release_receipts.py")

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == (
        "--paths=README.md --paths=docs/_generated/capability_manifest.json"
    )


def test_generated_filter_reports_dependencies(claims_tool: ModuleType) -> None:
    records = claims_tool.build_dependency_map(REPO_ROOT)

    selected, unknown = claims_tool.select_records(
        records,
        requested_sources=(),
        requested_generated=(Path("docs/_generated/capability_manifest.json"),),
        repo_root=REPO_ROOT,
    )

    assert unknown == ()
    assert [record.generated for record in selected] == ["docs/_generated/capability_manifest.json"]
    assert "tools/capability_manifest.py" in selected[0].dependencies
    assert "pyproject.toml" in selected[0].dependencies


def test_path_matching_supports_absolute_source_paths(claims_tool: ModuleType) -> None:
    records = claims_tool.build_dependency_map(REPO_ROOT)
    source = REPO_ROOT / "src" / "synapse_channel" / "core" / "receipts.py"

    selected, unknown = claims_tool.select_records(
        records,
        requested_sources=(source,),
        requested_generated=(),
        repo_root=REPO_ROOT,
    )

    assert unknown == ()
    assert {record.generated for record in selected} == {
        "README.md",
        "docs/_generated/capability_manifest.json",
    }


def test_temp_repo_check_detects_missing_generated_output(
    tmp_path: Path,
    claims_tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path / "tools" / "capability_manifest.py")
    _write(tmp_path / "tools" / "capability_manifest.toml")
    _write(tmp_path / "src" / "synapse_channel" / "core.py")

    exit_code = claims_tool.main(["--repo-root", str(tmp_path), "--check"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "missing generated output: README.md" in captured.err
    assert "missing generated output: docs/_generated/capability_manifest.json" in captured.err


def test_temp_repo_check_detects_unmatched_dependency_globs(
    tmp_path: Path,
    claims_tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path / "README.md")
    _write(tmp_path / "docs" / "_generated" / "capability_manifest.json", "{}\n")
    _write(tmp_path / "tools" / "capability_manifest.py")
    _write(tmp_path / "tools" / "capability_manifest.toml")

    exit_code = claims_tool.main(["--repo-root", str(tmp_path), "--check"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "dependency pattern matched no files: src/synapse_channel/**/*.py" in captured.err


def test_main_direct_branches_cover_json_human_claim_args_and_unknown(
    tmp_path: Path,
    claims_tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path / "README.md")
    _write(tmp_path / "docs" / "_generated" / "capability_manifest.json", "{}\n")
    _write(tmp_path / "tools" / "capability_manifest.py")
    _write(tmp_path / "tools" / "capability_manifest.toml")
    _write(tmp_path / "pyproject.toml")
    _write(tmp_path / "src" / "synapse_channel" / "cli.py")

    assert (
        claims_tool.main(["--repo-root", str(tmp_path), "--source", "src/synapse_channel/cli.py"])
        == 0
    )
    assert "README.md <-" in capsys.readouterr().out

    assert claims_tool.main(["--repo-root", str(tmp_path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["generated"] == "README.md"

    assert claims_tool.main(["--repo-root", str(tmp_path), "--claim-args"]) == 0
    assert capsys.readouterr().out.strip() == (
        "--paths=README.md --paths=docs/_generated/capability_manifest.json"
    )

    assert claims_tool.main(["--repo-root", str(tmp_path), "--generated", "missing.txt"]) == 2
    assert "unknown generated output: missing.txt" in capsys.readouterr().err


def test_main_direct_branches_cover_check_success_and_empty_render(
    tmp_path: Path,
    claims_tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(tmp_path / "README.md")
    _write(tmp_path / "docs" / "_generated" / "capability_manifest.json", "{}\n")
    _write(tmp_path / "tools" / "capability_manifest.py")
    _write(tmp_path / "tools" / "capability_manifest.toml")
    _write(tmp_path / "src" / "synapse_channel" / "cli.py")
    _write(tmp_path / "src" / "synapse_channel" / "core" / "receipts.py")

    assert claims_tool.main(["--repo-root", str(tmp_path), "--check"]) == 0
    assert "generated dependency map passed: 2 generated output(s)" in capsys.readouterr().out

    monkeypatch.setattr(claims_tool, "build_dependency_map", lambda repo_root: ())
    assert claims_tool.main(["--repo-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""


def test_direct_unknown_source_and_outside_absolute_path_branches(
    tmp_path: Path,
    claims_tool: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path / "untracked" / "source.txt")
    outside = tmp_path.parent / "outside.py"
    _write(outside)

    assert claims_tool._normalise_requested_path(outside, tmp_path) == outside.as_posix()
    assert claims_tool.main(["--repo-root", str(tmp_path), "--source", "untracked/source.txt"]) == 2
    assert "source path matches no generated dependency rule: untracked/source.txt" in (
        capsys.readouterr().err
    )


def test_no_matching_source_filter_is_a_cli_error() -> None:
    result = _run_tool("--source", "src/synapse_channel/not_a_real_module.py")

    assert result.returncode == 2
    assert "source path matches no generated dependency rule" in result.stderr
    assert "src/synapse_channel/not_a_real_module.py" in result.stderr


def test_docs_wire_generated_dependency_claims_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
        ]
    )

    assert "tools/generated_dependency_claims.py --claim-args" in combined
    assert "generated-output dependency" in combined
