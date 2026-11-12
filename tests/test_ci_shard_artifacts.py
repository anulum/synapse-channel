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
    else:
        assert kind == "coverage-altered", kind
        with (directory / "coverage.data").open("ab") as output:
            output.write(b"altered after publication")
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


def _set_attempt(root: Path, index: int, attempt: str) -> None:
    """Rewrite one published shard's hosted attempt, including its native receipt."""
    path = root / f"artifacts/{index}/artifact.json"
    record = json.loads(path.read_text())
    record["main"]["attempt"] = attempt
    native = record.get("native")
    if isinstance(native, dict):
        native["attempt"] = attempt
    path.write_text(json.dumps(record))


def _verify_attempt(root: Path, attempt: str, *, earlier: bool) -> subprocess.CompletedProcess[str]:
    """Run the public union gate at one aggregate attempt."""
    arguments = [
        str(TOOL),
        "verify",
        "--root",
        "artifacts",
        "--source",
        SOURCE,
        "--run-id",
        "actual-runner-fixture",
        "--attempt",
        attempt,
        "--python",
        PYTHON,
        "--count",
        "2",
        "--coverage-python",
        PYTHON,
        "--coverage-config",
        "pyproject.toml",
        "--fail-under",
        "98",
        "--output",
        "reports",
    ]
    if earlier:
        arguments.append("--allow-earlier-attempts")
    return _run(root, arguments)


def test_partial_rerun_accepts_earlier_shards_from_the_same_run(
    actual_artifacts: Path, tmp_path: Path
) -> None:
    """A later aggregate keeps successful shards and refuses a newer producer."""
    root = tmp_path / "project"
    shutil.copytree(actual_artifacts, root)
    _set_attempt(root, 0, "2")
    strict = _verify_attempt(root, "2", earlier=False)
    assert strict.returncode != 0
    assert "does not match" in strict.stderr
    assert not (root / "reports").exists()
    accepted = _verify_attempt(root, "2", earlier=True)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    shutil.rmtree(root / "reports")
    _set_attempt(root, 0, "3")
    newer = _verify_attempt(root, "2", earlier=True)
    assert newer.returncode != 0
    assert "newer than the aggregate" in newer.stderr
    assert not (root / "reports").exists()
    _set_attempt(root, 0, "1")
    _set_attempt(root, 1, "01")
    invalid = _verify_attempt(root, "2", earlier=True)
    assert invalid.returncode != 0
    assert "invalid hosted attempt" in invalid.stderr


