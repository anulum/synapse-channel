# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for CLI connect-token resolution
"""Exercise ``cli._resolve_token`` against the shared secret-file floor."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from synapse_channel.cli import TOKEN_ENV, _resolve_token
from synapse_channel.core.secret_files import SecretFileError


def _ns(**kwargs: object) -> argparse.Namespace:
    base: dict[str, object] = {"token": None, "token_file": None}
    base.update(kwargs)
    return argparse.Namespace(**base)


def _owner_only(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_explicit_token_wins_over_file_and_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = _owner_only(tmp_path / "t", "file-token\n")
    monkeypatch.setenv(TOKEN_ENV, "env-token")
    assert _resolve_token(_ns(token="argv-token", token_file=str(token_file))) == "argv-token"


def test_token_file_uses_owner_only_secret_floor(tmp_path: Path) -> None:
    token_file = _owner_only(tmp_path / "t", "  file-token\n")
    assert _resolve_token(_ns(token_file=str(token_file))) == "file-token"


def test_token_file_refuses_world_readable_without_content(tmp_path: Path) -> None:
    token_file = tmp_path / "t"
    token_file.write_text("must-not-leak\n", encoding="utf-8")
    token_file.chmod(0o644)
    with pytest.raises(SecretFileError, match="chmod 600") as excinfo:
        _resolve_token(_ns(token_file=str(token_file)))
    assert "must-not-leak" not in str(excinfo.value)


def test_env_fallback_when_no_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TOKEN_ENV, "env-only")
    assert _resolve_token(_ns()) == "env-only"


def test_none_when_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert _resolve_token(_ns()) is None
