# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — active-waker configuration tests

from __future__ import annotations

import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.waker_config import (
    CONFIG_SCHEMA_VERSION,
    DESIRED_INHIBITED,
    WakerConfig,
    WakerConfigError,
    clean_waker_text,
    load_waker_config,
    save_waker_config,
    validate_waker_config,
    waker_config_dir,
    waker_config_path,
)


def _config(tmp_path: Path, **changes: Any) -> WakerConfig:
    values: dict[str, Any] = {
        "identity": "repo/codex-1",
        "session": "repo-codex-1",
        "cwd": str(tmp_path.resolve()),
        "agent_command": ("codex", "--model", "gpt-5"),
        "token_file": str((tmp_path / "token").resolve()),
        "registry_dir": str((tmp_path / "registry").resolve()),
        "submit_delay": 0.0,
        "pane_probe_interval": 5.0,
        "updated_at": 1.0,
    }
    values.update(changes)
    return WakerConfig(**values)


def test_owner_only_configuration_round_trip_builds_transport(tmp_path: Path) -> None:
    config = _config(tmp_path)

    path = save_waker_config(config, home=tmp_path)
    loaded = load_waker_config(config.identity, home=tmp_path)
    transport = loaded.agent_tmux_config()

    assert loaded == config
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert transport.identity == config.identity
    assert transport.agent_command == config.agent_command
    assert transport.token is None
    assert transport.token_file == Path(config.token_file or "")
    assert transport.registry_dir == Path(config.registry_dir)
    assert waker_config_dir(home=tmp_path) == path.parent
    assert path.name == waker_config_path(config.identity, home=tmp_path).name
    assert waker_config_path("repo/a_b", home=tmp_path) != waker_config_path(
        "repo/a/b", home=tmp_path
    )


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"schema_version": 2}, "unsupported waker configuration schema"),
        ({"identity": ""}, "identity must not be empty"),
        ({"session": "bad\nvalue"}, "session contains control"),
        ({"cwd": "relative"}, "cwd must be an absolute path"),
        ({"agent_command": ()}, "agent_command must contain"),
        ({"agent_command": ("codex", "")}, "agent_command token must not be empty"),
        ({"tmux_bin": 3}, "tmux_bin must be a string"),
        ({"synapse_bin": ""}, "synapse_bin must not be empty"),
        ({"uri": "\u202eunsafe"}, "uri contains control or bidi"),
        ({"token_file": "relative"}, "token_file must be an absolute path"),
        ({"registry_dir": "relative"}, "registry_dir must be an absolute path"),
        ({"submit_delay": True}, "submit_delay must be a number"),
        ({"submit_delay": float("inf")}, "submit_delay must be finite and non-negative"),
        ({"pane_probe_interval": 0.0}, "pane_probe_interval must be finite and positive"),
        ({"pane_probe_interval": 61.0}, "pane_probe_interval must not exceed 60 seconds"),
        ({"desired_state": "stopped"}, "desired_state must be armed or inhibited"),
        ({"generation": True}, "generation must be an integer"),
        ({"generation": 0}, "generation must be positive"),
        (
            {"desired_state": DESIRED_INHIBITED, "inhibit_reason": ""},
            "inhibit_reason must not be empty",
        ),
        ({"updated_at": -1.0}, "updated_at must be finite and non-negative"),
    ],
)
def test_validation_rejects_ambiguous_or_unbounded_fields(
    tmp_path: Path, changes: dict[str, Any], match: str
) -> None:
    with pytest.raises(WakerConfigError, match=match):
        validate_waker_config(_config(tmp_path, **changes))


def test_clean_text_rejects_non_string_and_nul() -> None:
    with pytest.raises(WakerConfigError, match="field must be a string"):
        clean_waker_text(1, field="field")
    with pytest.raises(WakerConfigError, match="field contains control"):
        clean_waker_text("bad\x00value", field="field")


def _write_document(tmp_path: Path, identity: str, document: object) -> Path:
    path = waker_config_path(identity, home=tmp_path)
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda _document: [], "must be a JSON object"),
        (
            lambda document: {key: value for key, value in document.items() if key != "uri"},
            "missing=uri",
        ),
        (lambda document: {**document, "extra": True}, "unknown=extra"),
        (lambda document: {**document, "agent_command": "codex"}, "array of strings"),
        (lambda document: {**document, "agent_command": ["codex", 3]}, "array of strings"),
        (lambda document: {**document, "generation": "one"}, "generation must be an integer"),
    ],
)
def test_loader_rejects_schema_and_type_drift(tmp_path: Path, mutation: Any, match: str) -> None:
    identity = "repo/codex-1"
    document = _config(tmp_path).to_document()
    _write_document(tmp_path, identity, mutation(document))

    with pytest.raises(WakerConfigError, match=match):
        load_waker_config(identity, home=tmp_path)


def test_loader_rejects_invalid_json_permissions_and_identity(tmp_path: Path) -> None:
    identity = "repo/codex-1"
    path = _write_document(tmp_path, identity, {"not": "json"})
    path.write_text("{", encoding="utf-8")
    with pytest.raises(WakerConfigError, match="Expecting property name"):
        load_waker_config(identity, home=tmp_path)

    path.write_text(json.dumps(_config(tmp_path).to_document()), encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(WakerConfigError, match="accessible by other users"):
        load_waker_config(identity, home=tmp_path)

    path.chmod(0o600)
    wrong = replace(_config(tmp_path), identity="repo/other")
    path.write_text(json.dumps(wrong.to_document()), encoding="utf-8")
    with pytest.raises(WakerConfigError, match="does not match"):
        load_waker_config(identity, home=tmp_path)


def test_save_removes_temporary_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        save_waker_config(_config(tmp_path), home=tmp_path)
    assert list(waker_config_dir(home=tmp_path).iterdir()) == []


def test_schema_version_constant_is_persisted(tmp_path: Path) -> None:
    path = save_waker_config(_config(tmp_path), home=tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == CONFIG_SCHEMA_VERSION
