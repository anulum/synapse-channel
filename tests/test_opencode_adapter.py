# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import json
from pathlib import Path

import pytest

from synapse_channel.opencode_adapter import (
    OpenCodeAdapterError,
    build_mcp_entry,
    plan_config_install,
    plan_config_uninstall,
    plan_plugin_install,
    plan_plugin_uninstall,
    plugin_is_owned,
    resolve_opencode_paths,
)
from synapse_channel.opencode_plugin import render_opencode_plugin


def _entry() -> dict[str, object]:
    return build_mcp_entry(
        synapse_bin="/bin/synapse",
        identity="seat/one",
        uri="ws://127.0.0.1:8876",
        token_file="/run/private/token",
    )


def test_project_and_global_paths_are_responsibility_split(tmp_path: Path) -> None:
    project = resolve_opencode_paths(
        scope="project", project=tmp_path / "repo", home=tmp_path / "home"
    )
    global_paths = resolve_opencode_paths(
        scope="global",
        project=tmp_path / "repo",
        home=tmp_path / "home",
        config_root=tmp_path / "cfg",
    )
    assert project.config == (tmp_path / "repo" / ".opencode" / "opencode.json").resolve()
    assert (
        global_paths.plugin
        == (tmp_path / "cfg" / "opencode" / "plugins" / "synapse-claim-guard.js").resolve()
    )


def test_global_path_uses_xdg_or_home_and_invalid_scope_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    from_xdg = resolve_opencode_paths(scope="global", project=tmp_path, home=tmp_path / "home")
    assert from_xdg.config == (tmp_path / "xdg" / "opencode" / "opencode.json").resolve()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    from_home = resolve_opencode_paths(scope="global", project=tmp_path, home=tmp_path / "home")
    assert (
        from_home.config == (tmp_path / "home" / ".config" / "opencode" / "opencode.json").resolve()
    )
    with pytest.raises(OpenCodeAdapterError, match="scope"):
        resolve_opencode_paths(scope="other", project=tmp_path, home=tmp_path)


def test_config_install_preserves_user_fields_and_is_idempotent() -> None:
    first = plan_config_install('{"theme":"dark"}', _entry())
    second = plan_config_install(first, _entry())
    decoded = json.loads(first)
    assert first == second
    assert decoded["theme"] == "dark"
    assert decoded["mcp"]["synapse"]["command"][-2:] == ["--token-file", "/run/private/token"]
    assert "secret" not in first


def test_uninstall_removes_only_owned_entry() -> None:
    installed = plan_config_install('{"mcp":{"other":{"type":"remote"}}}', _entry())
    removed = plan_config_uninstall(installed)
    assert removed is not None
    assert json.loads(removed) == {"mcp": {"other": {"type": "remote"}}}


def test_empty_owned_config_uninstalls_file_and_absent_entry_is_unchanged() -> None:
    installed = plan_config_install("", _entry())
    assert plan_config_uninstall(installed) is None
    assert plan_config_uninstall("") is None
    untouched = '{"mcp":{"other":{}}}'
    assert plan_config_uninstall(untouched) == untouched


@pytest.mark.parametrize(
    "existing",
    [
        '{"mcp":{"synapse":{"type":"remote"}}}',
        '{"mcp":[]}',
        "{ // jsonc\n}",
        "[]",
    ],
)
def test_config_refuses_unowned_or_ambiguous_surfaces(existing: str) -> None:
    with pytest.raises(OpenCodeAdapterError):
        plan_config_install(existing, _entry())


def test_plugin_planner_never_replaces_or_removes_unowned_file() -> None:
    rendered = render_opencode_plugin(hook_argv=("synapse",), timeout_seconds=5)
    assert plugin_is_owned(plan_plugin_install("", rendered))
    with pytest.raises(OpenCodeAdapterError):
        plan_plugin_install("export const user = 1;", rendered)
    with pytest.raises(OpenCodeAdapterError):
        plan_plugin_uninstall("export const user = 1;")
    plan_plugin_uninstall(rendered)
    with pytest.raises(OpenCodeAdapterError, match="ownership marker"):
        plan_plugin_install("", "export const invalid = 1;")


@pytest.mark.parametrize(
    ("synapse_bin", "identity", "uri", "timeout_ms"),
    [
        ("", "seat", "ws://x", 1),
        ("syn", "", "ws://x", 1),
        ("syn", "seat", "", 1),
        ("syn", "seat", "ws://x", 0),
    ],
)
def test_mcp_entry_validates_required_fields(
    synapse_bin: str, identity: str, uri: str, timeout_ms: int
) -> None:
    with pytest.raises(OpenCodeAdapterError):
        build_mcp_entry(
            synapse_bin=synapse_bin,
            identity=identity,
            uri=uri,
            timeout_ms=timeout_ms,
            token_file=None,
        )
