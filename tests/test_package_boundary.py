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

The layering is already clean; this pins it. One historical exception is
allowed and named below — low-level plumbing that belongs in the kernel and is
tracked to be folded in.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent / "src" / "synapse_channel" / "core"
_PACKAGE = "synapse_channel"

# The kernel may import from these non-core siblings. Each entry is debt to
# fold into the kernel, not a licence to add more — keep this list shrinking.
_ALLOWED_NON_CORE = frozenset(
    {
        # Low-level NDJSON relay log + compact lite-wire codec: kernel plumbing
        # the hub's relay mirror uses; belongs in core, tracked to move.
        "synapse_channel.relay",
    }
)


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


def test_kernel_imports_only_kernel_or_the_named_exceptions() -> None:
    leaks: dict[str, set[str]] = {}
    for path in sorted(_CORE.rglob("*.py")):
        imported = _imported_synapse_modules(path.read_text(encoding="utf-8"))
        upward = {
            name
            for name in imported
            if not name.startswith(f"{_PACKAGE}.core.")
            and name != f"{_PACKAGE}.core"
            and name not in _ALLOWED_NON_CORE
        }
        if upward:
            leaks[str(path.relative_to(_CORE.parent.parent))] = upward
    assert not leaks, (
        "the coordination kernel reached up into feature layers — a split-blocking "
        f"import. Fix the import or, only for genuine kernel plumbing, add it to "
        f"_ALLOWED_NON_CORE with a reason:\n{leaks}"
    )


def test_the_allowed_exceptions_are_actually_used() -> None:
    """A named exception that no core module imports is stale — drop it."""
    every_import: set[str] = set()
    for path in _CORE.rglob("*.py"):
        every_import |= _imported_synapse_modules(path.read_text(encoding="utf-8"))
    stale = _ALLOWED_NON_CORE - every_import
    assert not stale, f"remove these unused kernel-boundary exceptions: {stale}"
