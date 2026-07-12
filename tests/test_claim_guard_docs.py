# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — provider claim-hook documentation regressions

from pathlib import Path


def test_provider_claim_hook_guide_is_discoverable_and_honest() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    nav = Path("mkdocs.yml").read_text(encoding="utf-8")
    guide = Path("docs/claim-guard-hooks.md").read_text(encoding="utf-8")

    assert "docs/claim-guard-hooks.md" in readme
    assert "claim-guard-hooks.md" in nav
    for command in (
        "claude-claim-hook",
        "codex-claim-hook",
        "gemini-claim-hook",
        "kimi-claim-hook",
    ):
        assert command in readme
        assert command in guide
    assert "guardrail rather than" in guide
    assert "complete enforcement boundary" in guide
    assert "hook runner itself as fail-open" in guide
    assert "BeforeTool" in guide
    assert '{"decision": "deny", "reason": …}' in guide
    assert "milliseconds" in guide
    assert "--install-config" in guide
    assert "--uninstall-config" in guide
    assert "$KIMI_CODE_HOME/config.toml" in guide
    assert "final-component symlink" in guide
    assert "not complete Bash or filesystem isolation" in readme
    assert "git-claim-check --staged" in guide
