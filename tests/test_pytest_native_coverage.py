# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual pytest native coverage routing journeys
"""Run the native routing plugin through real isolated pytest processes."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NATIVE_NODE = (
    "tests/fixtures/protected_write_mounts_linux.py::test_real_private_namespace_mount_policy"
)
_WRITER = """import json,os,subprocess,sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('case', ['accepted','base-writable'])
def test_real_private_namespace_mount_policy(case):
    directory=Path(os.environ['SYNAPSE_TEST_COVERAGE_DIR'])
    subprocess.run([sys.executable,'-m','coverage','run',
        '--data-file='+str(directory/'.coverage.observed'),'payload.py'],check=True)
    with Path('destinations.jsonl').open('a') as output:
        output.write(json.dumps({'case':case,'directory':str(directory)})+'\\n')
"""


def _run(root: Path, native: str | None) -> subprocess.CompletedProcess[str]:
    """Invoke the public plugin entry point with only this task's fixture population."""
    env = dict(
        os.environ,
        PYTHONPATH=str(ROOT),
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        COVERAGE_RCFILE=str(root / "coverage.ini"),
        COVERAGE_FILE=str(root / ".coverage"),
    )
    env.pop("SYNAPSE_TEST_COVERAGE_DIR", None)
    if native is not None:
        env["SYNAPSE_TEST_COVERAGE_DIR"] = native
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=tools.pytest_native_coverage",
            "--parallel-mode",
            "--data-file=" + str(root / "plugin.coverage"),
            "-m",
            "pytest",
            "-q",
            "-p",
            "tools.pytest_native_coverage",
            "tests",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _project(root: Path) -> Path:
    """Create native writer and ordinary cases that exercise actual routing hooks."""
    (root / "coverage.ini").write_text("[run]\nbranch = true\n")
    (root / "payload.py").write_text("print('native coverage observation')\n")
    fixtures = root / "tests/fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "protected_write_mounts_linux.py").write_text(_WRITER)
    (root / "tests/test_ordinary.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def test_ordinary():\n"
        "    assert Path(os.environ['SYNAPSE_TEST_COVERAGE_DIR']) == Path('native').resolve()\n"
    )
    (root / "pytest.ini").write_text(
        "[pytest]\npython_files = test_*.py protected_write_mounts_linux.py\n"
    )
    native = root / "native"
    native.mkdir()
    return native


def test_actual_pytest_routes_native_parameters_and_restores_the_ordinary_root(
    tmp_path: Path,
) -> None:
    """Each parameter writes real coverage into its own deterministic node directory."""
    native = _project(tmp_path)
    result = _run(tmp_path, str(native))
    assert result.returncode == 0, result.stdout + result.stderr
    observed = [
        json.loads(line) for line in (tmp_path / "destinations.jsonl").read_text().splitlines()
    ]
    assert len(observed) == 2
    for record in observed:
        node = NATIVE_NODE + "[" + record["case"] + "]"
        expected = native / hashlib.sha256(node.encode("utf-8")).hexdigest()
        assert Path(record["directory"]) == expected
        assert (expected / ".coverage.observed").is_file()
    assert len(list(native.iterdir())) == 2


def test_actual_probe_writers_are_isolated_from_required_kernel_observations(
    tmp_path: Path,
) -> None:
    """A real probe child writes separate per-node data without polluting the kernel root."""
    native = _project(tmp_path)
    probe = tmp_path / "tests/test_protected_runtime_probe_example.py"
    probe.write_text(_WRITER.replace("test_real_private_namespace_mount_policy", "test_probe"))
    result = _run(tmp_path, str(native))
    assert result.returncode == 0, result.stdout + result.stderr
    records = [
        json.loads(line) for line in (tmp_path / "destinations.jsonl").read_text().splitlines()
    ]
    assert len(records) == 4
    assert len(list(native.iterdir())) == 2
    probe_root = native.with_name(native.name + "-probe")
    assert len(list(probe_root.iterdir())) == 2
    for case in ("accepted", "base-writable"):
        node = "tests/test_protected_runtime_probe_example.py::test_probe[" + case + "]"
        directory = probe_root / hashlib.sha256(node.encode()).hexdigest()
        assert (directory / ".coverage.observed").is_file()
        assert any(record["directory"] == str(directory) for record in records)


@pytest.mark.parametrize("invalid", ["unset", "relative", "missing", "file", "symlink"])
def test_plugin_refuses_unusable_output_roots_before_collecting_tests(
    tmp_path: Path, invalid: str
) -> None:
    """Configuration errors fail loudly and never fall back to shared native output."""
    native = _project(tmp_path)
    candidate: str | None = str(native)
    if invalid == "unset":
        candidate = None
    elif invalid == "relative":
        candidate = "native"
    elif invalid == "missing":
        candidate = str(tmp_path / "absent")
    elif invalid == "file":
        candidate = str(tmp_path / "payload.py")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(native, target_is_directory=True)
        candidate = str(alias)
    result = _run(tmp_path, candidate)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "native coverage routing requires" in result.stderr
    assert not list(native.iterdir())


def test_plugin_refuses_reusing_an_existing_case_directory(tmp_path: Path) -> None:
    """Stale data cannot be admitted as observations of a newly executed case."""
    native = _project(tmp_path)
    prior = native / hashlib.sha256((NATIVE_NODE + "[accepted]").encode("utf-8")).hexdigest()
    prior.mkdir()
    sentinel = prior / "original"
    sentinel.write_text("preserve previous observations")
    result = _run(tmp_path, str(native))
    assert result.returncode == 1
    assert "FileExistsError" in result.stdout
    assert sentinel.read_text() == "preserve previous observations"
    assert not (prior / ".coverage.observed").exists()


@pytest.mark.docs_contract
def test_native_writer_registry_covers_actual_instrumented_profiles() -> None:
    """A newly instrumented native profile must join the routing and merge registry."""
    source = ROOT / "tools/pytest_native_coverage.py"
    value = next(
        node.value
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "NATIVE_WRITERS"
            for target in node.targets
        )
    )
    writers = ast.literal_eval(value)
    expected = {
        path.name
        for path in (ROOT / "tests/fixtures").glob("protected_write_*_linux.py")
        if "SYNAPSE_TEST_COVERAGE_DIR" in path.read_text()
    }
    assert {writer.split("::", 1)[0].rsplit("/", 1)[-1] for writer in writers} == expected
    for writer in writers:
        module, function = writer.split("::", 1)
        assert function in {
            node.name
            for node in ast.parse((ROOT / module).read_text()).body
            if isinstance(node, ast.FunctionDef)
        }
