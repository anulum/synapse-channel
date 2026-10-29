# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — tests for the generated capability guarantee register
"""The guarantee register counts a stage only with evidence it can check.

The shipped register must be current. Planted registers in a temporary tree
show every refusal, above all the two the register exists for: a receipt for
one platform cannot qualify another, and a source-commit receipt cannot
qualify a released wheel.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import guarantee_register as register

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40
DIGEST = "b" * 64


def _tree(tmp_path: Path) -> Path:
    """Write a minimal repository the register can validate against."""
    package = tmp_path / "src" / "synapse_channel"
    (package / "core").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "core" / "__init__.py").write_text("", encoding="utf-8")
    (package / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "cli_feature.py").write_text(
        "def add(sub):\n    sub.add_parser('feature')\n    sub.add_parser(name)\n\n"
        "def run():\n    from synapse_channel import cli_runner\n",
        encoding="utf-8",
    )
    (package / "cli_runner.py").write_text("from synapse_channel import feature\n", "utf-8")
    (package / "cli_other.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
        "    from synapse_channel import feature\nelse:\n    import os\n\n"
        "def add(sub):\n    sub.add_parser('other')\n",
        encoding="utf-8",
    )
    (package / "core" / "user.py").write_text(
        "from .. import feature\nimport json\n", encoding="utf-8"
    )
    (package / "core" / "absolute.py").write_text(
        "from synapse_channel.feature import VALUE\n", encoding="utf-8"
    )
    (package / "core" / "package").mkdir()
    (package / "core" / "package" / "__init__.py").write_text(
        "from ... import feature\n", encoding="utf-8"
    )
    (package / "unrelated.py").write_text("import os\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.scripts]\n'
        'feature-tool = "synapse_channel.feature:main"\n'
        'other-tool = "synapse_channel.unrelated:main"\n'
        'missing-module = "synapse_channel.gone:main"\n',
        encoding="utf-8",
    )
    (tmp_path / "docs" / "qualification").mkdir(parents=True)
    (tmp_path / "docs" / "internal").mkdir()
    (tmp_path / "docs" / "internal" / "secret.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "feature.md").write_text("# Feature\n", encoding="utf-8")
    (tmp_path / "docs" / "notes.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "docs" / "register.md").write_text(
        f"# Register\n\n{register.PAGE_START}\n{register.PAGE_END}\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_feature.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    return tmp_path


def _receipt(root: Path, name: str, **changes: Any) -> str:
    """Write one receipt below docs/qualification and return its path."""
    receipt: dict[str, Any] = {
        "schema": register.RECEIPT_SCHEMA,
        "capability": "feature",
        "platform": "linux",
        "artefact": {"kind": "wheel", "version": "1.2.3", "sha256": DIGEST},
        "tests": ["tests/test_feature.py"],
        "result": "passed",
    }
    receipt.update(changes)
    path = f"docs/qualification/{name}.json"
    (root / path).write_text(json.dumps(receipt), encoding="utf-8")
    return path


def _capability(**changes: Any) -> dict[str, Any]:
    capability: dict[str, Any] = {
        "id": "feature",
        "title": "Feature",
        "described": ["docs/feature.md"],
        "implemented": ["src/synapse_channel/feature.py"],
        "wired": [{"kind": "cli", "name": "feature"}],
        "qualified": [],
        "released": [],
    }
    capability.update(changes)
    return capability


def _raw(*capabilities: dict[str, Any], **changes: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "schema_version": register.SCHEMA_VERSION,
        "generated_output": "docs/_generated/register.json",
        "page": "docs/register.md",
        "capability": list(capabilities) or [_capability()],
    }
    raw.update(changes)
    return raw


def test_the_shipped_register_is_current_and_reports_real_stages() -> None:
    """The committed JSON and page table match the TOML; stages are derived, not typed."""
    raw = register.load_register()
    register.check_register(raw)
    rows = {row["id"]: row for row in register.validate_register(raw)}

    assert rows["a2a-bridge"]["stage"] == "wired"
    assert rows["protected-write-driver"]["stage"] == "implemented"
    assert all(not row["qualified"] and not row["released"] for row in rows.values())


def test_the_script_checks_the_shipped_register_as_the_hook_does() -> None:
    """The hook entry ``python tools/guarantee_register.py --check`` passes on this tree."""
    result = subprocess.run(
        [sys.executable, "tools/guarantee_register.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_every_stage_is_derived_from_checked_evidence(tmp_path: Path) -> None:
    """A capability with a platform receipt and a release receipt reaches ``released``."""
    root = _tree(tmp_path)
    source = _receipt(
        root, "source", artefact={"kind": "source", "commit": COMMIT}, platform="windows"
    )
    wheel = _receipt(root, "wheel")
    raw = _raw(
        _capability(
            wired=[
                {"kind": "cli", "name": "feature"},
                {"kind": "console_script", "name": "feature-tool"},
                {"kind": "imported_by", "name": "src/synapse_channel/core/user.py"},
                {"kind": "imported_by", "name": "src/synapse_channel/core/absolute.py"},
                {"kind": "imported_by", "name": "src/synapse_channel/core/package/__init__.py"},
            ],
            qualified=[{"platform": "windows", "receipt": source}],
            released=[{"version": "1.2.3", "receipt": wheel}],
        ),
        _capability(id="idea", title="Idea", implemented=[], wired=[]),
        _capability(id="nothing", title="Nothing", described=[], implemented=[], wired=[]),
    )

    rows = register.validate_register(raw, root)

    assert [row["stage"] for row in rows] == ["released", "described", "none"]
    assert rows[0]["qualified"][0]["artefact"] == {"kind": "source", "commit": COMMIT}
    document = json.loads(register.render_json(raw, root))
    assert document["summary"]["released"] == 1
    assert document["summary"]["none"] == 1
    table = register.render_table(raw, root)
    assert f"windows: source {COMMIT}" in table
    assert "1.2.3 (linux)" in table
    assert "| Nothing | none | — | — | — |" in table


def test_a_receipt_for_one_platform_cannot_qualify_another(tmp_path: Path) -> None:
    """Linux test evidence cannot qualify Windows."""
    root = _tree(tmp_path)
    receipt = _receipt(root, "linux")
    raw = _raw(_capability(qualified=[{"platform": "windows", "receipt": receipt}]))

    with pytest.raises(register.RegisterError, match="a linux receipt cannot qualify windows"):
        register.validate_register(raw, root)


def test_a_source_receipt_cannot_qualify_a_release(tmp_path: Path) -> None:
    """Source main cannot stand in for a released wheel."""
    root = _tree(tmp_path)
    receipt = _receipt(root, "source", artefact={"kind": "source", "commit": COMMIT})
    qualified = [{"platform": "linux", "receipt": receipt}]
    raw = _raw(
        _capability(qualified=qualified, released=[{"version": "1.2.3", "receipt": receipt}])
    )

    with pytest.raises(register.RegisterError, match="source-commit receipt cannot qualify"):
        register.validate_register(raw, root)


def test_a_wheel_receipt_for_another_version_cannot_qualify_a_release(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    receipt = _receipt(root, "wheel")
    qualified = [{"platform": "linux", "receipt": receipt}]
    raw = _raw(
        _capability(qualified=qualified, released=[{"version": "9.9.9", "receipt": receipt}])
    )

    with pytest.raises(register.RegisterError, match="1.2.3 wheel receipt cannot qualify 9.9.9"):
        register.validate_register(raw, root)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "other"}, "is not a synapse-qualification-receipt.v1 receipt"),
        ({"capability": "other"}, "is not a synapse-qualification-receipt.v1 receipt"),
        ({"platform": "solaris"}, "platform must be one of"),
        ({"result": "failed"}, "records no passing qualification"),
        ({"tests": []}, "names no test"),
        ({"tests": ["tests/test_missing.py"]}, "does not exist"),
        ({"artefact": "wheel"}, "artefact must be a table"),
        ({"artefact": {"kind": "source", "commit": "short"}}, "40-hex commit"),
        ({"artefact": {"kind": "wheel", "version": "1.2", "sha256": DIGEST}}, "MAJOR.MINOR.PATCH"),
        ({"artefact": {"kind": "wheel", "version": "1.2.3", "sha256": "x"}}, "64-hex digest"),
        ({"artefact": {"kind": "image", "digest": DIGEST}}, "source commit or a wheel"),
        ({"extra": True}, "fields must be exactly"),
    ],
)
def test_malformed_receipts_are_refused(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    root = _tree(tmp_path)
    receipt = _receipt(root, "bad", **changes)
    raw = _raw(_capability(qualified=[{"platform": "linux", "receipt": receipt}]))

    with pytest.raises(register.RegisterError, match=message):
        register.validate_register(raw, root)


def test_a_receipt_that_is_not_json_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "qualification" / "broken.json").write_text("{", encoding="utf-8")
    receipt = "docs/qualification/broken.json"
    raw = _raw(_capability(qualified=[{"platform": "linux", "receipt": receipt}]))

    with pytest.raises(register.RegisterError, match="is not JSON"):
        register.validate_register(raw, root)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"wired": [{"kind": "cli", "name": "absent"}]}, "no CLI subcommand 'absent'"),
        ({"wired": [{"kind": "console_script", "name": "absent"}]}, "no console script"),
        (
            {"wired": [{"kind": "imported_by", "name": "src/synapse_channel/unrelated.py"}]},
            "does not reach an implementing module",
        ),
        ({"wired": [{"kind": "plugin", "name": "x"}]}, "kind is unknown"),
        ({"wired": [{"kind": "cli"}]}, "exactly kind and name"),
        ({"qualified": [{"platform": "linux"}]}, "exactly platform and receipt"),
        ({"released": [{"version": "1.2.3"}]}, "exactly version and receipt"),
        ({"described": []}, "skips a stage"),
        ({"described": ["docs/missing.md"]}, "does not exist"),
        ({"described": ["docs/internal/secret.md"]}, "public .md file"),
        ({"described": ["docs/notes.txt"]}, "public .md file"),
        ({"described": ["../outside.md"]}, "repository path below docs/"),
        ({"described": ["docs/feature.md", "docs/feature.md"]}, "contains duplicates"),
        ({"described": "docs/feature.md"}, "must be a list"),
        ({"implemented": ["tools/x.py"]}, "below src/synapse_channel/"),
        ({"id": "Not Kebab"}, "lower-case kebab case"),
        ({"title": " "}, "must be a non-empty string"),
        ({"notes": "x"}, "fields must be exactly"),
    ],
)
def test_capability_evidence_violations_are_refused(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    root = _tree(tmp_path)

    with pytest.raises(register.RegisterError, match=message):
        register.validate_register(_raw(_capability(**changes)), root)


@pytest.mark.parametrize(
    ("raw_changes", "message"),
    [
        ({"schema_version": "v0"}, "schema_version must be"),
        ({"capability": []}, "must not be empty"),
        ({"extra": 1}, "top-level fields must be exactly"),
        ({"capability": [_capability(), _capability()]}, "ids must be unique"),
        ({"capability": ["not-a-table"]}, "fields must be exactly"),
    ],
)
def test_register_level_violations_are_refused(
    tmp_path: Path, raw_changes: dict[str, Any], message: str
) -> None:
    root = _tree(tmp_path)
    raw = _raw(_capability())
    raw.update(raw_changes)

    with pytest.raises(register.RegisterError, match=message):
        register.validate_register(raw, root)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("generated_output", "/abs/register.json"),
        ("generated_output", "docs/register.md"),
        ("generated_output", "docs/internal/register.json"),
        ("page", "README.md"),
    ],
)
def test_outputs_must_be_public_docs_paths(tmp_path: Path, key: str, value: str) -> None:
    root = _tree(tmp_path)
    raw = _raw(**{key: value})

    with pytest.raises(register.RegisterError, match=f"{key} must be a"):
        register.update_register(raw, root)


def test_update_then_check_and_stale_outputs_are_refused(tmp_path: Path) -> None:
    """``--update`` writes both outputs; any later edit to either is refused by ``--check``."""
    root = _tree(tmp_path)
    raw = _raw()

    with pytest.raises(register.RegisterError, match="register.json is stale"):
        register.check_register(raw, root)
    register.update_register(raw, root)
    register.check_register(raw, root)

    json_path = root / "docs" / "_generated" / "register.json"
    json_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(register.RegisterError, match="register.json is stale"):
        register.check_register(raw, root)
    register.update_register(raw, root)

    page = root / "docs" / "register.md"
    page.write_text(page.read_text(encoding="utf-8").replace("wired", "released"), encoding="utf-8")
    with pytest.raises(register.RegisterError, match="register.md table is stale"):
        register.check_register(raw, root)


@pytest.mark.parametrize(
    "text",
    [
        "# no markers\n",
        f"{register.PAGE_END}\n{register.PAGE_START}\n",
        f"{register.PAGE_START}\n{register.PAGE_START}\n{register.PAGE_END}\n",
    ],
)
def test_the_page_needs_exactly_one_ordered_marker_pair(tmp_path: Path, text: str) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "register.md").write_text(text, encoding="utf-8")

    with pytest.raises(register.RegisterError, match="one ordered marker pair"):
        register.update_register(_raw(), root)


def test_main_reports_success_and_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command line updates, checks and reports a refusal with exit status one."""
    root = _tree(tmp_path)
    (root / "tools" / "guarantee_register.toml").write_text(
        f'schema_version = "{register.SCHEMA_VERSION}"\n'
        'generated_output = "docs/_generated/register.json"\n'
        'page = "docs/register.md"\n\n'
        "[[capability]]\n"
        'id = "feature"\ntitle = "Feature"\ndescribed = ["docs/feature.md"]\n'
        'implemented = ["src/synapse_channel/feature.py"]\n'
        'wired = [{ kind = "cli", name = "feature" }]\nqualified = []\nreleased = []\n',
        encoding="utf-8",
    )

    assert register.main(["--check"], root) == 1
    assert "is stale" in capsys.readouterr().err
    assert register.main(["--update"], root) == 0
    assert register.main(["--check"], root) == 0
    (root / "tools" / "guarantee_register.toml").write_text("not toml = [", encoding="utf-8")
    assert register.main(["--check"], root) == 1
    assert "guarantee-register:" in capsys.readouterr().err


