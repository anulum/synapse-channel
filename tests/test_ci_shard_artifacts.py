# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual pytest shard execution and complete coverage artifact journeys
"""Exercise real collection, failures, indexed artifacts and coverage through public CLIs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/ci_shard_artifacts.py"
SOURCE = "a" * 40
PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
NATIVE = "tests/fixtures/opencode/metadata_denial_linux.py"


def _run(
    root: Path, arguments: list[str], *, coverage_file: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the actual public command without relying on inherited collection plugins."""
    env = dict(os.environ)
    if coverage_file is not None:
        env = {key: value for key, value in env.items() if not key.startswith("COVERAGE")}
    env.update(
        PYTHONPATH=str(ROOT) + os.pathsep + str(root),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        GITHUB_SHA=SOURCE,
        GITHUB_RUN_ID="actual-runner-fixture",
        GITHUB_RUN_ATTEMPT="1",
    )
    if coverage_file is not None:
        env["COVERAGE_FILE"] = str(coverage_file)
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=root,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def _prepare(root: Path, *, both_branches: bool = True, failure: str = "") -> Path:
    """Create a real two-test project whose disjoint shards exercise opposite branches."""
    root.mkdir(parents=True)
    config = root / "pyproject.toml"
    config.write_text(
        '[tool.pytest.ini_options]\naddopts = ""\n'
        '[tool.coverage.run]\nbranch = true\nsource = ["app"]\n'
        "[tool.coverage.report]\nfail_under = 0\n"
    )
    (root / "app.py").write_text("def value(flag):\n    if flag:\n        return 1\n    return 0\n")
    first = {
        "": "assert value(False) == 0",
        "test": "assert value(False) == 99",
        "setup": "pytest.fail('actual setup failure')",
        "early-success": "pytest.exit('actual premature exit', returncode=0)",
    }[failure]
    if failure == "setup":
        setup = "@pytest.fixture(autouse=True)\ndef broken():\n    " + first + "\n"
        first = "assert value(False) == 0"
    else:
        setup = ""
    (root / "test_project.py").write_text(
        "import pytest\nfrom pathlib import Path\nfrom app import value\n"
        + setup
        + "def test_a():\n    "
        + first
        + "\n"
        + "    with Path('executed').open('a') as output:\n        output.write('a\\n')\n"
        + f"def test_b():\n    assert value({both_branches}) == {int(both_branches)}\n"
        + "    with Path('executed').open('a') as output:\n        output.write('b\\n')\n"
    )
    native = root / NATIVE
    native.parent.mkdir(parents=True)
    native.write_text("def test_native_followup():\n    assert 2 + 2 == 4\n")
    return config


def _pytest(root: Path, index: int, *, native: bool = False) -> subprocess.CompletedProcess[str]:
    """Use pytest's real coverage plugin, native collection and indexed recorder."""
    args = [
        "-m",
        "pytest",
        NATIVE if native else "test_project.py",
        "-q",
        "-p",
        "tools.pytest_shard",
        "-p",
        "pytest_cov.plugin",
        f"--shard-index={index}",
        f"--shard-count={1 if native else 2}",
        "--shard-output=" + ("native.json" if native else f"main-{index}.json"),
        "--cov=app",
        "--cov-config=pyproject.toml",
        "--cov-report=",
        "--cov-fail-under=0",
        "-o",
        "cache_dir=" + str(root / "cache"),
    ]
    if native:
        args.append("--cov-append")
    return _run(root, args, coverage_file=root / f"data-{index}")


def _artifacts(root: Path, *, both_branches: bool = True) -> Path:
    """Build two real independently measured artifacts and the required native follow-up."""
    config = _prepare(root, both_branches=both_branches)
    for index in range(2):
        result = _pytest(root, index)
        assert result.returncode == 0, result.stdout + result.stderr
        if index == 0:
            result = _pytest(root, 0, native=True)
            assert result.returncode == 0, result.stdout + result.stderr
        result = _run(
            root,
            [
                str(TOOL),
                "pack",
                "--manifest",
                f"main-{index}.json",
                "--coverage-file",
                f"data-{index}",
                "--output",
                f"artifacts/{index}",
                *(["--native-manifest", "native.json"] if index == 0 else []),
            ],
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return config


def _verify(root: Path, *, coverage_python: str = PYTHON) -> subprocess.CompletedProcess[str]:
    """Run the public complete-union gate at the unchanged production threshold."""
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
            "actual-runner-fixture",
            "--attempt",
            "1",
            "--python",
            PYTHON,
            "--count",
            "2",
            "--coverage-python",
            coverage_python,
            "--coverage-config",
            "pyproject.toml",
            "--fail-under",
            "98",
            "--output",
            "reports",
        ],
    )