def test_ci_failed_job_rerun_reuses_this_run_and_states_the_matrix_result() -> None:
    """A failed-job rerun finds this run's timings and says why coverage is unqualified."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    timings_upload = next(
        step
        for step in workflow["jobs"]["test-timings"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    shard_upload = next(
        step
        for step in workflow["jobs"]["test"]["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert timings_upload["with"]["name"] == "pytest-timings-${{ github.sha }}-${{ github.run_id }}"
    assert "${{ github.run_attempt }}" not in timings_upload["with"]["name"]
    assert timings_upload["with"]["overwrite"] is True
    assert shard_upload["with"]["overwrite"] is True
    downloads = [
        step["with"]["name"]
        for step in workflow["jobs"]["test"]["steps"] + workflow["jobs"]["test-complete"]["steps"]
        if step.get("with", {}).get("name", "").startswith("pytest-timings-")
    ]
    assert downloads == [timings_upload["with"]["name"], timings_upload["with"]["name"]]
    command = next(
        step["run"]
        for step in workflow["jobs"]["test-complete"]["steps"]
        if "ci_shard_artifacts.py verify" in step.get("run", "")
    )
    assert "--allow-earlier-attempts" in command
    gate = workflow["jobs"]["test-complete"]["steps"][0]["run"]
    for shards, windows, code in (
        ("success", "success", 0),
        ("success", "failure", 1),
        ("cancelled", "success", 1),
        ("failure", "success", 1),
        ("", "", 1),
    ):
        completed = subprocess.run(
            ["bash", "-c", gate],
            env=dict(os.environ, SHARD_RESULT=shards, WINDOWS_RESULT=windows),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert completed.returncode == code
        if code != 0:
            assert completed.stdout == (
                f"::error::The test matrix result is {shards or 'empty'} and the Windows"
                f" coverage part is {windows or 'empty'}. Coverage is not qualified. Re-run the"
                " failed test jobs; this run keeps successful shard artifacts and the original"
                " timing snapshot.\n"
            )


def test_ci_keeps_the_shard_coverage_database_outside_the_checkout(tmp_path: Path) -> None:
    """The real prepare step exports a runner-temp data file, and the pack step reads it."""
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["test"]["steps"]
    prepare = next(
        step for step in steps if step.get("name") == "Prepare isolated kernel coverage output"
    )
    runner_temp, github_env = tmp_path / "runner-temp", tmp_path / "github-env"
    runner_temp.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    completed = subprocess.run(
        ["bash", "-c", prepare["run"]],
        cwd=checkout,
        env=dict(os.environ, RUNNER_TEMP=str(runner_temp), GITHUB_ENV=str(github_env)),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert (checkout / "native-kernel-coverage").is_dir()
    assert (runner_temp / "coverage-data").is_dir()
    assert github_env.read_text() == f"COVERAGE_FILE={runner_temp}/coverage-data/.coverage\n"
    pack = next(
        step["run"] for step in steps if "ci_shard_artifacts.py pack" in step.get("run", "")
    )
    assert 'args+=(--coverage-file "$COVERAGE_FILE")' in pack
    assert all("--coverage-file .coverage" not in step.get("run", "") for step in steps)


def test_application_report_retains_probe_data_without_expanding_the_package_gate(
    tmp_path: Path,
) -> None:
    """The real report covers every application module and retains off-scope raw observations."""
    package = tmp_path / "src/synapse_channel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "app.py").write_text("value = 1\n")
    (package / "unexecuted.py").write_text("value = 2\n")
    (tmp_path / "diagnostic.py").write_text("def untouched():\n    return 3\n")
    (tmp_path / "driver.py").write_text(
        "import sys\nsys.path.insert(0, 'src')\nimport diagnostic\n"
        "from synapse_channel import app\nassert app.value == 1\n"
    )
    (tmp_path / "measure.ini").write_text(
        "[run]\nbranch = true\nsource = src/synapse_channel, diagnostic\n"
    )
    result = _run(
        tmp_path,
        ["-m", "coverage", "run", "--rcfile=measure.ini", "driver.py"],
        coverage_file=tmp_path / ".coverage",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run(
        tmp_path,
        ["-m", "coverage", "json", "--rcfile=" + str(ROOT / "pyproject.toml"), "-o", "report.json"],
        coverage_file=tmp_path / ".coverage",
    )
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads((tmp_path / "report.json").read_text())
    assert set(report["files"]) == {
        "src/synapse_channel/__init__.py",
        "src/synapse_channel/app.py",
        "src/synapse_channel/unexecuted.py",
    }
    assert report["files"]["src/synapse_channel/unexecuted.py"]["missing_lines"] == [1]
    from coverage import CoverageData

    data = CoverageData(basename=str(tmp_path / ".coverage"))
    data.read()
    assert str(tmp_path / "diagnostic.py") in data.measured_files()


PLATFORM = sys.platform
ONE_SHARD = f"a {PLATFORM} coverage part is one complete shard with its own coverage"


def _with_platform_paths(root: Path) -> None:
    """Map a second checkout's ``app.py`` onto this project, as the package config does."""
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text()
        + f'[tool.coverage.paths]\nsource = ["{root.as_posix()}", "*/platform-checkout"]\n'
    )


