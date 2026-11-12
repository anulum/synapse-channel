# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the federation interface is exact, lazy and identical to its definitions
"""Pin ``synapse_channel.federation_api``: its names, their grouping and its lazy loading.

The fleet inventory guard (``tests/test_federation_consumer_contract.py``) pins the
same names by defining module and kind, and checks that each name on this surface is
its defining module's own object; this file pins the surface itself.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import synapse_channel
from synapse_channel import federation_api

# The fleet tier's inventory (109 at the introduction, 110 once the fleet tier dropped its
# record-keeping copies and read Core's wire version where it is defined); changing the
# count is a reviewed edit here, in the inventory guard and on the API stability page.
_SURFACE_SIZE = 110


def test_the_surface_is_sorted_unique_and_of_the_promised_size() -> None:
    assert federation_api.__all__ == sorted(set(federation_api.__all__))
    assert len(federation_api.__all__) == _SURFACE_SIZE


def test_the_capability_modules_partition_the_surface() -> None:
    owners = federation_api._CAPABILITY_OF
    assert set(owners) == set(federation_api.__all__)
    for capability in sorted(set(owners.values())):
        module = importlib.import_module(f"synapse_channel.federation_api.{capability}")
        assert module.__all__ == sorted(
            name for name, owner in owners.items() if owner == capability
        )
        for name in module.__all__:
            assert getattr(federation_api, name) is getattr(module, name)


def test_a_name_off_the_surface_is_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match="has no attribute 'EventStore'"):
        _ = federation_api.EventStore
    assert not hasattr(federation_api, "_private_helper")


def test_listing_and_star_import_show_exactly_the_surface() -> None:
    assert dir(federation_api) == federation_api.__all__
    namespace: dict[str, object] = {}
    exec("from synapse_channel.federation_api import *", namespace)
    assert set(namespace) - {"__builtins__"} == set(federation_api.__all__)


def test_importing_the_surface_loads_no_other_module() -> None:
    probe = (
        "import sys, synapse_channel\n"
        "before = set(sys.modules)\n"
        "import synapse_channel.federation_api\n"
        "print(sorted(set(sys.modules) - before))\n"
    )
    package_root = Path(synapse_channel.__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        check=True,
        cwd=package_root,
        env={**os.environ, "PYTHONPATH": str(package_root)},
        text=True,
        timeout=60,
    )
    assert result.stdout.strip() == "['synapse_channel.federation_api']"


def test_the_package_root_resolves_one_public_name() -> None:
    """The package root loads a public name on first use and lists the surface."""
    from synapse_channel.core.protocol import MessageType

    assert synapse_channel.MessageType is MessageType
    assert "MessageType" in dir(synapse_channel)
    with pytest.raises(AttributeError, match="has no attribute 'not_a_public_name'"):
        _ = synapse_channel.not_a_public_name


def _type_checking_bindings(source: str) -> list[tuple[str, str, str]]:
    """Return ``(module, imported name, local name)`` from ``TYPE_CHECKING`` blocks."""
    bindings: list[tuple[str, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Name) or test.id != "TYPE_CHECKING":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.ImportFrom) or statement.module is None:
                continue
            for alias in statement.names:
                bindings.append((statement.module, alias.name, alias.asname or alias.name))
    return bindings


def test_type_checking_imports_are_the_runtime_lazy_exports(tmp_path: Path) -> None:
    """Every ``TYPE_CHECKING`` name is the same object the lazy export returns.

    The blocks themselves do not run. This is their applicability check: the
    parsed import, the runtime object, and a strict mypy probe of a consumer.
    """
    negative = _type_checking_bindings(
        "\n".join(
            (
                "if OTHER:",
                "    from os import path",
                "if True:",
                "    x = 1",
                "if TYPE_CHECKING:",
                "    x = 1",
                "    from . import local_name",
                "    from os import path as file_path",
                "",
            )
        )
    )
    assert negative == [("os", "path", "file_path")]

    package_root = Path(synapse_channel.__file__).resolve().parent
    surface = package_root / "federation_api" / "__init__.py"
    for module_name, imported, local in _type_checking_bindings(
        surface.read_text(encoding="utf-8")
    ):
        assert local == imported
        assert local in federation_api.__all__
        defining = importlib.import_module(module_name)
        assert getattr(federation_api, local) is getattr(defining, imported)

    for path in sorted((package_root / "federation_api").glob("*.py")):
        if path.name == "__init__.py":
            continue
        assert _type_checking_bindings(path.read_text(encoding="utf-8")) == []

    package_source = (package_root / "__init__.py").read_text(encoding="utf-8")
    exports = synapse_channel._EXPORTS
    for module_name, imported, local in _type_checking_bindings(package_source):
        assert exports[local] == f"{module_name}:{imported}"
        defining = importlib.import_module(module_name)
        assert getattr(synapse_channel, local) is getattr(defining, imported)

    consumer = tmp_path / "consumer_probe.py"
    consumer.write_text(
        "\n".join(
            (
                "from synapse_channel import SynapseHub",
                "from synapse_channel.federation_api import ClockSkew",
                "",
                "def take(hub: SynapseHub, skew: ClockSkew) -> tuple[SynapseHub, ClockSkew]:",
                "    return hub, skew",
                "",
            )
        ),
        encoding="utf-8",
    )
    probe = subprocess.run(
        [sys.executable, "-m", "mypy", "--strict", str(consumer)],
        capture_output=True,
        check=False,
        cwd=package_root.parent,
        env={**os.environ, "PYTHONPATH": str(package_root.parent)},
        text=True,
        timeout=180,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