@pytest.fixture(scope="module")
def actual_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Retain real runner evidence for isolated public-consumer corruption checks."""
    root = tmp_path_factory.mktemp("actual-shard-fixture") / "project"
    _artifacts(root)
    return root


def test_complete_real_shards_gate_the_genuine_coverage_union(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """Each branch is measured by a different shard; the complete union reaches 100 percent."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    result = _verify(root)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((root / "reports/coverage.json").read_text())
    assert report["totals"]["percent_covered"] == 100
    assert report["totals"]["num_branches"] == 2
    assert (root / "reports/coverage.xml").is_file()


@pytest.mark.parametrize("failure", ["test", "setup", "early-success"])
def test_real_failed_or_premature_pytest_cannot_publish(tmp_path: Path, failure: str) -> None:
    """Actual assertion, setup and early zero-exit paths cannot mint successful shard evidence."""
    root = tmp_path / "project"
    _prepare(root, failure=failure)
    result = _run(
        root,
        [
            "-m",
            "pytest",
            "test_project.py",
            "-p",
            "tools.pytest_shard",
            "--shard-index=0",
            "--shard-count=2",
            "--shard-output=main-0.json",
        ],
    )
    assert result.returncode != 0
    record = json.loads((root / "main-0.json").read_text())
    assert record["success"] is False
    result = _run(root, [str(TOOL), "pack", "--manifest", "main-0.json", "--output", "refused"])
    assert result.returncode == 1 and "incomplete" in result.stderr
    assert not (root / "refused").exists()


def test_complete_union_still_refuses_insufficient_real_coverage(tmp_path: Path) -> None:
    """Successful tests cannot substitute for missing production branch observations."""
    root = tmp_path / "project"
    _artifacts(root, both_branches=False)
    result = _verify(root)
    assert result.returncode == 1
    assert "is below 98%" in result.stderr


def _damage(root: Path, kind: str) -> None:
    """Corrupt a copied real downloaded artifact to exercise a specific public refusal."""
    directory = root / "artifacts/0"
    path = directory / "artifact.json"
    record = json.loads(path.read_text())
    if kind == "missing":
        shutil.rmtree(directory)
    elif kind == "extra":
        shutil.copytree(directory, root / "artifacts/extra")
    elif kind == "duplicate-index":
        (root / "artifacts/1/artifact.json").write_bytes(path.read_bytes())
    elif kind in {"source", "run_id", "attempt", "python", "config_sha256"}:
        record["main"][kind] = "foreign-value"
        record["native"][kind] = "foreign-value"
    elif kind == "inventory":
        record["main"]["inventory"].append("zz_unobserved.py::test_foreign")
        record["main"]["selected"] = record["main"]["inventory"][::2]
        record["main"]["completed"] = record["main"]["selected"]
    elif kind == "selection":
        record["main"]["selected"] = record["main"]["inventory"][1::2]
    elif kind == "completed":
        record["main"]["completed"] = []
    elif kind == "failed":
        record["main"]["success"] = False
    elif kind == "native-missing":
        record["native"] = None
    elif kind == "native-skipped":
        record["native"]["skipped"] = record["native"]["selected"]
        record["native"]["called"] = []
    elif kind == "native-foreign":
        record["native"]["source"] = "foreign-native-source"
    elif kind == "native-malformed":
        record["native"] = "invalid receipt"
    elif kind == "schema":
        record["schema"] = 99
    elif kind == "duplicate-ids":
        record["main"]["inventory"] *= 2
    elif kind == "coverage-missing":
        (directory / "coverage.data").unlink()
    elif kind == "coverage-altered":
        with (directory / "coverage.data").open("ab") as output:
            output.write(b"altered after publication")
    else:
        raise AssertionError(kind)
    if kind not in {"missing", "extra", "duplicate-index"}:
        path.write_text(json.dumps(record))


