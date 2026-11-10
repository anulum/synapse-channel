# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — hostile invariant-conformance registry tests
from __future__ import annotations

import copy
import errno
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tools import invariant_conformance as conformance

# An absolute path on this platform: "/tests/..." has no drive on Windows and is
# relative there, so the root anchor of the working directory is used instead.
_ABSOLUTE_TEST_PATH = str(Path(Path.cwd().anchor, "tests", "test_absolute.py"))

# A temporary name over the 255-byte component limit is refused as ENAMETOOLONG on
# POSIX. Native Windows (Python 3.12.10 on Windows 11, long paths not enabled) refuses
# the same create as EINVAL.
_NAME_TOO_LONG = errno.EINVAL if os.name == "nt" else errno.ENAMETOOLONG


@pytest.fixture
def registry() -> dict[str, Any]:
    return conformance.load_registry()


def _scratch_root(root: Path, registry: dict[str, Any]) -> None:
    """Give ``root`` the specification and an empty file at every cited evidence path."""
    (root / "docs").mkdir()
    (root / "docs" / "coordination-spec.md").write_text(
        (conformance.REPO_ROOT / conformance.SPEC_PATH).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for row in registry["boundary"]:
        for value in [*row["sources"], *row["tests"]]:
            target = root / value
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()


def test_live_registry_is_valid_and_generated_json_is_current(registry: dict[str, Any]) -> None:
    rows = conformance.validate_registry(registry)
    conformance.check_registry(registry)
    rendered = json.loads(conformance.render_registry(registry))

    assert tuple(row["id"] for row in rows) == conformance.BOUNDARY_ORDER
    assert rendered["overall_status"] == "partial"
    assert rendered["summary"] == {
        "conformant": 2,
        "not-implemented": 0,
        "partial": 4,
        "total": 6,
    }


def test_enforcement_fails_closed_while_any_boundary_is_partial(registry: dict[str, Any]) -> None:
    with pytest.raises(conformance.RegistryError, match="not fully conformant"):
        conformance.enforce_registry(registry)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw["boundary"].append(copy.deepcopy(raw["boundary"][0])), "exactly once"),
        (lambda raw: raw["boundary"][1].update(status="unknown"), "status is unknown"),
        (lambda raw: raw["boundary"][1].update(limitations=[]), "must state limitations"),
        (
            lambda raw: raw["boundary"][0]["sources"].append("docs/internal/private.md"),
            "cannot cite private evidence",
        ),
        (lambda raw: raw["boundary"][0]["spec_invariants"].append("INV-NO-999"), "unknown spec"),
        (lambda raw: raw["boundary"][0].update(extra="not allowed"), "fields differ"),
        (lambda raw: raw.update(extra="not allowed"), "top-level fields differ"),
        (
            lambda raw: raw["boundary"][0]["sources"].append("README.md"),
            "source evidence must be",
        ),
    ],
)
def test_registry_schema_mutations_fail_closed(
    registry: dict[str, Any], mutation: Callable[[dict[str, Any]], object], message: str
) -> None:
    mutated = copy.deepcopy(registry)
    mutation(mutated)

    with pytest.raises(conformance.RegistryError, match=message):
        conformance.validate_registry(mutated)


@pytest.mark.parametrize("value", [None, "", "   ", 1])
def test_required_text_rejects_empty_or_non_text(value: object) -> None:
    with pytest.raises(conformance.RegistryError, match="non-empty string"):
        conformance._require_text(value, "field")


def test_required_text_list_rejects_wrong_empty_and_duplicate_values() -> None:
    with pytest.raises(conformance.RegistryError, match="non-empty list"):
        conformance._require_text_list([], "field")
    with pytest.raises(conformance.RegistryError, match="non-empty list"):
        conformance._require_text_list("value", "field")
    with pytest.raises(conformance.RegistryError, match="contains duplicates"):
        conformance._require_text_list(["same", "same"], "field")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.update(schema_version="unknown"), "schema_version"),
        (lambda raw: raw.update(generated_output="docs/internal/result.json"), "generated_output"),
        (lambda raw: raw.update(boundary="not-a-list"), "boundary must be a list"),
        (
            lambda raw: raw["boundary"][0].update(limitations=["contradiction"]),
            "cannot be conformant",
        ),
        (
            lambda raw: raw["boundary"][0]["tests"].append("README.md"),
            "test evidence must be",
        ),
        (
            lambda raw: raw["boundary"][0]["sources"].append("src/synapse_channel/not_present.py"),
            "does not exist",
        ),
        (
            lambda raw: raw["boundary"][0]["sources"].append(
                "src/synapse_channel/../../outside.py"
            ),
            "repository-relative",
        ),
        (
            lambda raw: raw["boundary"][0]["tests"].append(_ABSOLUTE_TEST_PATH),
            "repository-relative",
        ),
        (
            lambda raw: raw["boundary"].__setitem__(2, "not a table"),
            r"boundary\[2\] must be a table",
        ),
    ],
)
def test_registry_shape_and_evidence_fail_closed(
    registry: dict[str, Any], mutation: Callable[[dict[str, Any]], object], message: str
) -> None:
    mutated = copy.deepcopy(registry)
    mutation(mutated)

    with pytest.raises(conformance.RegistryError, match=message):
        conformance.validate_registry(mutated)


