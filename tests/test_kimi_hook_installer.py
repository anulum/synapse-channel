# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pure planning for Kimi hook block install/uninstall

from __future__ import annotations

import sys
from pathlib import Path

from synapse_channel.kimi_hook_installer import (
    KIMI_HOOK_MARKER_BEGIN,
    KIMI_HOOK_MARKER_END,
    contains_hook_block,
    plan_install_hook,
    plan_uninstall_hook,
    render_hook_config,
    render_marked_hook_block,
)


def test_render_hook_config_is_valid_toml_and_token_safe(tmp_path: Path) -> None:
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - exercised only on Python 3.10
        import tomli as tomllib

    token_file = tmp_path / "hub.token"
    token_file.write_text("secret-token", encoding="utf-8")
    rendered = render_hook_config(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=str(token_file),
        synapse_bin=sys.executable,
    )
    config = tomllib.loads(rendered)
    hook = config["hooks"][0]
    assert hook["event"] == "PreToolUse"
    assert hook["matcher"] == "^(Write|Edit)$"
    assert "secret-token" not in rendered
    assert str(token_file.resolve()) in rendered


def test_render_marked_hook_block_wraps_config_with_markers() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    assert block.startswith(f"# {KIMI_HOOK_MARKER_BEGIN}\n")
    assert block.rstrip().endswith(f"# {KIMI_HOOK_MARKER_END}")
    assert "[[hooks]]" in block


def test_contains_hook_block_detects_markers() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    assert contains_hook_block(f"foo\n\n{block}")
    assert not contains_hook_block("no markers here")


def test_plan_install_hook_appends_block_to_existing_config() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    existing = 'default_model = "kimi-code/kimi-for-coding"\n'
    result = plan_install_hook(existing, block)
    assert result.startswith(existing.rstrip())
    assert result.endswith(block)
    assert result.count(KIMI_HOOK_MARKER_BEGIN) == 1


def test_plan_install_hook_is_idempotent() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    once = plan_install_hook('model = "x"\n', block)
    twice = plan_install_hook(once, block)
    assert twice == once
    assert twice.count(KIMI_HOOK_MARKER_BEGIN) == 1


def test_plan_install_hook_owns_empty_file() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    assert plan_install_hook(None, block) == block
    assert plan_install_hook("   \n", block) == block


def test_plan_uninstall_hook_removes_block_and_preserves_rest() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    existing = f'model = "x"\n\n{block}\nmore = 1\n'
    result = plan_uninstall_hook(existing)
    assert KIMI_HOOK_MARKER_BEGIN not in result
    assert 'model = "x"' in result
    assert "more = 1" in result


def test_plan_uninstall_hook_returns_empty_when_only_block() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    assert plan_uninstall_hook(block) == ""


def test_plan_uninstall_without_markers_returns_input_unchanged() -> None:
    existing = 'model = "x"\n'
    assert plan_uninstall_hook(existing) == existing


def test_plan_uninstall_strips_padding_before_remaining_content() -> None:
    block = render_marked_hook_block(
        identity="seat/one",
        uri="ws://hub",
        ready_timeout=2.0,
        token_file=None,
        synapse_bin=sys.executable,
    )
    assert plan_uninstall_hook(f'{block}\n\nmodel = "x"\n') == 'model = "x"\n'
