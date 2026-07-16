# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — packaging extras drift guard and feature-module import smoke
"""Guard the optional-dependency extras and the modules that consume them.

The wheel keeps a deliberately minimal runtime (`websockets` only); every feature
library — cryptography, WASM, OTel, MCP, tree-sitter — lives behind a named extra so a base
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

import importlib
from pathlib import Path
from typing import Any

import pytest

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
    "synapse_channel.git.semantic_tree_sitter",
)


def _load_pyproject() -> dict[str, Any]:
    for module_name in ("tomllib", "tomli"):
        try:
            toml = importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        return dict(toml.loads(_PYPROJECT.read_text(encoding="utf-8")))
    pytest.skip("no TOML parser available (tomllib on 3.11+, or tomli)")


def _extras() -> dict[str, list[str]]:
    project = _load_pyproject()["project"]
    return dict(project["optional-dependencies"])


def test_runtime_dependencies_stay_minimal() -> None:
    # The runtime floor is a single dependency. A heavy feature library must live
    # in an extra, never here, or a base install would pull it in unasked.
    dependencies = _load_pyproject()["project"]["dependencies"]
    names = [dep.split(">=")[0].split("==")[0].split("[")[0].strip() for dep in dependencies]
    assert names == ["websockets"]


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
    assert set(_extras()["mcp"]) == {"mcp==1.28.1", "cryptography>=42.0"}


@pytest.mark.parametrize("module_name", _FEATURE_MODULES)
def test_feature_module_imports_without_its_optional_dependency_at_import_time(
    module_name: str,
) -> None:
    # Importing a feature module must never require its optional library — the
    # dependency loads lazily on first use. A top-level import of cryptography /
    # wasmtime / opentelemetry / mcp would break a base install; this catches it.
    module = importlib.import_module(module_name)
    assert module is not None
