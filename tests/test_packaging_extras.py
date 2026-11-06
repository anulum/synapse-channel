# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaging extras drift guard and feature-module import smoke
"""Guard the optional-dependency extras and the modules that consume them.

The wheel keeps a minimal runtime: WebSocket transport. Every feature library —
cryptography, WASM, OTel, MCP, tree-sitter — lives behind a named extra so a base
install stays lean and a feature install is explicit. Two failure modes are worth
a permanent test: the runtime dependency set quietly growing a heavy library, and
the `all` convenience extra drifting out of sync with the individual feature
extras it is meant to union. Both are asserted from the packaging metadata here,
so neither can regress silently. A companion smoke test imports every
feature-consuming module to prove the base import surface never hard-requires an
optional library (the deps load lazily), which is exactly the packaging drift a
`pip install synapse-channel` user would otherwise hit as an ImportError.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import trace
import venv
from pathlib import Path
from typing import Any

import pytest
import tomllib
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

#: Extras that carry a runtime feature library, and the module(s) that consume each.
_FEATURE_EXTRAS = (
    "wasm",
    "otel",
    "mcp",
    "encryption",
    "sqlcipher",
    "pkcs11",
    "tpm2",
    "cloud-hsm",
    "semantic",
)

#: One import per optional dependency proving the feature module loads without the
#: dependency being present at import time (it is loaded lazily on use).
_FEATURE_MODULES = (
    "synapse_channel.core.payload_crypto",
    "synapse_channel.core.message_auth",
    "synapse_channel.core.tls",
    "synapse_channel.core.at_rest",
    "synapse_channel.core.persistence_sqlcipher",
    "synapse_channel.core.at_rest_pkcs11",
    "synapse_channel.core.at_rest_tpm2",
    "synapse_channel.core.at_rest_cloud_hsm",
    "synapse_channel.core.receipt_signing",
    "synapse_channel.core.wasm_sandbox",
    "synapse_channel.otel_export",
    "synapse_channel.mcp.registration",
    "synapse_channel.core.mcp_config_signing",
    "synapse_channel.core.aef_emission",
    "synapse_channel.git.semantic_tree_sitter",
)


def _load_pyproject() -> dict[str, Any]:
    return dict(tomllib.loads(_PYPROJECT.read_text(encoding="utf-8")))


def _extras() -> dict[str, list[str]]:
    project = _load_pyproject()["project"]
    return dict(project["optional-dependencies"])


def test_runtime_dependencies_stay_minimal() -> None:
    """Keep feature libraries optional across every supported interpreter."""
    project = _load_pyproject()["project"]
    supported = SpecifierSet(project["requires-python"])
    assert "3.10.22" not in supported
    requirements = [Requirement(value) for value in project["dependencies"]]
    assert [requirement.name for requirement in requirements] == ["websockets"]
    for version in ("3.11", "3.12", "3.13"):
        assert version in supported
        active = [
            requirement.name
            for requirement in requirements
            if requirement.marker is None
            or requirement.marker.evaluate({"python_version": version})
        ]
        assert active == ["websockets"]


def test_websockets_floor_supports_the_asyncio_api() -> None:
    # Production modules import websockets.asyncio, which ships since
    # websockets 13.0; a 12.x environment resolved by the old >=12.0 floor
    # raised ModuleNotFoundError on first import. Both public declarations
    # must state the true floor.
    dependencies = _load_pyproject()["project"]["dependencies"]
    websockets_pin = next(dep for dep in dependencies if dep.startswith("websockets"))
    assert websockets_pin == "websockets>=13.0"

    requirements = (_PYPROJECT.parent / "requirements.txt").read_text(encoding="utf-8")
    assert "websockets>=13.0" in requirements.splitlines()


@pytest.mark.parametrize("extra", _FEATURE_EXTRAS)
def test_feature_extra_is_declared_and_non_empty(extra: str) -> None:
    extras = _extras()
    assert extra in extras, f"missing feature extra: {extra}"
    assert extras[extra], f"feature extra {extra!r} declares no dependency"


def test_all_extra_unions_the_feature_extras() -> None:
    # `all` must be exactly the union of the feature extras so it cannot drift: add
    # a dependency to a feature extra and forget `all`, and this fails.
    extras = _extras()
    assert "all" in extras, "missing 'all' convenience extra"
    union: set[str] = set()
    for extra in _FEATURE_EXTRAS:
        union.update(extras[extra])
    assert set(extras["all"]) == union


def test_mcp_extra_installs_manifest_signature_verification() -> None:
    assert set(_extras()["mcp"]) == {
        "mcp==2.3.0",
        "cryptography>=42.0",
        "PyJWT>=2.15.0,<3",
        "uvicorn>=0.54.0,<1; sys_platform != 'emscripten'",
    }


@pytest.fixture(scope="module")
def base_package_runtime(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Install the actual built wheel with only its hash-locked base dependency."""
    root = _PYPROJECT.parent
    directory = tmp_path_factory.mktemp("base-package-runtime")
    environment = directory / "environment"
    builder = venv.EnvBuilder(with_pip=True)
    context = builder.ensure_directories(environment)
    python = Path(context.env_exec_cmd)
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    subprocess.run(
        [sys.executable, "-m", "venv", str(environment)],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheelhouse = directory / "wheels"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(wheelhouse)],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    wheels = list(wheelhouse.glob("synapse_channel-*.whl"))
    assert len(wheels) == 1
    for arguments in (
        [
            "--require-hashes",
            "-r",
            str(root / ".github/requirements/requirements-base-runtime.txt"),
        ],
        ["--no-index", "--no-deps", str(wheels[0])],
    ):
        subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                *arguments,
            ],
            cwd=directory,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    listed = subprocess.run(
        [str(python), "-I", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    installed = {item["name"].lower().replace("_", "-") for item in json.loads(listed.stdout)}
    assert {"synapse-channel", "websockets"} <= installed
    assert not installed & {"cryptography", "mcp", "wasmtime", "opentelemetry-api", "grpcio"}
    subprocess.run(
        [str(python), "-I", "-m", "pip", "check", "--disable-pip-version-check"],
        cwd=directory,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    script = directory / "import_boundary.py"
    script.write_text(
        "import importlib\nimport sys\n"
        "module = importlib.import_module(sys.argv[1])\nprint(module.__file__)\n",
        encoding="utf-8",
    )
    return python, script


@pytest.mark.parametrize("module_name", _FEATURE_MODULES)
def test_feature_module_imports_without_its_optional_dependency_at_import_time(
    module_name: str, base_package_runtime: tuple[Path, Path]
) -> None:
    python, script = base_package_runtime
    counts = script.parent / (module_name + ".counts")
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-m",
            "trace",
            "--count",
            "--coverdir",
            str(script.parent / f"trace-{_FEATURE_MODULES.index(module_name)}"),
            "--file",
            str(counts),
            str(script),
            module_name,
        ],
        cwd=script.parent,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    origin = Path(result.stdout.strip()).resolve()
    assert origin.is_relative_to(python.parent.parent)
    measured = trace.CoverageResults(infile=str(counts)).counts
    assert all(measured.get((str(script), line), 0) > 0 for line in range(1, 5))