def test_a_write_failure_leaves_no_temporary_file(tmp_path: Path) -> None:
    """An atomic write that cannot replace its target removes its temporary file."""
    target = tmp_path / "out" / "file.json"
    target.mkdir(parents=True)
    (target / "occupied").write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        register._write_atomic(target, b"data")
    assert sorted(path.name for path in target.parent.iterdir()) == ["file.json"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"wired": [{"kind": "cli", "name": "other"}]}, "does not reach an implementing module"),
        (
            {"wired": [{"kind": "console_script", "name": "other-tool"}]},
            "does not reach an implementing module",
        ),
        (
            {"wired": [{"kind": "console_script", "name": "missing-module"}]},
            "does not reach an implementing module",
        ),
        (
            {"wired": [{"kind": "imported_by", "name": "src/synapse_channel/cli_other.py"}]},
            "does not reach an implementing module",
        ),
    ],
)
def test_an_entry_point_that_does_not_reach_the_implementation_is_not_wiring(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    """An existing but unrelated command, or a type-checking-only import, wires nothing."""
    root = _tree(tmp_path)

    with pytest.raises(register.RegisterError, match=message):
        register.validate_register(_raw(_capability(**changes)), root)


def test_wiring_follows_run_time_imports_through_other_modules(tmp_path: Path) -> None:
    """``feature`` is registered in cli_feature, which reaches the code through cli_runner."""
    root = _tree(tmp_path)

    assert register.reaches(
        root, "synapse_channel.cli_feature", frozenset({"synapse_channel.feature"})
    )
    assert not register.reaches(
        root, "synapse_channel.cli_other", frozenset({"synapse_channel.feature"})
    )
    assert register.registering_modules(root, "feature") == ("synapse_channel.cli_feature",)
    assert register.console_script_module(root, "absent") is None


@pytest.mark.parametrize(
    "path",
    ["docs//internal/secret.md", "docs/./internal/secret.md", "docs/sub/../internal/secret.md"],
)
def test_aliases_of_private_paths_are_refused(tmp_path: Path, path: str) -> None:
    root = _tree(tmp_path)

    with pytest.raises(register.RegisterError, match="canonical repository path"):
        register.validate_register(_raw(_capability(described=[path])), root)


def test_a_link_into_private_documentation_is_refused(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "docs" / "linked.md").symlink_to(root / "docs" / "internal" / "secret.md")
    (root / "docs" / "outside.md").symlink_to(tmp_path.parent)

    with pytest.raises(register.RegisterError, match="must be a public .md file"):
        register.validate_register(_raw(_capability(described=["docs/linked.md"])), root)
    with pytest.raises(register.RegisterError, match="leaves the repository"):
        register.validate_register(_raw(_capability(described=["docs/outside.md"])), root)


@pytest.mark.parametrize(
    "value", ["docs//_generated/register.json", "docs/./register.md", "docs/internal/x.json"]
)
def test_output_aliases_are_refused(tmp_path: Path, value: str) -> None:
    root = _tree(tmp_path)
    key = "page" if value.endswith(".md") else "generated_output"

    with pytest.raises(register.RegisterError, match=key):
        register.update_register(_raw(**{key: value}), root)


@pytest.mark.parametrize(
    "text",
    [
        '{"result": "failed", "result": "passed"}',
        '{"platform": "windows", "platform": "linux"}',
        '{"artefact": {"kind": "wheel", "kind": "source"}}',
    ],
)
def test_a_receipt_with_repeated_members_is_refused(tmp_path: Path, text: str) -> None:
    root = _tree(tmp_path)
    path = root / "docs" / "qualification" / "ambiguous.json"
    path.write_text(text, encoding="utf-8")
    receipt = "docs/qualification/ambiguous.json"

    with pytest.raises(register.RegisterError, match="repeats a member name"):
        register.read_receipt(root, receipt, "feature")


@pytest.mark.parametrize("platform", [[], {}, 5])
def test_a_non_text_platform_is_a_refusal_not_a_crash(
    tmp_path: Path, platform: object, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _tree(tmp_path)
    receipt = _receipt(root, "odd", platform=platform)
    raw = _raw(_capability(qualified=[{"platform": "linux", "receipt": receipt}]))

    with pytest.raises(register.RegisterError, match="platform must be one of"):
        register.validate_register(raw, root)


@pytest.mark.parametrize("target", ["internal", "outside"])
def test_an_output_directory_linked_out_of_public_docs_is_refused(
    tmp_path: Path, target: str
) -> None:
    """A canonical path whose directory is a link into private docs or outside is refused."""
    root = _tree(tmp_path)
    destination = root / "docs" / "internal" if target == "internal" else tmp_path.parent
    (root / "docs" / "public-output").symlink_to(destination)

    with pytest.raises(register.RegisterError, match="generated_output must be a public"):
        register.update_register(_raw(generated_output="docs/public-output/register.json"), root)
    assert not (root / "docs" / "internal" / "register.json").exists()
