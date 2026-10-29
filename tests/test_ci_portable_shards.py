# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual portable pytest workers and platform-bound artifact journeys
"""Exercise public portable execution and complete aggregation with real subprocesses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.ci_portable_shards import pack, verify

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/ci_portable_shards.py"
SOURCE = "b" * 40
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"


def _run(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT) + os.pathsep + str(root),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        GITHUB_SHA=SOURCE,
        GITHUB_RUN_ID="portable-runner-fixture",
        GITHUB_RUN_ATTEMPT="2",
    )
    return subprocess.run(
        [sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, timeout=20
    )


def _project(root: Path, history: bool = False) -> None:
    root.mkdir()
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = ""\n')
    for index in range(4):
        (root / f"test_{index}.py").write_text(
            "from pathlib import Path\nimport pytest\n"
            "@pytest.mark.parametrize('case', [0, 1])\n"
            "def test_native(case):\n"
            f"    with Path('executed').open('a') as output:\n"
            f"        output.write('{index}-' + str(case) + '\\n')\n"
        )
    weights = {f"test_{index}.py": float(10 - index) for index in range(4)} if history else {}
    # A removed historical module must never enter current discovery.
    weights["test_removed.py"] = 999.0
    (root / "timings.json").write_text(
        json.dumps({"schema": 1, "profiles": {PYTHON: weights}}) + "\n"
    )


def _worker(root: Path, index: int) -> subprocess.CompletedProcess[str]:
    return _run(
        root,
        [
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "tools.pytest_shard",
            "--shard-index=" + str(index),
            "--shard-count=4",
            "--shard-output=main-" + str(index) + ".json",
            "--shard-timings=timings.json",
        ],
    )


def _publish(root: Path, index: int) -> subprocess.CompletedProcess[str]:
    return _run(
        root,
        [
            str(TOOL),
            "pack",
            "--manifest",
            f"main-{index}.json",
            "--timings",
            "timings.json",
            "--output",
            f"artifacts/{index}",
        ],
    )


def _verify(root: Path) -> subprocess.CompletedProcess[str]:
    return _run(
        root,
        [
            str(TOOL),
            "verify",
            "--root",
            "artifacts",
            "--source",
            SOURCE,
            "--run-id",
            "portable-runner-fixture",
            "--attempt",
            "2",
            "--platform",
            sys.platform,
            "--python",
            PYTHON,
            "--count",
            "4",
            "--config",
            "pyproject.toml",
            "--timings",
            "timings.json",
            "--output",
            "complete",
        ],
    )


@pytest.fixture(scope="module")
def actual_portable_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("portable-workers") / "project"
    _project(root, history=True)
    for index in range(4):
        result = _worker(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
        result = _publish(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
    return root


@pytest.mark.parametrize("history", [False, True])
def test_real_complete_workers_include_new_modules_once(tmp_path: Path, history: bool) -> None:
    root = tmp_path / "project"
    _project(root, history)
    for index in range(4):
        result = _worker(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
        result = _publish(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
    assert sorted((root / "executed").read_text().splitlines()) == [
        f"{module}-{case}" for module in range(4) for case in range(2)
    ]
    result = _verify(root)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads((root / "complete/timings.json").read_text())["profiles"][PYTHON]
    assert set(observed) == {f"test_{index}.py" for index in range(4)}
    assert all(seconds >= 0 for seconds in observed.values())
    assert not (root / "complete/coverage.json").exists()


def test_public_python_api_uses_actual_platform_evidence(
    actual_portable_artifacts: Path, tmp_path: Path
) -> None:
    root = tmp_path / "project"
    shutil.copytree(actual_portable_artifacts, root)
    pack(root / "main-0.json", root / "timings.json", root / "second-publication")
    verify(
        root / "artifacts",
        SOURCE,
        "portable-runner-fixture",
        "2",
        sys.platform,
        PYTHON,
        4,
        root / "pyproject.toml",
        root / "timings.json",
        root / "complete",
    )
    assert (root / "complete/timings.json").is_file()
    record = json.loads((root / "main-0.json").read_text())
    assert record["platform"] == sys.platform


def test_actual_existing_platform_skip_keeps_its_original_meaning(tmp_path: Path) -> None:
    root = tmp_path / "project"
    _project(root)
    with (root / "test_0.py").open("a") as stream:
        stream.write(
            "import sys\n"
            "@pytest.mark.skipif(sys.platform != 'win32', reason='NT-only fixture')\n"
            "def test_nt_only():\n    assert sys.platform == 'win32'\n"
        )
    for index in range(4):
        result = _worker(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
        result = _publish(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
    result = _verify(root)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((root / "main-0.json").read_text())
    assert ("test_0.py::test_nt_only" in record["skipped"]) == (sys.platform != "win32")


@pytest.mark.parametrize("failure", ["assertion", "setup", "teardown", "early-success"])
def test_actual_unsuccessful_workers_cannot_publish(tmp_path: Path, failure: str) -> None:
    root = tmp_path / "project"
    _project(root)
    fixtures = {
        "assertion": "def pytest_runtest_call(item):\n    assert False\n",
        "setup": "def pytest_runtest_setup(item):\n    pytest.fail('actual setup failure')\n",
        "teardown": (
            "def pytest_runtest_teardown(item):\n    pytest.fail('actual teardown failure')\n"
        ),
        "early-success": (
            "def pytest_runtest_setup(item):\n    pytest.exit('early', returncode=0)\n"
        ),
    }
    (root / "conftest.py").write_text("import pytest\n" + fixtures[failure])
    result = _worker(root, 0)
    assert result.returncode != 0
    result = _publish(root, 0)
    assert result.returncode != 0
    assert not (root / "artifacts/0").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("index", True),
        ("index", -1),
        ("index", 4),
        ("count", 0),
        ("count", 129),
        ("count", "4"),
        ("inventory", []),
        ("inventory", ["", ""]),
        ("inventory", "tests"),
        ("selected", []),
        ("selected", ["wrong::test"]),
        ("completed", []),
        ("called", []),
        ("called", ["wrong::test"]),
        ("skipped", ["wrong::test"]),
        ("schema", 2),
        ("schema", True),
        ("platform", "foreign-platform"),
        ("success", False),
        ("exitstatus", 1),
        ("schedule", None),
        ("module_durations", {}),
        ("module_durations", {"test_0.py": -1}),
    ],
)
def test_public_pack_rejects_corrupt_or_incomplete_actual_receipts(
    actual_portable_artifacts: Path, tmp_path: Path, field: str, value: object
) -> None:
    root = tmp_path / "project"
    shutil.copytree(actual_portable_artifacts, root)
    manifest = root / "main-0.json"
    record = json.loads(manifest.read_text())
    record[field] = value
    manifest.write_text(json.dumps(record))
    result = _publish(root, 0)
    assert result.returncode != 0


@pytest.mark.parametrize("kind", ["absent", "symlink", "array", "invalid-json"])
def test_public_pack_requires_an_ordinary_json_receipt(
    actual_portable_artifacts: Path, tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "project"
    shutil.copytree(actual_portable_artifacts, root)
    manifest = root / "main-0.json"
    manifest.unlink()
    if kind == "symlink":
        manifest.symlink_to(root / "main-1.json")
    elif kind == "array":
        manifest.write_text("[]")
    elif kind == "invalid-json":
        manifest.write_text("{")
    result = _publish(root, 0)
    assert result.returncode != 0


def _damage(root: Path, kind: str) -> None:
    path = root / "artifacts/0/artifact.json"
    artifact: dict[str, Any] = json.loads(path.read_text())
    record = artifact["main"]
    if kind in {"source", "run_id", "attempt", "python", "platform", "config_sha256"}:
        record[kind] = "foreign"
    elif kind in {"schema", "profile", "main"}:
        artifact[kind] = None
    elif kind == "extra-profile-field":
        artifact["native"] = None
    elif kind == "missing":
        path.unlink()
    elif kind == "extra":
        shutil.copytree(root / "artifacts/0", root / "artifacts/extra")
    elif kind == "duplicate-index":
        record["index"] = 1
    elif kind == "count":
        record["count"] = 3
    elif kind == "inventory":
        record["inventory"] = record["inventory"][:-1]
    elif kind == "union":
        record["selected"] = []
    elif kind == "failed":
        record["success"] = False
    elif kind == "different-timings":
        (root / "timings.json").write_text('{"schema": 1, "profiles": {}}')
    else:
        (root / "timings.json").write_text("[]")
    if path.exists():
        path.write_text(json.dumps(artifact))


@pytest.mark.parametrize(
    "kind",
    [
        "source",
        "run_id",
        "attempt",
        "python",
        "platform",
        "config_sha256",
        "schema",
        "profile",
        "main",
        "extra-profile-field",
        "missing",
        "extra",
        "duplicate-index",
        "count",
        "inventory",
        "union",
        "failed",
        "different-timings",
        "malformed-timings",
    ],
)
def test_public_aggregate_refuses_foreign_missing_or_incomplete_artifacts(
    actual_portable_artifacts: Path, tmp_path: Path, kind: str
) -> None:
    root = tmp_path / "project"
    shutil.copytree(actual_portable_artifacts, root)
    _damage(root, kind)
    result = _verify(root)
    assert result.returncode != 0
    assert not (root / "complete").exists()


@pytest.mark.parametrize("count", [0, 129])
def test_public_aggregate_refuses_an_invalid_expected_population(
    actual_portable_artifacts: Path, count: int, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="expected portable shard count"):
        verify(
            actual_portable_artifacts / "artifacts",
            SOURCE,
            "portable-runner-fixture",
            "2",
            sys.platform,
            PYTHON,
            count,
            actual_portable_artifacts / "pyproject.toml",
            actual_portable_artifacts / "timings.json",
            tmp_path / "complete",
        )


def test_a_portable_artifact_never_satisfies_the_linux_native_gate(
    actual_portable_artifacts: Path,
) -> None:
    result = _run(
        actual_portable_artifacts,
        [
            str(ROOT / "tools/ci_shard_artifacts.py"),
            "verify",
            "--root",
            "artifacts",
            "--source",
            SOURCE,
            "--run-id",
            "portable-runner-fixture",
            "--attempt",
            "2",
            "--python",
            PYTHON,
            "--count",
            "4",
            "--coverage-config",
            "pyproject.toml",
            "--output",
            "refused-linux",
        ],
    )
    assert result.returncode != 0
    assert "mandatory native" in result.stderr


@pytest.mark.parametrize("timing_result", ["success", "failure", "skipped", "cancelled", ""])
@pytest.mark.parametrize("shard_result", ["success", "failure", "skipped", "cancelled", ""])
def test_actual_macos_aggregate_shell_requires_every_job(
    timing_result: str, shard_result: str
) -> None:
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/cross-os.yml").read_text())
    job = workflow["jobs"]["macos-complete"]
    assert job["if"] == "always()"
    assert job["name"] == "cross-os-filesystem (macos-latest)"
    gate = job["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", gate],
        env=dict(os.environ, TIMING_RESULT=timing_result, SHARD_RESULT=shard_result),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) == (timing_result == shard_result == "success")
