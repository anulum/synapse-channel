# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — exact-identity ergonomic inbox regressions

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from synapse_channel import ergonomics, ergonomics_inbox
from synapse_channel.relay import append_jsonl, encode_lite


def _capture_dispatch(
    calls: list[list[str]],
) -> Callable[[list[str] | None], int]:
    def dispatch(argv: Sequence[str] | None) -> int:
        calls.append(list(argv or ()))
        return 0

    return dispatch


def _env(home: Path, identity: str) -> dict[str, str]:
    return {
        "HOME": str(home),
        "SYN_HOME": str(home / "synapse"),
        "SYN_IDENTITY": identity,
        "SYN_PROJECT": identity.split("/", 1)[0],
    }


def _append_chat(feed: Path, *, msg_id: int, target: str, payload: str) -> None:
    append_jsonl(
        feed,
        encode_lite(
            {
                "hub_id": "hub-test",
                "msg_id": msg_id,
                "payload": payload,
                "sender": "sender",
                "target": target,
                "timestamp": float(msg_id),
                "type": "chat",
            }
        ),
    )


def test_default_inbox_uses_exact_identity_and_identity_cursor(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    code = ergonomics.main(
        ["inbox"],
        env=_env(tmp_path, "user/terminal-14753"),
        cwd_basename="user",
        dispatcher=_capture_dispatch(calls),
    )

    assert code == 0
    assert calls == [
        [
            "relay",
            str(tmp_path / "synapse" / "feed.ndjson"),
            "--for",
            "user/terminal-14753",
            "--cursor",
            str(tmp_path / "synapse" / "user__terminal-14753.cursor"),
        ]
    ]


def test_project_wide_inbox_requires_an_explicit_flag(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    code = ergonomics.main(
        ["inbox", "--project-wide"],
        env=_env(tmp_path, "user/terminal-14753"),
        cwd_basename="user",
        dispatcher=_capture_dispatch(calls),
    )

    assert code == 0
    assert calls[0][2:] == [
        "--project",
        "user",
        "--cursor",
        str(tmp_path / "synapse" / "user.cursor"),
    ]


def test_inbox_name_override_is_exact_and_unknown_options_fail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []
    dispatch = _capture_dispatch(calls)
    env = _env(tmp_path, "user/terminal-14753")

    assert (
        ergonomics.main(
            ["inbox", "--name", "user/terminal-23696"],
            env=env,
            cwd_basename="user",
            dispatcher=dispatch,
        )
        == 0
    )
    assert calls[0][2:] == [
        "--for",
        "user/terminal-23696",
        "--cursor",
        str(tmp_path / "synapse" / "user__terminal-23696.cursor"),
    ]

    assert (
        ergonomics.main(
            ["inbox", "--name", "user/terminal-23696", "--project-wide"],
            env=env,
            cwd_basename="user",
            dispatcher=dispatch,
        )
        == 2
    )
    assert "mutually exclusive" in capsys.readouterr().err
    assert len(calls) == 1


def test_inbox_option_parser_supports_equals_forms_and_deduplicates_aliases() -> None:
    options = ergonomics_inbox.parse_inbox_options(
        ["--name=user/terminal-23696", "--as=user/coordinator", "--as=user/coordinator"],
        {},
    )
    env_options = ergonomics_inbox.parse_inbox_options(
        [],
        {"SYN_ALIASES": "user/coordinator, ,user/coordinator"},
    )

    assert options == ergonomics_inbox.InboxOptions(
        exact_name="user/terminal-23696",
        project_wide=False,
        aliases=("user/coordinator",),
    )
    assert env_options.aliases == ("user/coordinator",)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--as"], "--as requires"),
        (["--name", "--project-wide"], "--name requires"),
        (["--as", "  "], "--as requires"),
        (["--name", "one", "--name", "two"], "only once"),
        (["--as="], "--as requires"),
        (["--name=one", "--name=two"], "only once"),
        (["--unknown"], "unsupported inbox option"),
    ],
)
def test_inbox_option_parser_rejects_ambiguous_or_malformed_input(
    argv: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ergonomics_inbox.parse_inbox_options(argv, {})


def test_exact_cursors_do_not_display_or_consume_another_terminal_mail(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feed = tmp_path / "synapse" / "feed.ndjson"
    _append_chat(feed, msg_id=1, target="user/terminal-23696", payload="private-23696")
    _append_chat(feed, msg_id=2, target="user/terminal-14753", payload="private-14753")

    assert (
        ergonomics.main(
            ["inbox"],
            env=_env(tmp_path, "user/terminal-14753"),
            cwd_basename="user",
        )
        == 0
    )
    first = capsys.readouterr().out
    assert "private-14753" in first
    assert "private-23696" not in first

    assert (
        ergonomics.main(
            ["inbox"],
            env=_env(tmp_path, "user/terminal-23696"),
            cwd_basename="user",
        )
        == 0
    )
    second = capsys.readouterr().out
    assert "private-23696" in second
    assert "private-14753" not in second
    assert (tmp_path / "synapse" / "user__terminal-14753.cursor").exists()
    assert (tmp_path / "synapse" / "user__terminal-23696.cursor").exists()


def test_inbox_helpers_have_a_focused_owner_and_compatibility_reexports() -> None:
    assert ergonomics.inbox_argv is ergonomics_inbox.inbox_argv
    assert ergonomics.split_as_names is ergonomics_inbox.split_as_names
    assert ergonomics.aliased_inbox_argv is ergonomics_inbox.aliased_inbox_argv