def test_output_path_rejects_escape(registry: dict[str, Any]) -> None:
    mutated = copy.deepcopy(registry)
    mutated["generated_output"] = "../result.json"

    with pytest.raises(conformance.RegistryError, match="repository-relative"):
        conformance.output_path(mutated)


def test_check_rejects_stale_generated_json(tmp_path: Path, registry: dict[str, Any]) -> None:
    root = tmp_path
    _scratch_root(root, registry)
    output = root / registry["generated_output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("{}\n", encoding="utf-8")

    with pytest.raises(conformance.RegistryError, match="is stale"):
        conformance.check_registry(registry, root)


def test_update_is_atomic_and_makes_check_pass(tmp_path: Path, registry: dict[str, Any]) -> None:
    root = tmp_path
    _scratch_root(root, registry)

    conformance.update_registry(registry, root)
    conformance.check_registry(registry, root)

    target = root / registry["generated_output"]
    assert target.is_file()
    assert not list(target.parent.glob(f".{target.name}.*.tmp"))


def test_enforcement_passes_when_all_boundaries_conform(
    tmp_path: Path, registry: dict[str, Any]
) -> None:
    root = tmp_path
    complete = copy.deepcopy(registry)
    for row in complete["boundary"]:
        row["status"] = "conformant"
        row["limitations"] = []
    _scratch_root(root, complete)

    conformance.update_registry(complete, root)
    conformance.enforce_registry(complete, root)


@pytest.mark.parametrize("mode", ["--update", "--check", "--enforce"])
def test_main_routes_each_mode(
    monkeypatch: pytest.MonkeyPatch, registry: dict[str, Any], mode: str
) -> None:
    called: list[str] = []
    monkeypatch.setattr(conformance, "load_registry", lambda: registry)
    monkeypatch.setattr(conformance, "update_registry", lambda raw: called.append("--update"))
    monkeypatch.setattr(conformance, "check_registry", lambda raw: called.append("--check"))
    monkeypatch.setattr(conformance, "enforce_registry", lambda raw: called.append("--enforce"))

    assert conformance.main([mode]) == 0
    assert called == [mode]


def test_main_reports_registry_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail() -> dict[str, Any]:
        raise conformance.RegistryError("bad registry")

    monkeypatch.setattr(conformance, "load_registry", fail)

    assert conformance.main(["--check"]) == 1
    assert "invariant-conformance: bad registry" in capsys.readouterr().err


def test_evidence_that_resolves_outside_the_repository_fails_closed(
    tmp_path: Path, registry: dict[str, Any]
) -> None:
    """A cited path that is a symlink out of the repository is refused, not followed."""
    root = tmp_path / "repo"
    root.mkdir()
    _scratch_root(root, registry)
    outside = tmp_path / "outside.py"
    outside.write_text("", encoding="utf-8")
    cited = root / registry["boundary"][0]["sources"][0]
    cited.unlink()
    cited.symlink_to(outside)

    with pytest.raises(conformance.RegistryError, match="escapes the repository"):
        conformance.validate_registry(registry, root)


def test_update_leaves_nothing_when_its_temporary_file_cannot_be_created(
    tmp_path: Path, registry: dict[str, Any]
) -> None:
    """A generated name too long for its temporary sibling fails before any byte is written."""
    _scratch_root(tmp_path, registry)
    long_output = copy.deepcopy(registry)
    long_output["generated_output"] = "docs/generated/" + "c" * 250 + ".json"
    target = conformance.output_path(long_output, tmp_path)

    with pytest.raises(OSError) as raised:
        conformance.update_registry(long_output, tmp_path)

    assert raised.value.errno == _NAME_TOO_LONG
    assert list(target.parent.iterdir()) == []


def test_the_script_entry_point_checks_the_checkout() -> None:
    """The pre-commit hook's command, run as a script, finds the generated registry current."""
    script = conformance.REPO_ROOT / "tools" / "invariant_conformance.py"

    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