def _platform_part(root: Path, *, name: str = PLATFORM, native: bool = False) -> Path:
    """Measure the branch the shards miss in another checkout and pack it as a platform part."""
    other = root.parent / "platform-checkout"
    other.mkdir()
    for item in ("pyproject.toml", "app.py"):
        shutil.copyfile(root / item, other / item)
    (other / "test_platform.py").write_text(
        "from app import value\n\ndef test_true():\n    assert value(True) == 1\n"
    )
    measured = _run(
        other,
        [
            "-m",
            "pytest",
            "test_platform.py",
            "-q",
            "-p",
            "tools.pytest_shard",
            "-p",
            "pytest_cov.plugin",
            "--shard-index=0",
            "--shard-count=1",
            "--shard-output=platform.json",
            "--cov=app",
            "--cov-config=pyproject.toml",
            "--cov-report=",
            "--cov-fail-under=0",
            "-o",
            "cache_dir=" + str(other / "cache"),
        ],
        coverage_file=other / "platform-data",
    )
    assert measured.returncode == 0, measured.stdout + measured.stderr
    options = ["--native-manifest", "platform.json"] if native else []
    packed = _run(
        other,
        [
            str(TOOL),
            "pack",
            "--platform",
            name,
            "--manifest",
            "platform.json",
            "--coverage-file",
            "platform-data",
            "--output",
            str(root / "platform-part"),
            *options,
        ],
    )
    assert packed.returncode == 0, packed.stdout + packed.stderr
    return root / "platform-part"


def _verify_parts(root: Path, *parts: str) -> subprocess.CompletedProcess[str]:
    """Run the public gate with named platform parts at the fixture's threshold."""
    arguments = [
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
        PYTHON,
        "--coverage-config",
        "pyproject.toml",
        "--fail-under",
        "100",
        "--output",
        "reports",
    ]
    for part in parts:
        arguments += ["--platform-coverage", part]
    return _run(root, arguments)


@pytest.fixture(scope="module")
def platform_artifacts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Linux-style shards that miss one branch, and a platform part that measures it."""
    root = tmp_path_factory.mktemp("platform-fixture") / "project"
    _prepare(root, both_branches=False)
    _with_platform_paths(root)
    for index in range(2):
        assert _pytest(root, index).returncode == 0
        if index == 0:
            assert _pytest(root, 0, native=True).returncode == 0
        packed = _run(
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
        assert packed.returncode == 0, packed.stdout + packed.stderr
    _platform_part(root)
    return root


def test_a_platform_part_joins_the_union_and_completes_the_gate(
    platform_artifacts: Path, tmp_path: Path
) -> None:
    """The branch only the other checkout runs counts once its part is named."""
    root = tmp_path / "project"
    shutil.copytree(platform_artifacts, root)
    without = _verify_parts(root)
    assert without.returncode == 1 and "is below 100%" in without.stderr
    shutil.rmtree(root / "reports")
    joined = _verify_parts(root, f"{PLATFORM}=platform-part")
    assert joined.returncode == 0, joined.stdout + joined.stderr
    report = json.loads((root / "reports/coverage.json").read_text())
    assert report["totals"]["percent_covered"] == 100
    assert [Path(name).name for name in report["files"]] == ["app.py"]


def test_the_public_verify_api_counts_a_named_platform_part(
    platform_artifacts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-process gate adds the part's data and reaches the full union."""
    from tools.ci_shard_artifacts import verify

    root = tmp_path / "project"
    shutil.copytree(platform_artifacts, root)
    monkeypatch.chdir(root)
    verify(
        Path("artifacts"),
        SOURCE,
        "actual-runner-fixture",
        "1",
        [PYTHON],
        2,
        PYTHON,
        Path("pyproject.toml"),
        Path("reports"),
        100,
        platforms={PLATFORM: Path("platform-part")},
    )
    report = json.loads((root / "reports/coverage.json").read_text())
    assert report["totals"]["percent_covered"] == 100


