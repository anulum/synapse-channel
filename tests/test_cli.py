# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the unified command-line entry point (parser, dispatch, token)

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synapse_channel import cli


@contextmanager
def _env_var(name: str, value: str | None) -> Iterator[None]:
    previous = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


# --- main dispatch -----------------------------------------------------------


def test_main_without_command_prints_help() -> None:
    assert cli.main([]) == 1


def test_main_version_exits(capsys: pytest.CaptureFixture[str]) -> None:
    with _env_var("SYNAPSE_NO_UPDATE_CHECK", "1"), pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "synapse-channel" in capsys.readouterr().out


def test_main_version_prints_update_notice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "synapse-channel" / "update-check.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"checked_at": 9_999_999_999.0, "latest": "9.9.9"}))
    with (
        _env_var("XDG_CACHE_HOME", str(tmp_path)),
        _env_var("SYNAPSE_UPDATE_CHECK", "1"),
        _env_var("SYNAPSE_NO_UPDATE_CHECK", None),
        pytest.raises(SystemExit),
    ):
        cli.main(["--version"])
    captured = capsys.readouterr()
    assert "synapse-channel" in captured.out
    assert "9.9.9 is available" in captured.err  # the notice goes to stderr


def test_main_version_skips_update_notice_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "synapse-channel" / "update-check.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps({"checked_at": 9_999_999_999.0, "latest": "9.9.9"}))
    with (
        _env_var("XDG_CACHE_HOME", str(tmp_path)),
        _env_var("SYNAPSE_UPDATE_CHECK", None),
        _env_var("SYNAPSE_NO_UPDATE_CHECK", None),
        pytest.raises(SystemExit),
    ):
        cli.main(["--version"])
    captured = capsys.readouterr()
    assert "synapse-channel" in captured.out
    assert captured.err == ""


# --- token parsing across commands -------------------------------------------


def test_parser_token_options() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["hub", "--token", "h"]).token == "h"
    assert parser.parse_args(["worker", "--token", "w"]).token == "w"
    assert parser.parse_args(["send", "msg", "--token", "s"]).token == "s"
    assert parser.parse_args(["listen", "--token", "l"]).token == "l"
    assert parser.parse_args(["board", "--token", "b"]).token == "b"


def test_parser_adds_token_file_to_token_commands() -> None:
    args = cli.build_parser().parse_args(["send", "hi", "--token-file", "/x"])
    assert args.token_file == "/x"


# --- relay parser flags ------------------------------------------------------


def test_parser_relay_for_flag() -> None:
    relay = cli.build_parser().parse_args(["relay", "feed.ndjson", "--for", "B"])
    assert relay.for_name == "B"


def test_parser_relay_project() -> None:
    args = cli.build_parser().parse_args(["relay", "feed.ndjson", "--project", "quantum"])
    assert args.project == "quantum"


# --- token via cli / file / env ----------------------------------------------


def test_resolve_token_prefers_cli() -> None:
    assert cli._resolve_token(argparse.Namespace(token="cli", token_file=None)) == "cli"


def test_resolve_token_from_file(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok\n", encoding="utf-8")
    assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_from_env() -> None:
    with _env_var("SYNAPSE_TOKEN", "env-tok"):
        assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) == "env-tok"


def test_resolve_token_precedence(tmp_path: Path) -> None:
    f = tmp_path / "tok"
    f.write_text("file-tok", encoding="utf-8")
    with _env_var("SYNAPSE_TOKEN", "env-tok"):
        assert cli._resolve_token(argparse.Namespace(token="cli", token_file=str(f))) == "cli"
        assert cli._resolve_token(argparse.Namespace(token=None, token_file=str(f))) == "file-tok"


def test_resolve_token_none() -> None:
    with _env_var("SYNAPSE_TOKEN", None):
        assert cli._resolve_token(argparse.Namespace(token=None, token_file=None)) is None


def test_resolve_token_missing_file(tmp_path: Path) -> None:
    ns = argparse.Namespace(token=None, token_file=str(tmp_path / "nope"))
    with pytest.raises(FileNotFoundError):
        cli._resolve_token(ns)


def test_resolve_token_no_token_file_attr() -> None:
    with _env_var("SYNAPSE_TOKEN", "env-tok"):
        assert cli._resolve_token(argparse.Namespace(token=None)) == "env-tok"
