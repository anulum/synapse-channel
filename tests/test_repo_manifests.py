# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — repository manifest reader regressions

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from synapse_channel.core.repo_manifests import (
    discover_repositories,
    normalise_python_name,
    read_repo_manifest,
    requirement_constraint,
    requirement_name,
)


def _repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def test_pyproject_declares_package_and_normalised_dependencies(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "alpha",
        {
            "pyproject.toml": (
                '[project]\nname = "Alpha_Kit"\n'
                'dependencies = ["websockets>=12,<16",'
                " \"Beta.Lib[extra] ; python_version >= '3.11'\"]\n"
                "[project.optional-dependencies]\n"
                'dev = ["pytest>=8", "websockets"]\n'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    assert [(pkg.name, pkg.ecosystem) for pkg in manifest.packages] == [("alpha-kit", "python")]
    names = [dep.name for dep in manifest.dependencies]
    # extras and markers stripped, PEP 503 normalised, duplicates collapsed
    assert names == ["websockets", "beta-lib", "pytest"]
    assert all(dep.manifest == "pyproject.toml" for dep in manifest.dependencies)
    assert manifest.problems == ()


def test_pyproject_without_project_table_declares_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "tool-only", {"pyproject.toml": "[tool.ruff]\nline-length = 99\n"})
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert manifest.dependencies == ()
    assert manifest.problems == ()


def test_cargo_manifest_reads_all_three_tables_and_renames(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "crate",
        {
            "Cargo.toml": (
                '[package]\nname = "Fast-Core"\n\n'
                '[dependencies]\nserde = "1"\n'
                'renamed = { package = "Real-Name", version = "2" }\n\n'
                '[dev-dependencies]\nserde = "1"\ncriterion = "0.5"\n\n'
                '[build-dependencies]\ncc = "1"\n'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    assert [pkg.name for pkg in manifest.packages] == ["fast-core"]
    assert [dep.name for dep in manifest.dependencies] == ["serde", "real-name", "criterion", "cc"]
    assert {dep.ecosystem for dep in manifest.dependencies} == {"rust"}


def test_package_json_reads_scoped_names_and_three_tables(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "webapp",
        {
            "package.json": (
                '{"name": "@Org/WebApp", "dependencies": {"react": "^18"},'
                ' "devDependencies": {"vite": "^5", "react": "^18"},'
                ' "peerDependencies": {"@org/shared": "*"}}'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    assert [pkg.name for pkg in manifest.packages] == ["@org/webapp"]
    assert [dep.name for dep in manifest.dependencies] == ["react", "vite", "@org/shared"]


def test_invalid_json_manifest_is_a_visible_problem(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "broken-js", {"package.json": "{not json"})
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert len(manifest.problems) == 1
    assert manifest.problems[0].startswith("package.json:")


def test_non_object_json_manifest_is_a_visible_problem(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "array-js", {"package.json": "[1, 2]"})
    manifest = read_repo_manifest(repo)
    assert manifest.problems == ("package.json: top-level value is not an object",)


def test_invalid_toml_manifest_is_a_visible_problem(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "broken-py", {"pyproject.toml": "[project\nname ="})
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert len(manifest.problems) == 1
    assert manifest.problems[0].startswith("pyproject.toml:")


def test_missing_toml_parser_is_a_visible_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On Python 3.10 without tomli, TOML manifests must surface as problems,
    # never vanish silently from the graph.
    repo = _repo(tmp_path, "py310", {"pyproject.toml": '[project]\nname = "x"\n'})

    def refuse(name: str) -> Any:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr("synapse_channel.core.repo_manifests.importlib.import_module", refuse)
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert manifest.problems == (
        "pyproject.toml: TOML manifests need Python 3.11+ or the 'tomli' package",
    )


def test_go_mod_reads_module_block_and_single_line_requires(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "gosvc",
        {
            "go.mod": (
                "module example.com/org/gosvc // the service\n\n"
                "go 1.22\n\n"
                "require (\n"
                "\tgithub.com/gorilla/websocket v1.5.0\n"
                "\texample.com/org/shared v0.3.0 // indirect\n"
                "\tgithub.com/gorilla/websocket v1.5.0\n"
                ")\n\n"
                "require example.com/org/extra v1.0.0\n"
            )
        },
    )
    manifest = read_repo_manifest(repo)
    assert [pkg.name for pkg in manifest.packages] == ["example.com/org/gosvc"]
    assert [dep.name for dep in manifest.dependencies] == [
        "github.com/gorilla/websocket",
        "example.com/org/shared",
        "example.com/org/extra",
    ]


def test_codeowners_first_location_wins_and_collects_handles(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "owned",
        {
            ".github/CODEOWNERS": (
                "# global rule\n"
                "* @org/platform-team\n"
                "docs/ @writer ops@example.com\n"
                "pattern-without-owner\n"
            ),
            "CODEOWNERS": "* @should-not-be-read\n",
        },
    )
    manifest = read_repo_manifest(repo)
    assert manifest.owners == ("@org/platform-team", "@writer", "ops@example.com")


def test_repo_without_manifests_yields_an_empty_record(tmp_path: Path) -> None:
    repo = tmp_path / "bare"
    repo.mkdir()
    manifest = read_repo_manifest(repo)
    assert manifest.repo == "bare"
    assert manifest.packages == ()
    assert manifest.dependencies == ()
    assert manifest.owners == ()
    assert manifest.problems == ()


def test_requirement_name_extracts_or_rejects() -> None:
    assert requirement_name("Requests[socks]>=2.31 ; python_version < '3.13'") == "requests"
    assert requirement_name("  pkg_one.two-three==1") == "pkg-one-two-three"
    assert requirement_name("### not a requirement") == ""


def test_normalise_python_name_follows_pep_503() -> None:
    assert normalise_python_name("My__Weird..Pkg--Name") == "my-weird-pkg-name"


def test_pyproject_with_malformed_field_types_declares_nothing(tmp_path: Path) -> None:
    # Wrong-typed fields are treated as absent declarations, not crashes:
    # a numeric name, a string dependencies value, a list where the
    # optional-dependencies table should be, and a non-list extra group.
    repo = _repo(
        tmp_path,
        "odd-types",
        {
            "pyproject.toml": (
                '[project]\nname = 3\ndependencies = "not-a-list"\n'
                '[project.optional-dependencies]\ndev = "not-a-list"\n'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert manifest.dependencies == ()
    assert manifest.problems == ()


def test_cargo_rename_without_package_key_and_missing_tables(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "sparse-crate",
        {
            "Cargo.toml": (
                '[workspace]\nmembers = []\n\n[dependencies]\nplain = { version = "1" }\n'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    # no [package] table: the workspace root provides nothing
    assert manifest.packages == ()
    # a table entry without a `package` rename keeps its key as the crate name
    assert [dep.name for dep in manifest.dependencies] == ["plain"]


def test_cargo_package_table_with_non_string_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "unnamed-crate", {"Cargo.toml": "[package]\nname = 7\n"})
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()


def test_package_json_with_malformed_field_types(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "odd-js",
        {"package.json": '{"name": 7, "dependencies": ["not", "a", "table"]}'},
    )
    manifest = read_repo_manifest(repo)
    assert manifest.packages == ()
    assert manifest.dependencies == ()


def test_go_mod_keeps_only_the_first_module_line(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "twomod",
        {"go.mod": "module example.com/first\nmodule example.com/second\n"},
    )
    manifest = read_repo_manifest(repo)
    assert [pkg.name for pkg in manifest.packages] == ["example.com/first"]


def test_codeowners_skips_tokens_that_are_not_handles(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "plain-owner",
        {"CODEOWNERS": "docs/ plainword @writer\n"},
    )
    manifest = read_repo_manifest(repo)
    assert manifest.owners == ("@writer",)


def test_requirement_constraint_extracts_specifier_forms() -> None:
    assert requirement_constraint("websockets>=12,<16") == ">=12,<16"
    assert requirement_constraint("Beta.Lib[extra]==2.1 ; python_version >= '3.11'") == "==2.1"
    assert requirement_constraint("pkg (>=2, <3)") == ">=2, <3"
    assert requirement_constraint("plain-name") == ""
    assert requirement_constraint("### not a requirement") == ""
    assert requirement_constraint("pkg[unclosed>=1") == ""
    # a direct URL reference keeps its @ form: it pins a source, not a range
    assert requirement_constraint("pkg @ https://example.invalid/p.tar.gz").startswith("@")


def test_python_dependencies_carry_their_declared_constraints(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "constrained",
        {
            "pyproject.toml": (
                '[project]\nname = "c"\n'
                'dependencies = ["websockets>=12,<16", "requests"]\n'
                "[project.optional-dependencies]\n"
                'dev = ["websockets==99"]\n'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    constraints = {dep.name: dep.constraint for dep in manifest.dependencies}
    # the FIRST declaration's constraint wins, matching the name dedup
    assert constraints == {"websockets": ">=12,<16", "requests": ""}


def test_cargo_dependencies_carry_their_declared_constraints(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "crate-versions",
        {
            "Cargo.toml": (
                '[dependencies]\nserde = "1"\n'
                'renamed = { package = "real-name", version = "^2.1" }\n'
                'local = { path = "../local" }\n'
                "odd = 7\n"
            )
        },
    )
    manifest = read_repo_manifest(repo)
    constraints = {dep.name: dep.constraint for dep in manifest.dependencies}
    assert constraints == {"serde": "1", "real-name": "^2.1", "local": "", "odd": ""}


def test_package_json_dependencies_carry_their_declared_ranges(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "js-versions",
        {
            "package.json": (
                '{"dependencies": {"react": "^18.2.0", "odd": 7},'
                ' "devDependencies": {"vite": "~5.1"}}'
            )
        },
    )
    manifest = read_repo_manifest(repo)
    constraints = {dep.name: dep.constraint for dep in manifest.dependencies}
    assert constraints == {"react": "^18.2.0", "odd": "", "vite": "~5.1"}


def test_go_mod_requirements_carry_their_versions(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        "go-versions",
        {
            "go.mod": (
                "module example.com/v\n\n"
                "require (\n"
                "\tgithub.com/gorilla/websocket v1.5.0\n"
                "\texample.com/bare-path\n"
                ")\n\n"
                "require example.com/org/extra v1.0.0\n"
            )
        },
    )
    manifest = read_repo_manifest(repo)
    constraints = {dep.name: dep.constraint for dep in manifest.dependencies}
    assert constraints == {
        "github.com/gorilla/websocket": "v1.5.0",
        "example.com/bare-path": "",
        "example.com/org/extra": "v1.0.0",
    }


def test_discover_repositories_is_sorted_and_selective(tmp_path: Path) -> None:
    _repo(tmp_path, "zeta", {"pyproject.toml": '[project]\nname = "z"\n'})
    _repo(tmp_path, "alpha", {"go.mod": "module a\n"})
    owned = tmp_path / "owned"
    owned.mkdir()
    (owned / "CODEOWNERS").write_text("* @a\n", encoding="utf-8")
    gitted = tmp_path / "gitted"
    (gitted / ".git").mkdir(parents=True)
    (tmp_path / "not-a-repo").mkdir()
    (tmp_path / "loose-file.txt").write_text("x", encoding="utf-8")
    names = [path.name for path in discover_repositories(tmp_path)]
    assert names == ["alpha", "gitted", "owned", "zeta"]