def _damage_part(root: Path, kind: str) -> None:
    """Corrupt a copied platform part to exercise one refusal of the gate."""
    directory = root / "platform-part"
    path = directory / "artifact.json"
    record = json.loads(path.read_text())
    main = record["main"]
    if kind == "missing":
        shutil.rmtree(directory)
        return
    if kind == "schema":
        record["schema"] = 2
    elif kind == "run":
        main["run_id"] = "another-run"
    elif kind == "python":
        main["python"] = "2.7"
    elif kind == "failed":
        main["success"] = False
    elif kind == "native":
        record["native"] = dict(main)
    elif kind == "receipt-platform":
        main["platform"] = "another-system"
    elif kind == "count":
        main["count"] = 2
    elif kind == "no-coverage":
        record["coverage_sha256"] = None
    elif kind == "altered":
        with (directory / "coverage.data").open("ab") as output:
            output.write(b"altered after publication")
    else:
        assert kind == "newer", kind
        main["attempt"] = "2"
    path.write_text(json.dumps(record))


@pytest.mark.parametrize(
    ("kind", "part", "diagnostic"),
    [
        ("missing", PLATFORM, "missing ordinary artifact: platform-part/artifact.json"),
        ("schema", PLATFORM, f"invalid {PLATFORM} coverage artifact"),
        (None, "another-system", "invalid another-system coverage artifact"),
        ("run", PLATFORM, f"{PLATFORM} coverage artifact does not match this run"),
        ("python", PLATFORM, f"{PLATFORM} coverage artifact does not match this run"),
        ("failed", PLATFORM, "incomplete or misidentified pytest shard"),
        ("native", PLATFORM, ONE_SHARD),
        ("receipt-platform", PLATFORM, ONE_SHARD),
        ("count", PLATFORM, ONE_SHARD),
        ("no-coverage", PLATFORM, ONE_SHARD),
        ("altered", PLATFORM, f"missing or altered {PLATFORM} coverage data"),
        ("newer", PLATFORM, f"{PLATFORM} coverage artifact does not match this run"),
    ],
)
def test_a_named_platform_part_that_is_missing_foreign_or_altered_refuses_the_gate(
    platform_artifacts: Path, tmp_path: Path, kind: str | None, part: str, diagnostic: str
) -> None:
    """A part the gate was told to count either counts whole or refuses the gate."""
    root = tmp_path / "project"
    shutil.copytree(platform_artifacts, root)
    if kind is not None:
        _damage_part(root, kind)
    result = _verify_parts(root, f"{part}=platform-part")
    assert result.returncode == 1
    assert result.stderr == f"CI shard refused: {diagnostic}\n"
    assert not (root / "reports").exists()


def test_an_earlier_platform_part_counts_in_a_later_attempt_and_a_newer_one_refuses(
    platform_artifacts: Path, tmp_path: Path
) -> None:
    """A rerun aggregate keeps a successful platform part from an earlier attempt only."""
    root = tmp_path / "project"
    shutil.copytree(platform_artifacts, root)

    def gate(attempt: str) -> subprocess.CompletedProcess[str]:
        arguments = [
            str(TOOL),
            "verify",
            "--root",
            "artifacts",
            "--source",
            SOURCE,
            "--run-id",
            "actual-runner-fixture",
            "--attempt",
            attempt,
            "--python",
            PYTHON,
            "--count",
            "2",
            "--coverage-python",
            PYTHON,
            "--coverage-config",
            "pyproject.toml",
            "--fail-under",
            "100",
            "--output",
            "reports",
            "--allow-earlier-attempts",
            "--platform-coverage",
            f"{PLATFORM}=platform-part",
        ]
        return _run(root, arguments)

    accepted = gate("2")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    shutil.rmtree(root / "reports")
    _damage_part(root, "newer")
    path = root / "platform-part/artifact.json"
    record = json.loads(path.read_text())
    record["main"]["attempt"] = "3"
    path.write_text(json.dumps(record))
    newer = gate("2")
    assert newer.returncode == 1
    assert newer.stderr == "CI shard refused: shard attempt is newer than the aggregate\n"
    assert not (root / "reports").exists()


