# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — policy contract for lightweight pre-push hooks
"""Keep ordinary pushes fast while enforcing metadata and history integrity."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".pre-commit-config.yaml"

_EXPECTED_HOOKS = {
    "pre-push-capability-manifest": "python tools/capability_manifest.py --check",
    "pre-push-commit-trailers": "python tools/check_commit_trailers.py",
    "pre-push-version-sync": "python tools/check_version_sync.py",
}
_PRE_COMMIT_ONLY_HOOKS = (
    "ruff",
    "ruff-format",
    "trailing-whitespace",
    "end-of-file-fixer",
    "check-yaml",
    "check-toml",
    "check-merge-conflict",
    "check-added-large-files",
    "typos",
    "gitleaks",
    "mypy-whole-tree",
    "version-sync",
)


def _hook_block(text: str, hook_id: str) -> str:
    """Return one local hook block from the pre-commit configuration."""
    return text.split(f"- id: {hook_id}", 1)[1].split("\n      - id:", 1)[0]


def test_pre_push_hooks_are_installed_and_fixed_scope() -> None:
    text = CONFIG.read_text(encoding="utf-8")

    assert "default_install_hook_types: [pre-commit, commit-msg, pre-push]" in text
    assert "default_stages: [pre-commit]" in text
    for hook_id, entry in _EXPECTED_HOOKS.items():
        block = _hook_block(text, hook_id)
        assert f"entry: {entry}" in block
        assert "language: system" in block
        assert "stages: [pre-push]" in block
        assert "always_run: true" in block
        assert "pass_filenames: false" in block
    assert text.count("stages: [pre-push]") == len(_EXPECTED_HOOKS)
    for hook_id in _PRE_COMMIT_ONLY_HOOKS:
        assert "stages: [pre-commit]" in _hook_block(text, hook_id)


def test_pre_push_hooks_cannot_smuggle_in_exhaustive_checks() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    blocks = " ".join(_hook_block(text, hook_id).lower() for hook_id in _EXPECTED_HOOKS)

    for forbidden in (
        "preflight.sh",
        "pytest",
        "--cov",
        "mypy",
        "mkdocs",
        "pip-audit",
        "pip_audit",
    ):
        assert forbidden not in blocks


def test_exhaustive_preflight_is_not_documented_as_an_ordinary_push_gate() -> None:
    preflight = (ROOT / "tools" / "preflight.sh").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "not the ordinary pre-push hook" in preflight
    assert "let CI own the exhaustive" in preflight
    assert "before EVERY push" not in preflight
    assert "lightweight pre-push hooks" in makefile