@pytest.mark.parametrize(
    ("kind", "diagnostic"),
    [
        ("missing", "missing or extra"),
        ("extra", "missing or extra"),
        ("duplicate-index", "missing or duplicate indexed"),
        ("source", "does not match"),
        ("run_id", "does not match"),
        ("attempt", "does not match"),
        ("python", "does not match"),
        ("config_sha256", "does not match"),
        ("inventory", "complete collection differs"),
        ("selection", "incomplete or misidentified"),
        ("completed", "incomplete or misidentified"),
        ("failed", "incomplete or misidentified"),
        ("native-missing", "missing mandatory native"),
        ("native-skipped", "did not execute completely"),
        ("native-foreign", "different source"),
        ("native-malformed", "malformed native"),
        ("schema", "invalid artifact schema"),
        ("duplicate-ids", "invalid inventory test inventory"),
        ("coverage-missing", "missing or altered coverage"),
        ("coverage-altered", "missing or altered coverage"),
    ],
)
def test_public_union_refuses_missing_failed_or_foreign_artifacts(
    actual_artifacts: Path, tmp_path: Path, kind: str, diagnostic: str
) -> None:
    """A successful shard cannot hide omitted, misidentified or altered counterpart evidence."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    _damage(root, kind)
    result = _verify(root)
    assert result.returncode == 1 and diagnostic in result.stderr
    assert not (root / "reports").exists()


@pytest.mark.parametrize(
    "options",
    [
        ["--shard-count=0", "--shard-index=0", "--shard-output=bad.json"],
        ["--shard-count=129", "--shard-index=0", "--shard-output=bad.json"],
        ["--shard-count=2", "--shard-index=-1", "--shard-output=bad.json"],
        ["--shard-count=2", "--shard-index=2", "--shard-output=bad.json"],
        ["--shard-count=2", "--shard-index=0"],
        ["--shard-count=2", "--shard-output=bad.json"],
    ],
)
def test_native_pytest_refuses_invalid_or_partial_shard_options(
    tmp_path: Path, options: list[str]
) -> None:
    """Malformed shard configuration never runs an accidentally narrowed suite."""
    root = tmp_path / "project"
    _prepare(root)
    result = _run(root, ["-m", "pytest", "test_project.py", "-p", "tools.pytest_shard", *options])
    assert result.returncode != 0 and "shards require" in result.stderr
    assert not (root / "bad.json").exists()


@pytest.mark.parametrize("duplicate", [False, True])
def test_native_pytest_refuses_empty_or_duplicated_inventory(
    tmp_path: Path, duplicate: bool
) -> None:
    """Real collection cannot silently omit an empty index or run a duplicate node twice."""
    root = tmp_path / "project"
    _prepare(root)
    paths = (
        ["test_project.py", "test_project.py", "--keep-duplicates"]
        if duplicate
        else ["test_project.py"]
    )
    options = (
        ["--shard-index=0", "--shard-count=2"]
        if duplicate
        else ["--shard-index=2", "--shard-count=3"]
    )
    result = _run(
        root,
        ["-m", "pytest", *paths, "-p", "tools.pytest_shard", *options, "--shard-output=bad.json"],
    )
    assert result.returncode != 0
    assert ("duplicate node IDs" if duplicate else "no collected tests") in result.stderr
    assert json.loads((root / "bad.json").read_text())["success"] is False


def test_loading_the_plugin_without_a_shard_profile_preserves_native_pytest(tmp_path: Path) -> None:
    """An ordinary caller can load the module without changing the full collected suite."""
    root = tmp_path / "project"
    _prepare(root)
    result = _run(root, ["-m", "pytest", "test_project.py", "-q", "-p", "tools.pytest_shard"])
    assert result.returncode == 0 and "2 passed" in result.stdout


def test_disjoint_native_shards_execute_each_collected_test_once(tmp_path: Path) -> None:
    """Independent real runners leave exactly one external observation per admitted test."""
    root = tmp_path / "project"
    _prepare(root)
    for index in range(2):
        result = _run(
            root,
            [
                "-m",
                "pytest",
                "test_project.py",
                "-p",
                "tools.pytest_shard",
                f"--shard-index={index}",
                "--shard-count=2",
                f"--shard-output=part-{index}.json",
            ],
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "executed").read_text().splitlines() == ["a", "b"]
    receipts = [json.loads((root / f"part-{index}.json").read_text()) for index in range(2)]
    assert receipts[0]["inventory"] == receipts[1]["inventory"]
    selected = [node for record in receipts for node in record["selected"]]
    assert sorted(selected) == receipts[0]["inventory"] and len(selected) == len(set(selected))


def test_artifact_without_coverage_cannot_complete_the_coverage_interpreter(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """Compatibility-only publication is valid, but cannot substitute for required measured data."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    shutil.rmtree(root / "artifacts/0")
    result = _run(
        root,
        [
            str(TOOL),
            "pack",
            "--manifest",
            "main-0.json",
            "--native-manifest",
            "native.json",
            "--output",
            "artifacts/0",
        ],
    )
    assert result.returncode == 0, result.stderr
    result = _verify(root)
    assert result.returncode == 1 and "missing or altered coverage" in result.stderr


