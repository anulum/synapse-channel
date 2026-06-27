# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — test ownership map regressions

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from tools import test_ownership_map as ownership

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "test_ownership_map.py"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_tool(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_ast_imports_connect_tests_symbols_and_sources(tmp_path: Path) -> None:
    _write(
        tmp_path / "src" / "synapse_channel" / "core" / "receipts.py",
        """
def build_release_receipt():
    return {}


class ReleaseReceipt:
    pass
""",
    )
    _write(
        tmp_path / "tests" / "test_release_receipts.py",
        """
from synapse_channel.core.receipts import ReleaseReceipt, build_release_receipt


def test_receipt():
    assert build_release_receipt() == {}
    assert ReleaseReceipt
""",
    )

    records = ownership.build_ownership_map(tmp_path)

    assert records == (
        ownership.OwnershipRecord(
            source="src/synapse_channel/core/receipts.py",
            module="synapse_channel.core.receipts",
            symbols=("ReleaseReceipt", "build_release_receipt"),
            test_owners=(
                ownership.TestOwner(
                    path="tests/test_release_receipts.py",
                    reasons=("imports synapse_channel.core.receipts",),
                    imported_symbols=("ReleaseReceipt", "build_release_receipt"),
                ),
            ),
        ),
    )


def test_path_fallback_uses_longest_existing_source_prefix(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "synapse_channel" / "cli.py", "def main():\n    return 0\n")
    _write(
        tmp_path / "src" / "synapse_channel" / "cli_locking.py",
        "def release():\n    return None\n",
    )
    _write(
        tmp_path / "tests" / "test_cli_locking_release.py",
        "def test_release():\n    assert True\n",
    )

    records = ownership.build_ownership_map(tmp_path)

    owners_by_source = {record.source: record.test_owners for record in records}
    assert owners_by_source["src/synapse_channel/cli_locking.py"] == (
        ownership.TestOwner(
            path="tests/test_cli_locking_release.py",
            reasons=("filename fallback test_cli_locking_release.py -> cli_locking.py",),
            imported_symbols=(),
        ),
    )
    assert owners_by_source["src/synapse_channel/cli.py"] == ()


def test_discovery_handles_empty_roots_and_ignored_modules(tmp_path: Path) -> None:
    assert ownership.discover_sources(tmp_path) == ()
    assert ownership.discover_tests(tmp_path) == ()

    _write(tmp_path / "src" / "synapse_channel" / "__init__.py", "__all__ = []\n")
    _write(tmp_path / "src" / "synapse_channel" / "__pycache__" / "cached.py", "x = 1\n")
    _write(tmp_path / "tests" / "helper.py", "def test_not_collected():\n    pass\n")

    assert ownership.discover_sources(tmp_path) == ()
    assert ownership.discover_tests(tmp_path) == ()


def test_imported_modules_handles_supported_and_ignored_import_forms(tmp_path: Path) -> None:
    test_path = tmp_path / "tests" / "test_imports.py"
    _write(
        test_path,
        """
import json
import synapse_channel.a2a as a2a
from . import local
from synapse_channel.core.receipts import *
from synapse_channel.core.receipts import build_release_receipt
from pathlib import Path
from synapse_channel import cli
""",
    )

    assert ownership.imported_modules(test_path) == (
        ownership.ImportedModule(module="synapse_channel", symbols=("cli",)),
        ownership.ImportedModule(module="synapse_channel.a2a", symbols=()),
        ownership.ImportedModule(
            module="synapse_channel.core.receipts",
            symbols=("build_release_receipt",),
        ),
    )


def test_nested_imports_merge_owner_evidence(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "synapse_channel" / "a2a.py", "def bridge():\n    return None\n")
    _write(
        tmp_path / "tests" / "test_a2a.py",
        """
import synapse_channel.a2a
from synapse_channel import a2a


def test_a2a():
    assert synapse_channel.a2a
""",
    )

    records = ownership.build_ownership_map(tmp_path)

    assert records == (
        ownership.OwnershipRecord(
            source="src/synapse_channel/a2a.py",
            module="synapse_channel.a2a",
            symbols=("bridge",),
            test_owners=(
                ownership.TestOwner(
                    path="tests/test_a2a.py",
                    reasons=(
                        "filename fallback test_a2a.py -> a2a.py",
                        "imports synapse_channel.a2a",
                    ),
                    imported_symbols=(),
                ),
            ),
        ),
    )


def test_fallback_ignores_non_test_names_and_ambiguous_sources() -> None:
    source = ownership.SourceModule(
        source="src/synapse_channel/a2a.py",
        module="synapse_channel.a2a",
        symbols=(),
    )

    assert ownership._fallback_source_for_test(Path("helper.py"), {"a2a.py": (source,)}) is None
    assert (
        ownership._fallback_source_for_test(Path("test_a2a.py"), {"a2a.py": (source, source)})
        is None
    )


def test_record_helpers_cover_filters_json_and_empty_render(tmp_path: Path) -> None:
    record = ownership.OwnershipRecord(
        source="src/synapse_channel/a.py",
        module="synapse_channel.a",
        symbols=("A",),
        test_owners=(
            ownership.TestOwner(
                path="tests/test_a.py",
                reasons=("imports synapse_channel.a",),
                imported_symbols=("A",),
            ),
        ),
    )
    unowned = ownership.OwnershipRecord(
        source="src/synapse_channel/b.py",
        module="synapse_channel.b",
        symbols=(),
        test_owners=(),
    )
    absolute = tmp_path / "src" / "synapse_channel" / "a.py"

    assert ownership._select_records((record,), (), tmp_path) == ((record,), ())
    assert ownership._select_records((record,), (absolute,), tmp_path) == ((record,), ())
    assert ownership._select_records((record,), (Path("missing.py"),), tmp_path) == (
        (),
        ("missing.py",),
    )
    assert ownership._required_unowned((record, unowned), (Path(unowned.source),), tmp_path) == (
        unowned.source,
    )
    assert ownership._required_unowned((record,), (Path(record.source),), tmp_path) == ()
    assert ownership.records_to_json((record,)) == [
        {
            "source": "src/synapse_channel/a.py",
            "module": "synapse_channel.a",
            "symbols": ["A"],
            "test_owners": [
                {
                    "path": "tests/test_a.py",
                    "reasons": ["imports synapse_channel.a"],
                    "imported_symbols": ["A"],
                }
            ],
        }
    ]
    assert ownership.render_human(()) == ""
    assert ownership.render_human((unowned,)) == "src/synapse_channel/b.py -> (no mapped tests)"


def test_main_direct_branches_for_json_empty_and_unknown_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert ownership.main(["--repo-root", str(tmp_path), "--json"]) == 0
    assert capsys.readouterr().out.strip() == "[]"

    assert ownership.main(["--repo-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""

    assert ownership.main(["--repo-root", str(tmp_path), "--source", "missing.py"]) == 2
    assert "unknown source path: missing.py" in capsys.readouterr().err


def test_main_direct_check_prints_map_and_pass_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write(tmp_path / "src" / "synapse_channel" / "a.py", "def work():\n    return None\n")
    _write(
        tmp_path / "tests" / "test_a.py",
        "from synapse_channel.a import work\n\ndef test_work():\n    assert work() is None\n",
    )

    assert ownership.main(["--repo-root", str(tmp_path), "--check"]) == 0

    captured = capsys.readouterr()
    assert "src/synapse_channel/a.py -> tests/test_a.py" in captured.out
    assert "test ownership map passed: 1/1 source file(s) have mapped tests" in captured.out


def test_json_output_and_source_filter_cover_current_repo() -> None:
    result = _run_tool("--json", "--source", "src/synapse_channel/core/receipts.py")

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["source"] == "src/synapse_channel/core/receipts.py"
    assert payload[0]["module"] == "synapse_channel.core.receipts"
    assert "build_release_receipt" in payload[0]["symbols"]
    assert "tests/test_release_receipts.py" in {
        owner["path"] for owner in payload[0]["test_owners"]
    }


def test_check_requires_requested_owned_source_and_prints_human_map() -> None:
    result = _run_tool(
        "--check",
        "--source",
        "src/synapse_channel/core/receipts.py",
        "--require-owned",
        "src/synapse_channel/core/receipts.py",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "test ownership map passed" in result.stdout
    assert "src/synapse_channel/core/receipts.py ->" in result.stdout
    assert "tests/test_release_receipts.py" in result.stdout


def test_missing_source_filter_is_a_cli_error() -> None:
    result = _run_tool("--source", "src/synapse_channel/does_not_exist.py")

    assert result.returncode == 2
    assert "unknown source path" in result.stderr
    assert "src/synapse_channel/does_not_exist.py" in result.stderr


def test_require_owned_reports_unowned_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "src" / "synapse_channel" / "lonely.py", "def work():\n    return None\n")

    exit_code = ownership.main(
        [
            "--repo-root",
            str(tmp_path),
            "--require-owned",
            "src/synapse_channel/lonely.py",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unowned required source" in captured.err
    assert "src/synapse_channel/lonely.py" in captured.err


def test_docs_wire_the_test_ownership_map_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
        ]
    )

    assert "tools/test_ownership_map.py --check" in combined
    assert "test ownership map" in combined