@pytest.mark.parametrize(
    ("change", "diagnostic"),
    [
        ("native", ONE_SHARD),
        ("name", "a another-system coverage part is one complete shard with its own coverage"),
        ("no-coverage", ONE_SHARD),
    ],
)
def test_pack_refuses_a_platform_part_that_is_not_one_measured_shard(
    tmp_path: Path, change: str, diagnostic: str
) -> None:
    """Only a single complete shard with its own data can become a platform part."""
    root = tmp_path / "project"
    _prepare(root)
    measured = _pytest(root, 0, native=True)
    assert measured.returncode == 0, measured.stdout + measured.stderr
    arguments = [str(TOOL), "pack", "--manifest", "native.json", "--output", "part"]
    arguments += ["--platform", "another-system" if change == "name" else PLATFORM]
    if change != "no-coverage":
        arguments += ["--coverage-file", "data-0"]
    if change == "native":
        arguments += ["--native-manifest", "native.json"]
    result = _run(root, arguments)
    assert result.returncode == 1
    assert result.stderr == f"CI shard refused: {diagnostic}\n"
    assert not (root / "part").exists()


@pytest.mark.parametrize("value", ["no-separator", "=directory", "name=", "twice=a"])
def test_platform_coverage_is_named_once_with_its_directory(
    platform_artifacts: Path, tmp_path: Path, value: str
) -> None:
    """A malformed or repeated part name is refused before anything is measured."""
    root = tmp_path / "project"
    shutil.copytree(platform_artifacts, root)
    parts = [value, "twice=b"] if value == "twice=a" else [value]
    result = _verify_parts(root, *parts)
    assert result.returncode == 1
    assert result.stderr == (
        "CI shard refused: platform coverage is NAME=DIRECTORY, each name once\n"
    )
    assert not (root / "reports").exists()


def test_ci_joins_the_hosted_windows_part_to_the_unchanged_complete_gate() -> None:
    """The Windows job packs a win32 part, and test-complete counts it at 99.36 percent."""
    import tomllib
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    windows = workflow["jobs"]["test-windows"]
    assert windows["runs-on"] == "windows-latest"
    names = [step.get("name", step.get("run", "")) for step in windows["steps"]]
    venv = names.index("Create the virtual environment the Windows tests run in")
    assert venv < names.index("python -m pip install -e . --no-deps")
    assert "GITHUB_PATH" in windows["steps"][venv]["run"]
    run = " ".join(step.get("run", "") for step in windows["steps"])
    assert "-p tools.pytest_shard --shard-index=0 --shard-count=1" in run
    assert "--cov=synapse_channel" in run
    assert "ci_shard_artifacts.py pack --platform win32" in run
    upload = next(
        step
        for step in windows["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["name"] == "pytest-windows-${{ github.sha }}"
    complete = workflow["jobs"]["test-complete"]
    assert complete["needs"] == ["test", "test-windows"]
    assert "test-windows" in workflow["jobs"]["ci"]["needs"]
    download = next(
        step
        for step in complete["steps"]
        if step.get("with", {}).get("name") == "pytest-windows-${{ github.sha }}"
    )
    assert download["with"]["path"] == "ci-windows"
    command = next(
        step["run"]
        for step in complete["steps"]
        if "ci_shard_artifacts.py verify" in step.get("run", "")
    )
    assert "--fail-under 99.36" in command
    assert "--platform-coverage win32=ci-windows" in command
    listed = [token for token in run.split() if token.startswith("tests/")]
    assert listed and listed == sorted(set(listed))
    assert all((ROOT / token).is_file() for token in listed)
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert config["tool"]["coverage"]["report"]["fail_under"] == 99.36
    assert config["tool"]["coverage"]["paths"]["source"] == [
        "src/synapse_channel",
        "*/src/synapse_channel",
        "*\\src\\synapse_channel",
    ]
