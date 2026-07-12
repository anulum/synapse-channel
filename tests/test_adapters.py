# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-agent adapter catalogue and planning regressions

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from synapse_channel.adapters import (
    APPEND_MODE,
    FILE_MODE,
    MARKER_BEGIN,
    MARKER_END,
    contains_block,
    detect_installed,
    plan_install,
    plan_uninstall,
    render_block,
    resolve_target,
    strip_block,
    tool_for,
)

_CLAUDE = tool_for("claude-code")  # html, file, home scope
_AIDER = tool_for("aider")  # html, append, project scope
_WINDSURF = tool_for("windsurf")  # hash, append, project scope
_KIMI = tool_for("kimi")  # skill, file, KIMI_CODE_HOME scope
_KIMI_PROJECT = tool_for("kimi-project")  # skill, file, project scope, explicit-only


def test_tool_for_resolves_and_rejects() -> None:
    assert tool_for("  Cursor ").key == "cursor"
    with pytest.raises(KeyError):
        tool_for("nope")


def test_detect_installed_by_binary_then_by_config_dir(tmp_path: Path) -> None:
    # binary on PATH wins regardless of config dir
    assert detect_installed(_CLAUDE, home=tmp_path, which=lambda _b: "/usr/bin/claude")
    # no binary, but the config dir exists
    (tmp_path / ".claude").mkdir()
    assert detect_installed(_CLAUDE, home=tmp_path, which=lambda _b: None)
    # neither: aider has no config-dir signal and no binary
    assert not detect_installed(_AIDER, home=tmp_path, which=lambda _b: None)
    # explicit-only project variant is never auto-detected
    assert not detect_installed(_KIMI_PROJECT, home=tmp_path, which=lambda _b: "/usr/bin/kimi")


def test_resolve_target_honours_scope(tmp_path: Path) -> None:
    home = tmp_path / "home"
    project = tmp_path / "proj"
    assert resolve_target(_CLAUDE, home=home, project=project) == home / ".claude/synapse.md"
    assert resolve_target(_AIDER, home=home, project=project) == project / "CONVENTIONS.md"
    expected_kimi = home / ".kimi-code" / "skills" / "synapse" / "SKILL.md"
    assert resolve_target(_KIMI, home=home, project=project) == expected_kimi
    expected_kimi_project = project / ".kimi-code" / "skills" / "synapse" / "SKILL.md"
    assert resolve_target(_KIMI_PROJECT, home=home, project=project) == expected_kimi_project


def test_resolve_target_rejects_unknown_scope(tmp_path: Path) -> None:
    invalid = replace(_CLAUDE, scope="unknown")
    with pytest.raises(ValueError, match="unknown adapter scope"):
        resolve_target(invalid, home=tmp_path, project=tmp_path)


def test_kimi_target_honours_kimi_code_home(tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-kimi"
    target = resolve_target(
        _KIMI,
        home=tmp_path / "home",
        project=tmp_path / "project",
        environ={"KIMI_CODE_HOME": str(custom_home)},
    )
    assert target == custom_home / "skills" / "synapse" / "SKILL.md"


def test_kimi_detection_honours_kimi_code_home(tmp_path: Path) -> None:
    custom_home = tmp_path / "custom-kimi"
    custom_home.mkdir()
    assert detect_installed(
        _KIMI,
        home=tmp_path / "home",
        which=lambda _binary: None,
        environ={"KIMI_CODE_HOME": str(custom_home)},
    )


def test_render_block_html_and_hash_styles_carry_the_contract() -> None:
    html = render_block(_AIDER, identity="proj/agent", hub_uri="ws://h:1")
    assert html.startswith(f"<!-- {MARKER_BEGIN} -->")
    assert html.rstrip().endswith(f"<!-- {MARKER_END} -->")
    assert "proj/agent" in html and "ws://h:1" in html and "Claim before edit" in html

    hashed = render_block(_WINDSURF, identity="x", hub_uri="ws://y:2")
    assert hashed.startswith(f"# {MARKER_BEGIN}")
    assert hashed.rstrip().endswith(f"# {MARKER_END}")


def test_render_block_skill_style_has_frontmatter_and_contract() -> None:
    skill = render_block(_KIMI, identity="hub/agent", hub_uri="ws://h:9")
    assert skill.startswith("---\n")
    assert "name: synapse" in skill
    assert "type: prompt" in skill
    assert f"<!-- {MARKER_BEGIN} -->" in skill
    assert skill.rstrip().endswith(f"<!-- {MARKER_END} -->")
    assert "hub/agent" in skill and "ws://h:9" in skill and "Claim before edit" in skill


def test_contains_block_detects_presence() -> None:
    assert contains_block(render_block(_CLAUDE, identity="a", hub_uri="b"))
    assert not contains_block("just some notes\n")


def test_strip_block_removes_block_and_collapses_padding() -> None:
    block = render_block(_AIDER, identity="a", hub_uri="b")
    text = f"My rules.\n\n{block}"
    assert strip_block(text) == "My rules.\n"
    # a block that is the whole file strips to empty
    assert strip_block(block) == ""
    # no block: returned unchanged
    assert strip_block("nothing here\n") == "nothing here\n"


def test_strip_block_keeps_content_on_both_sides_of_the_block() -> None:
    block = render_block(_AIDER, identity="a", hub_uri="b")
    text = f"Header line.\n\n{block}\nFooter line.\n"
    stripped = strip_block(text)
    assert MARKER_BEGIN not in stripped
    assert stripped.startswith("Header line.")
    assert stripped.rstrip().endswith("Footer line.")


def test_strip_block_collapses_leading_padding_when_block_is_first() -> None:
    block = render_block(_AIDER, identity="a", hub_uri="b")
    text = f"{block}\n\nFooter line.\n"
    assert strip_block(text) == "Footer line.\n"


def test_strip_block_ignores_a_dangling_marker_order() -> None:
    # END before BEGIN is not a valid block; leave the text untouched
    text = f"# {MARKER_END}\nbody\n# {MARKER_BEGIN}\n"
    assert strip_block(text) == text


def test_plan_install_file_mode_owns_the_whole_file() -> None:
    block = render_block(_CLAUDE, identity="a", hub_uri="b")
    assert plan_install("anything prior", block, mode=FILE_MODE) == block
    assert plan_install(None, block, mode=FILE_MODE) == block


def test_plan_install_append_mode_is_idempotent() -> None:
    block = render_block(_AIDER, identity="a", hub_uri="b")
    # empty/absent base -> just the block
    assert plan_install(None, block, mode=APPEND_MODE) == block
    assert plan_install("   \n", block, mode=APPEND_MODE) == block
    # base preserved, block appended once
    once = plan_install("My rules.\n", block, mode=APPEND_MODE)
    assert once == f"My rules.\n\n{block}"
    # re-install replaces rather than duplicating
    twice = plan_install(once, block, mode=APPEND_MODE)
    assert twice == once
    assert twice.count(MARKER_BEGIN) == 1


def test_plan_uninstall_deletes_file_mode_and_strips_append_mode() -> None:
    block = render_block(_AIDER, identity="a", hub_uri="b")
    assert plan_uninstall("whatever", mode=FILE_MODE) is None
    assert plan_uninstall(f"My rules.\n\n{block}", mode=APPEND_MODE) == "My rules.\n"