def test_a_real_skipped_native_followup_cannot_publish(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """A real pytest skip must never replace the mandatory native observation."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    (root / NATIVE).write_text(
        "import pytest\ndef test_native_followup():\n"
        "    pytest.importorskip('synapse_absent_negative_native_capability_01a1824d')\n"
        "    assert False\n"
    )
    result = _run(
        root,
        [
            "-m",
            "pytest",
            NATIVE,
            "-p",
            "tools.pytest_shard",
            "--shard-index=0",
            "--shard-count=1",
            "--shard-output=skipped-native.json",
        ],
    )
    assert result.returncode == 0 and "1 skipped" in result.stdout
    result = _run(
        root,
        [
            str(TOOL),
            "pack",
            "--manifest",
            "main-0.json",
            "--native-manifest",
            "skipped-native.json",
            "--output",
            "refused",
        ],
    )
    assert result.returncode == 1 and "did not execute completely" in result.stderr
    assert not (root / "refused").exists()


@pytest.mark.parametrize(
    ("kind", "diagnostic"),
    [
        ("missing", "missing ordinary artifact"),
        ("array", "JSON object"),
        ("coverage", "missing ordinary accumulated coverage"),
        ("native-index", "belongs only to shard zero"),
    ],
)
def test_public_pack_refuses_unusable_inputs_before_publication(
    actual_artifacts: Path, tmp_path: Path, kind: str, diagnostic: str
) -> None:
    """Ordinary-file, schema, native ownership and actual data prerequisites fail before output."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    index = 1 if kind == "native-index" else 0
    manifest = root / f"main-{index}.json"
    if kind == "missing":
        manifest.unlink()
    elif kind == "array":
        manifest.write_text("[]")
    args = [
        str(TOOL),
        "pack",
        "--manifest",
        manifest.name,
        "--native-manifest",
        "native.json",
        "--output",
        "refused",
    ]
    if kind == "coverage":
        args += ["--coverage-file", "missing-coverage.data"]
    result = _run(root, args)
    assert result.returncode == 1 and diagnostic in result.stderr
    assert not (root / "refused").exists()


@pytest.mark.parametrize("have_data", [False, True])
def test_declared_coverage_interpreter_must_match_the_actual_profile(
    actual_artifacts: Path, tmp_path: Path, have_data: bool
) -> None:
    """Compatibility-only or measured artifacts cannot complete a foreign coverage profile."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    if not have_data:
        shutil.rmtree(root / "artifacts")
        for index in range(2):
            result = _run(
                root,
                [
                    str(TOOL),
                    "pack",
                    "--manifest",
                    f"main-{index}.json",
                    "--output",
                    f"artifacts/{index}",
                    *(["--native-manifest", "native.json"] if index == 0 else []),
                ],
            )
            assert result.returncode == 0, result.stderr
    result = _verify(root, coverage_python="3.99")
    diagnostic = "unexpected coverage interpreter" if have_data else "invalid coverage profile"
    assert result.returncode == 1 and diagnostic in result.stderr
    assert not (root / "reports").exists()


def test_public_python_pack_api_retains_an_actual_successful_shard(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """The documented Python entry produces the same genuine indexed artifact as the CLI."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    result = _run(
        root,
        [
            "-c",
            "from pathlib import Path; from tools.ci_shard_artifacts import pack; "
            "pack(Path('main-1.json'), None, Path('data-1'), Path('api-artifact'))",
        ],
    )
    assert result.returncode == 0, result.stderr
    assert (root / "api-artifact/artifact.json").read_bytes() == (
        root / "artifacts/1/artifact.json"
    ).read_bytes()
    assert (root / "api-artifact/coverage.data").read_bytes() == (root / "data-1").read_bytes()
