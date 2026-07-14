# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the relay CLI command

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from synapse_channel import cli, cli_streams
from synapse_channel.relay import append_jsonl, encode_lite


def _relay_ns(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "relay_log": "feed.ndjson",
        "since": 0,
        "cursor": None,
        "for_name": None,
        "project": None,
        "role": None,
        "channel": None,
        "public_only": False,
        "channel_metadata": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _lite_line(log: Path, payload: str, msg_id: int) -> None:
    append_jsonl(
        log,
        encode_lite(
            {
                "sender": "A",
                "target": "all",
                "type": "chat",
                "payload": payload,
                "timestamp": 2.0,
                "msg_id": msg_id,
            }
        ),
    )


def test_parser_relay() -> None:
    args = cli.build_parser().parse_args(
        ["relay", "feed.ndjson", "--since", "10", "--channel", "ops"]
    )
    assert args.relay_log == "feed.ndjson"
    assert args.since == 10
    assert args.channel == "ops"
    assert args.cursor is None
    assert args.func is cli_streams._cmd_relay


def test_format_relay_line_renders_envelope() -> None:
    line = cli_streams._format_relay_line(
        {"timestamp": 1.5, "sender": "A", "target": "B", "type": "chat", "payload": "hi"}
    )
    assert line == "[1.500] A -> B (chat): hi"


def test_format_relay_line_neutralises_terminal_controls() -> None:
    line = cli_streams._format_relay_line(
        {
            "timestamp": 1.5,
            "sender": "A\x1b]52;c;YQ==\x07",
            "target": "B\nspoof",
            "type": "chat",
            "payload": "one\rtwo\u202e",
        }
    )

    assert line == r"[1.500] A\x1b]52;c;YQ==\x07 -> B\nspoof (chat): one\rtwo\u202e"
    assert "\x1b" not in line
    assert "\x07" not in line
    assert "\u202e" not in line


def test_cmd_relay_prints_decoded_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "feed.ndjson"
    _lite_line(log, "hello", 1)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log))) == 0
    assert "A -> all (chat): hello" in capsys.readouterr().out


def test_cmd_relay_resumes_from_cursor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    cursor = tmp_path / "feed.cursor"
    _lite_line(log, "one", 1)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), cursor=str(cursor))) == 0
    assert "one" in capsys.readouterr().out

    _lite_line(log, "two", 2)
    # The persisted cursor means the second run shows only the newly appended line.
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), cursor=str(cursor))) == 0
    second = capsys.readouterr().out
    assert "two" in second
    assert "one" not in second


def test_cmd_relay_uses_since_offset(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    _lite_line(log, "skip", 1)
    offset = log.stat().st_size
    _lite_line(log, "keep", 2)
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), since=offset)) == 0
    out = capsys.readouterr().out
    assert "keep" in out
    assert "skip" not in out


def test_cmd_relay_filters_by_recipient(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    rows = [
        ("all", "chat", "everyone", 1),
        ("B,C", "chat", "you two", 2),
        ("C", "chat", "just C", 3),
        ("all", "presence_update", "noise", 4),
    ]
    for target, mtype, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "A",
                    "target": target,
                    "type": mtype,
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), for_name="B")) == 0
    out = capsys.readouterr().out
    assert "everyone" in out  # broadcast reaches everyone
    assert "you two" in out  # B is one of several named recipients
    assert "just C" not in out  # addressed only to C
    assert "noise" not in out  # non-chat presence is dropped in the inbox view


def test_parser_relay_accepts_roles() -> None:
    args = cli.build_parser().parse_args(
        ["relay", "feed.ndjson", "--for", "SC/claude"]
        + ["--role", "SC/coordinator", "--role", "SC/git"]
    )
    assert args.role == ["SC/coordinator", "SC/git"]


def test_cmd_relay_surfaces_role_addressed_messages(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A role the reader holds lands in its per-agent inbox alongside its instance name.
    log = tmp_path / "feed.ndjson"
    rows = [
        ("SC/claude", "to my name", 1),
        ("SC/coordinator", "to my role", 2),
        ("SC/git", "a role I do not hold", 3),
        ("SC/other", "someone else", 4),
    ]
    for target, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "peer",
                    "target": target,
                    "type": "chat",
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert (
        cli_streams._cmd_relay(
            _relay_ns(relay_log=str(log), for_name="SC/claude", role=["SC/coordinator"])
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "to my name" in out  # instance name
    assert "to my role" in out  # a role held
    assert "a role I do not hold" not in out
    assert "someone else" not in out


def test_cmd_relay_role_only_inbox_without_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # --role alone (no --for) still builds an inbox: broadcasts plus the held role.
    log = tmp_path / "feed.ndjson"
    rows = [
        ("all", "broadcast", 1),
        ("SC/coordinator", "role message", 2),
        ("SC/other", "not for me", 3),
    ]
    for target, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "peer",
                    "target": target,
                    "type": "chat",
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), role=["SC/coordinator"])) == 0
    out = capsys.readouterr().out
    assert "broadcast" in out
    assert "role message" in out
    assert "not for me" not in out


def test_cmd_relay_filters_by_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "feed.ndjson"
    rows = [
        ("all", "everyone", 1),
        ("quantum/worker-1", "to instance", 2),
        ("quantum/*", "to team", 3),
        ("other/worker-1", "elsewhere", 4),
    ]
    for target, payload, mid in rows:
        append_jsonl(
            log,
            encode_lite(
                {
                    "sender": "A",
                    "target": target,
                    "type": "chat",
                    "payload": payload,
                    "timestamp": 2.0,
                    "msg_id": mid,
                }
            ),
        )
    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), project="quantum")) == 0
    out = capsys.readouterr().out
    assert "everyone" in out
    assert "to instance" in out
    assert "to team" in out
    assert "elsewhere" not in out


def test_cmd_relay_filters_by_channel_and_public_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = tmp_path / "feed.ndjson"
    for channel, payload, mid in (
        ("", "public", 1),
        ("ops", "private ops", 2),
        ("qa", "private qa", 3),
    ):
        message = {
            "sender": "A",
            "target": "all",
            "type": "chat",
            "payload": payload,
            "timestamp": 2.0,
            "msg_id": mid,
        }
        if channel:
            message["channel"] = channel
        append_jsonl(log, encode_lite(message))

    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), channel="ops")) == 0
    channel_out = capsys.readouterr().out
    assert "private ops" in channel_out
    assert "private qa" not in channel_out
    assert "public" not in channel_out

    assert cli_streams._cmd_relay(_relay_ns(relay_log=str(log), public_only=True)) == 0
    public_out = capsys.readouterr().out
    assert "public" in public_out
    assert "private ops" not in public_out


def test_cmd_relay_channel_metadata_hides_body(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log = tmp_path / "feed.ndjson"
    append_jsonl(
        log,
        encode_lite(
            {
                "sender": "A",
                "target": "all",
                "type": "chat",
                "payload": "operator body",
                "timestamp": 2.0,
                "msg_id": 1,
                "channel": "ops",
            }
        ),
    )

    assert (
        cli_streams._cmd_relay(_relay_ns(relay_log=str(log), channel="ops", channel_metadata=True))
        == 0
    )

    out = capsys.readouterr().out
    assert "channel=ops" in out
    assert "operator body" not in out
    assert "<private channel body hidden>" in out
