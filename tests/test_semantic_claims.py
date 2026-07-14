# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — semantic claim resolver regressions

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "semantic_claims.py"


def _load_tool(module_name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


semantic_claims = _load_tool("semantic_claims", TOOL)


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


def _build_temp_repo(root: Path) -> None:
    _write(
        root / "src" / "synapse_channel" / "core" / "receipts.py",
        """
def build_release_receipt():
    return {}


class ReleaseReceipt:
    pass
""",
    )
    _write(
        root / "tests" / "test_release_receipts.py",
        """
from synapse_channel.core.receipts import ReleaseReceipt, build_release_receipt


def test_receipt():
    assert build_release_receipt() == {}
    assert ReleaseReceipt
""",
    )
    _write(root / "README.md")
    _write(root / "docs" / "_generated" / "capability_manifest.json", "{}\n")
    _write(root / "tools" / "capability_manifest.py")
    _write(root / "tools" / "capability_manifest.toml")
    _write(root / "pyproject.toml")


def test_current_repo_symbol_selector_json_resolves_full_claim_surface() -> None:
    result = _run_tool(
        "--selector",
        "symbol:synapse_channel.core.receipts.build_release_receipt",
        "--json",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload == [
        {
            "selector": "symbol:synapse_channel.core.receipts.build_release_receipt",
            "kind": "symbol",
            "value": "synapse_channel.core.receipts.build_release_receipt",
            "sources": ["src/synapse_channel/core/receipts.py"],
            "modules": ["synapse_channel.core.receipts"],
            "symbols": ["build_release_receipt"],
            "semantic_scopes": [
                "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt"
            ],
            "tests": ["tests/test_hub_core_claims.py", "tests/test_release_receipts.py"],
            "generated": ["README.md", "docs/_generated/capability_manifest.json"],
            "claim_paths": [
                "src/synapse_channel/core/receipts.py/.synapse-symbol/build_release_receipt",
                "tests/test_hub_core_claims.py",
                "tests/test_release_receipts.py",
                "README.md",
                "docs/_generated/capability_manifest.json",
            ],
        }
    ]


def test_current_repo_claim_args_are_stable_for_api_selector() -> None:
    result = _run_tool(
        "--selector",
        "api:synapse_channel.core.receipts.ReleaseReceipt",
        "--claim-args",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert result.stdout.strip() == (
        "--paths=src/synapse_channel/core/receipts.py/.synapse-symbol/ReleaseReceipt "
        "--paths=tests/test_hub_core_claims.py "
        "--paths=tests/test_release_receipts.py "
        "--paths=README.md "
        "--paths=docs/_generated/capability_manifest.json"
    )


def test_test_and_generated_selectors_resolve_current_repo_surfaces() -> None:
    result = _run_tool(
        "--selector",
        "test:tests/test_release_receipts.py",
        "--selector",
        "generated:docs/_generated/capability_manifest.json",
        "--json",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert [entry["kind"] for entry in payload] == ["test", "generated"]
    assert payload[0]["sources"] == ["src/synapse_channel/core/receipts.py"]
    assert payload[0]["tests"] == ["tests/test_release_receipts.py"]
    assert payload[1]["generated"] == ["docs/_generated/capability_manifest.json"]
    assert payload[1]["claim_paths"] == ["docs/_generated/capability_manifest.json"]


def test_api_test_and_generated_selectors_resolve_directly() -> None:
    records = semantic_claims.resolve_selectors(
        REPO_ROOT,
        (
            "api:synapse_channel.core.receipts.ReleaseReceipt",
            "test:tests/test_release_receipts.py",
            "generated:docs/_generated/capability_manifest.json",
        ),
    )

    assert [record.kind for record in records] == ["api", "test", "generated"]
    assert records[0].symbols == ("ReleaseReceipt",)
    assert records[0].semantic_scopes == (
        "src/synapse_channel/core/receipts.py/.synapse-symbol/ReleaseReceipt",
    )
    assert records[1].sources == ("src/synapse_channel/core/receipts.py",)
    assert records[2].claim_paths == ("docs/_generated/capability_manifest.json",)


def test_module_source_and_migration_selectors_resolve_temp_repo(
    tmp_path: Path,
) -> None:
    _build_temp_repo(tmp_path)
    _write(tmp_path / "migrations" / "001_initial.sql", "create table t(id integer);\n")

    records = semantic_claims.resolve_selectors(
        tmp_path,
        (
            "module:synapse_channel.core.receipts",
            "source:src/synapse_channel/core/receipts.py",
            "migration:migrations/001_initial.sql",
        ),
    )

    assert [record.kind for record in records] == ["module", "source", "migration"]
    assert records[0].symbols == ("ReleaseReceipt", "build_release_receipt")
    assert records[0].semantic_scopes == ()
    assert records[0].tests == ("tests/test_release_receipts.py",)
    assert records[0].generated == (
        "README.md",
        "docs/_generated/capability_manifest.json",
    )
    assert records[2].claim_paths == ("migrations/001_initial.sql",)


def test_absolute_source_and_migration_paths_are_normalised(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)
    migration = tmp_path / "migrations" / "001_initial.sql"
    _write(migration, "create table t(id integer);\n")

    records = semantic_claims.resolve_selectors(
        tmp_path,
        (
            f"source:{tmp_path / 'src' / 'synapse_channel' / 'core' / 'receipts.py'}",
            f"migration:{migration}",
        ),
    )

    assert records[0].value == "src/synapse_channel/core/receipts.py"
    assert records[1].value == "migrations/001_initial.sql"

    outside = tmp_path.parent / "outside.py"
    _write(outside)
    assert (
        semantic_claims.main(["--repo-root", str(tmp_path), "--selector", f"source:{outside}"]) == 2
    )
    assert outside.as_posix() in capsys.readouterr().err


def test_check_prints_resolution_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)

    exit_code = semantic_claims.main(
        [
            "--repo-root",
            str(tmp_path),
            "--selector",
            "symbol:synapse_channel.core.receipts.build_release_receipt",
            "--check",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "symbol:synapse_channel.core.receipts.build_release_receipt ->" in (captured.out)
    assert "tests/test_release_receipts.py" in captured.out
    assert "semantic claim resolution passed: 1 selector(s), 4 claim path(s)" in (captured.out)


def test_main_direct_json_and_claim_args(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _build_temp_repo(tmp_path)

    assert (
        semantic_claims.main(
            [
                "--repo-root",
                str(tmp_path),
                "--selector",
                "module:synapse_channel.core.receipts",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["selector"] == "module:synapse_channel.core.receipts"

    assert (
        semantic_claims.main(
            [
                "--repo-root",
                str(tmp_path),
                "--selector",
                "module:synapse_channel.core.receipts",
                "--claim-args",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.strip() == (
        "--paths=src/synapse_channel/core/receipts.py "
        "--paths=tests/test_release_receipts.py "
        "--paths=README.md "
        "--paths=docs/_generated/capability_manifest.json"
    )


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("symbol:synapse_channel.core.receipts.missing_symbol", "unknown symbol selector"),
        ("module:synapse_channel.core.missing", "unknown module selector"),
        ("source:src/synapse_channel/core/missing.py", "unknown source selector"),
        ("test:tests/test_missing.py", "unknown test selector"),
        ("generated:missing.json", "unknown generated selector"),
        ("migration:missing.sql", "unknown migration selector"),
        ("badselector", "selector must use kind:value"),
        ("unknown:value", "unsupported semantic selector kind"),
    ],
)
def test_unknown_selectors_are_cli_errors(selector: str, message: str) -> None:
    result = _run_tool("--selector", selector)

    assert result.returncode == 2
    assert message in result.stderr
    assert selector in result.stderr


@pytest.mark.parametrize(
    ("selector", "message"),
    [
        ("symbol:synapse_channel.core.receipts.missing_symbol", "unknown symbol selector"),
        ("module:synapse_channel.core.missing", "unknown module selector"),
        ("source:src/synapse_channel/core/missing.py", "unknown source selector"),
        ("test:tests/test_missing.py", "unknown test selector"),
        ("generated:missing.json", "unknown generated selector"),
        ("migration:missing.sql", "unknown migration selector"),
        ("badselector", "selector must use kind:value"),
        ("unknown:value", "unsupported semantic selector kind"),
        ("symbol:nosymbol", "unknown symbol selector"),
        ("module:", "selector value must not be empty"),
    ],
)
def test_main_direct_unknown_selectors_are_cli_errors(
    selector: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert semantic_claims.main(["--selector", selector]) == 2
    captured = capsys.readouterr()
    assert message in captured.err
    assert selector in captured.err


def test_main_requires_at_least_one_selector(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert semantic_claims.main([]) == 2
    assert "at least one --selector is required" in capsys.readouterr().err


def test_main_handles_empty_rendered_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(semantic_claims, "resolve_selectors", lambda repo_root, selectors: ())

    assert semantic_claims.main(["--selector", "module:synapse_channel.empty"]) == 0
    assert capsys.readouterr().out == ""


def test_records_to_json_and_render_human_cover_empty_and_deduplicated_paths() -> None:
    record = semantic_claims.SemanticClaimRecord(
        selector="module:synapse_channel.a",
        kind="module",
        value="synapse_channel.a",
        sources=("src/synapse_channel/a.py",),
        modules=("synapse_channel.a",),
        symbols=("A",),
        semantic_scopes=(),
        tests=("tests/test_a.py",),
        generated=("README.md",),
        claim_paths=(
            "src/synapse_channel/a.py",
            "tests/test_a.py",
            "README.md",
        ),
    )

    assert semantic_claims.records_to_json((record,))[0]["claim_paths"] == [
        "src/synapse_channel/a.py",
        "tests/test_a.py",
        "README.md",
    ]
    assert semantic_claims.render_human(()) == ""
    assert semantic_claims.render_human((record,)) == (
        "module:synapse_channel.a -> src/synapse_channel/a.py, tests/test_a.py, README.md"
    )


def test_companion_paths_reuse_test_and_generated_maps() -> None:
    assert semantic_claims.companion_claim_paths(
        REPO_ROOT,
        ("src/synapse_channel/core/receipts.py", "clients/js/src/client.ts"),
    ) == (
        "tests/test_hub_core_claims.py",
        "tests/test_release_receipts.py",
        "README.md",
        "docs/_generated/capability_manifest.json",
    )


def test_docs_wire_semantic_claim_resolver_command() -> None:
    combined = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
            (REPO_ROOT / "docs" / "coordination-model.md").read_text(encoding="utf-8"),
        ]
    )

    assert "tools/semantic_claims.py --selector" in combined
    assert "synapse git-claim` accepts the same selector kinds" in combined
    assert "--semantic-evidence-json" in combined
    assert "semantic claim resolver" in combined
