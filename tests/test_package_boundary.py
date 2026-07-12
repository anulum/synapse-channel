# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the coordination kernel stays a self-contained layer

"""The kernel boundary that makes a package decomposition possible.

`synapse_channel.core` is the coordination kernel — the hub, state, protocol,
persistence, and the wire codec. For a lean `synapse-channel-core` distribution
to be extractable (see docs/internal/proposals/package_decomposition_spec),
the kernel must not reach *up* into the feature layers (dashboard, federation,
adapters, sandbox, benchmarks, the CLI). This test walks every core module's
imports and asserts that invariant, so a new upward import fails the suite
instead of silently welding a feature into the kernel.

The layering is clean; this pins it with no exceptions. The historical
``synapse_channel.relay`` path remains a downward-only compatibility facade,
but kernel modules must import its canonical ``core.relay`` implementation.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent / "src" / "synapse_channel" / "core"
_PACKAGE = "synapse_channel"


def _imported_synapse_modules(source: str) -> set[str]:
    """Return the ``synapse_channel.*`` modules a source file imports."""
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith(f"{_PACKAGE}."):
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(f"{_PACKAGE}."):
                    modules.add(alias.name)
    return modules


def test_kernel_imports_only_kernel_modules() -> None:
    leaks: dict[str, set[str]] = {}
    for path in sorted(_CORE.rglob("*.py")):
        imported = _imported_synapse_modules(path.read_text(encoding="utf-8"))
        upward = {
            name
            for name in imported
            if not name.startswith(f"{_PACKAGE}.core.") and name != f"{_PACKAGE}.core"
        }
        if upward:
            leaks[str(path.relative_to(_CORE.parent.parent))] = upward
    assert not leaks, (
        "the coordination kernel reached up into feature layers — a split-blocking "
        f"import. Move the dependency into core or invert the dependency:\n{leaks}"
    )


def test_legacy_relay_path_reexports_the_canonical_kernel_objects() -> None:
    """The compatibility facade must not fork state or implementations."""
    canonical = importlib.import_module("synapse_channel.core.relay")
    legacy = importlib.import_module("synapse_channel.relay")

    assert legacy.__all__ == canonical.__all__
    for name in canonical.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
