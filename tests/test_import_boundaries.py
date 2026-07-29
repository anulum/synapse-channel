# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dependency-direction contracts for cycle-free shared layers
"""Pin the neutral modules that removed two deferred import inversions."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _top_level_imports(relative_path: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    return {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def test_git_path_identity_depends_on_neutral_runtime_not_gitclaim() -> None:
    imports = _top_level_imports("src/synapse_channel/git/path_identity.py")

    assert "synapse_channel.git.git_runtime" in imports
    assert "synapse_channel.git.gitclaim" not in imports


def test_hub_config_depends_on_neutral_defaults_not_hub_runtime() -> None:
    imports = _top_level_imports("src/synapse_channel/core/hub_config.py")

    assert "synapse_channel.core.hub_defaults" in imports
    assert "synapse_channel.core.hub" not in imports


def test_compatibility_imports_reexport_the_same_git_runtime_objects() -> None:
    from synapse_channel.git.git_runtime import (
        GitError as RuntimeGitError,
    )
    from synapse_channel.git.git_runtime import (
        GitRunner as RuntimeGitRunner,
    )
    from synapse_channel.git.git_runtime import (
        _default_git_runner as runtime_runner,
    )
    from synapse_channel.git.gitclaim import (
        GitError,
        GitRunner,
        _default_git_runner,
    )

    assert GitError is RuntimeGitError
    assert GitRunner is RuntimeGitRunner
    assert _default_git_runner is runtime_runner


def test_hub_keeps_reexporting_shared_default_constants() -> None:
    from synapse_channel.core import hub, hub_defaults

    names = (
        "DEFAULT_AUTH_TIMEOUT",
        "DEFAULT_MAX_CLIENTS",
        "DEFAULT_MAX_CONNECTIONS_PER_HOST",
        "DEFAULT_PING_INTERVAL",
        "DEFAULT_PING_TIMEOUT",
        "DEFAULT_TAKEOVER_COOLDOWN",
    )
    assert all(getattr(hub, name) == getattr(hub_defaults, name) for name in names)
