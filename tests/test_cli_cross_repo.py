# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-repo CLI command regressions

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from synapse_channel.cli import build_parser, main
from test_cross_repo_graph import _org, _seed_claims


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_human_report_over_a_real_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _org(tmp_path)
    code, out, err = _run(["cross-repo", str(root)], capsys)
    assert code == 0
    assert err == ""
    assert out.startswith("Cross-repository dependency graph:")
    assert "consumer -[dependency]-> provider:" in out


def test_json_report_parses_and_carries_the_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    code, out, _ = _run(["cross-repo", str(root), "--json"], capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["note"] == "declaration-level dependency evidence; advisory, not enforcement"
    assert [node["repo"] for node in payload["nodes"]] == ["consumer", "island", "provider"]


def test_dot_report_is_a_digraph(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _org(tmp_path)
    code, out, _ = _run(["cross-repo", str(root), "--dot"], capsys)
    assert code == 0
    assert out.startswith("digraph cross_repo {")
    assert '"consumer" -> "provider" [label="dependency"];' in out


def test_focus_with_connected_live_claim_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    db = tmp_path / "events.db"
    _seed_claims(db)
    code, out, _ = _run(["cross-repo", str(root), "--db", str(db), "--repo", "consumer"], capsys)
    assert code == 1
    assert "provider [depends_on]" in out


def test_focus_with_only_self_claims_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The island repository has no dependency edges, so its own claim is the
    # only signal — informational, not a cross-repository coordination alarm.
    root = _org(tmp_path)
    db = tmp_path / "events.db"
    _seed_claims(db)
    code, out, _ = _run(["cross-repo", str(root), "--db", str(db), "--repo", "island"], capsys)
    assert code == 0
    assert "Live claims" not in out


def test_without_focus_claims_are_informational_and_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    db = tmp_path / "events.db"
    _seed_claims(db)
    code, out, _ = _run(["cross-repo", str(root), "--db", str(db)], capsys)
    assert code == 0
    assert "consumer [self] CONS-1@agent-b" in out


def test_missing_root_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["cross-repo", str(tmp_path / "absent")], capsys)
    assert code == 2
    assert out == ""
    assert "missing repository root" in err


def test_unknown_focus_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _org(tmp_path)
    code, _, err = _run(["cross-repo", str(root), "--repo", "nonesuch"], capsys)
    assert code == 2
    assert "unknown repository: nonesuch" in err


def _conflicting_root(tmp_path: Path) -> Path:
    """Two repositories pin shared-dep to provably disjoint ranges, one reconciles."""
    root = tmp_path / "conflict-org"
    root.mkdir()
    for repo, constraint in (
        ("alpha", "shared-dep>=4.1,<5"),
        ("beta", "shared-dep==2.9"),
        ("gamma", "shared-dep>=4.2,<4.6"),
    ):
        directory = root / repo
        directory.mkdir()
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{repo}-pkg"\ndependencies = ["{constraint}"]\n',
            encoding="utf-8",
        )
    return root


def test_suggest_resolution_names_the_outlier_in_the_human_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _conflicting_root(tmp_path)

    code, out, _ = _run(["cross-repo", str(root), "--suggest-resolution"], capsys)

    assert code == 0
    assert "## Suggested resolutions (1 conflicting package(s))" in out
    assert "ODD ONE OUT: beta ('==2.9')" in out
    assert "reconcile at >=4.2, <4.6" in out


def test_suggest_resolution_extends_the_json_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _conflicting_root(tmp_path)

    code, out, _ = _run(["cross-repo", str(root), "--suggest-resolution", "--json"], capsys)

    assert code == 0
    payload = json.loads(out)
    assert payload["resolutions"][0]["package"] == "shared-dep"
    assert payload["resolutions"][0]["odd_ones_out"][0]["repo"] == "beta"


def test_suggest_resolution_without_conflicts_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)

    code, out, _ = _run(["cross-repo", str(root), "--suggest-resolution"], capsys)

    assert code == 0
    assert "no provable version conflicts" in out


def test_suggest_resolution_refuses_watch_and_dot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)

    for extra in ("--dot", "--watch"):
        code, _, err = _run(["cross-repo", str(root), "--suggest-resolution", extra], capsys)
        assert code == 2
        assert "--suggest-resolution does not combine" in err


def test_json_and_dot_are_mutually_exclusive(tmp_path: Path) -> None:
    root = _org(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        main(["cross-repo", str(root), "--json", "--dot"])
    assert excinfo.value.code == 2


def test_parser_flags_and_defaults() -> None:
    parser = build_parser(command="cross-repo")
    args = parser.parse_args(["cross-repo", "/some/root"])
    assert args.root == "/some/root"
    assert args.db is None
    assert args.repo is None
    assert args.json is False
    assert args.dot is False
    assert args.watch is False
    assert args.interval == 2.0
    assert args.count == 0


class _FakeTty:
    """A TTY-shaped text sink capturing everything written to it."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def write(self, text: str) -> int:
        self.written.append(text)
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return True


def test_watch_on_a_tty_clears_and_redraws_in_place(tmp_path: Path) -> None:
    from typing import TextIO, cast

    from synapse_channel.cli_cross_repo import watch_cross_repo

    root = _org(tmp_path)
    sleeps: list[float] = []
    out = _FakeTty()
    code = watch_cross_repo(
        root=str(root),
        db=None,
        focus=None,
        as_json=False,
        interval=0.5,
        count=2,
        out=cast(TextIO, out),
        sleeper=sleeps.append,
    )
    assert code == 0
    assert sleeps == [0.5]  # count bounds the refreshes: one sleep between two
    text = "".join(out.written)
    assert text.count("\x1b[H\x1b[2J") == 2
    assert text.count("Cross-repository dependency graph:") == 2
    assert "---" not in text


def test_watch_piped_separates_refreshes_with_a_divider(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    code, out, _ = _run(
        ["cross-repo", str(root), "--watch", "--interval", "0.01", "--count", "2"], capsys
    )
    assert code == 0
    assert out.count("Cross-repository dependency graph:") == 2
    assert out.count("---\n") == 1
    assert "\x1b[" not in out


def test_watch_json_streams_one_document_per_refresh(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    code, out, _ = _run(
        ["cross-repo", str(root), "--json", "--watch", "--interval", "0.01", "--count", "2"],
        capsys,
    )
    assert code == 0
    lines = out.strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        payload = json.loads(line)
        assert [node["repo"] for node in payload["nodes"]] == ["consumer", "island", "provider"]


def test_watch_exit_reports_the_last_claim_signal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    db = tmp_path / "events.db"
    _seed_claims(db)
    code, out, _ = _run(
        [
            "cross-repo",
            str(root),
            "--db",
            str(db),
            "--repo",
            "consumer",
            "--watch",
            "--interval",
            "0.01",
            "--count",
            "2",
        ],
        capsys,
    )
    assert code == 1
    assert "provider [depends_on]" in out


def test_notify_cmd_fires_on_a_transition_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    from typing import TextIO, cast

    from synapse_channel import cli_cross_repo
    from synapse_channel.cli_cross_repo import watch_cross_repo

    root = _org(tmp_path)
    db = tmp_path / "events.db"
    _seed_claims(db)
    fired: list[tuple[list[str], list[str]]] = []
    monkeypatch.setattr(
        cli_cross_repo,
        "run_notify_command",
        lambda command, added, removed, notify_root: fired.append((added, removed)),
    )

    def _release_between_ticks(_seconds: float) -> None:
        from synapse_channel.core.journal import EventKind
        from synapse_channel.core.persistence import EventStore

        store = EventStore(db)
        store.append(EventKind.RELEASE, {"task_id": "CONS-1"}, ts=6.0, durable=True)
        store.close()

    code = watch_cross_repo(
        root=str(root),
        db=str(db),
        focus=None,
        as_json=False,
        interval=0.01,
        count=3,
        notify_cmd="true",
        out=cast(TextIO, io.StringIO()),
        sleeper=_release_between_ticks,
    )

    assert code == 0
    # refresh 1 = baseline (no fire); refresh 2 sees CONS-1 released (fire);
    # refresh 3 sees the same facts again (no fire)
    assert len(fired) == 1
    added, removed = fired[0]
    assert added == []
    assert removed == ["claim consumer CONS-1@agent-b [self]"]


def test_notify_cmd_stays_silent_on_a_steady_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    from typing import TextIO, cast

    from synapse_channel import cli_cross_repo
    from synapse_channel.cli_cross_repo import watch_cross_repo

    root = _org(tmp_path)
    fired: list[object] = []
    monkeypatch.setattr(
        cli_cross_repo,
        "run_notify_command",
        lambda *call: fired.append(call),
    )

    code = watch_cross_repo(
        root=str(root),
        db=None,
        focus=None,
        as_json=False,
        interval=0.01,
        count=3,
        notify_cmd="true",
        out=cast(TextIO, io.StringIO()),
        sleeper=lambda _seconds: None,
    )

    assert code == 0
    assert fired == []


def test_notify_cmd_requires_watch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _org(tmp_path)

    code, _, err = _run(["cross-repo", str(root), "--notify-cmd", "true"], capsys)

    assert code == 2
    assert "it requires --watch" in err


def test_run_notify_command_delivers_the_delta_and_environment(tmp_path: Path) -> None:
    from synapse_channel.cli_cross_repo import run_notify_command

    capture = tmp_path / "captured.txt"
    script = tmp_path / "sink.py"
    script.write_text(
        "import os, sys, pathlib\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    sys.stdin.read() + os.environ['SYNAPSE_CROSS_REPO_ROOT'],\n"
        "    encoding='utf-8',\n"
        ")\n",
        encoding="utf-8",
    )

    run_notify_command(
        f"{sys.executable} {script} {capture}",
        ["claim beta NEW-1@bob [self]"],
        ["version_conflict alpha<->beta shared-dep"],
        "/scanned/root",
    )

    written = capture.read_text(encoding="utf-8")
    assert "+ claim beta NEW-1@bob [self]\n" in written
    assert "- version_conflict alpha<->beta shared-dep\n" in written
    assert written.endswith("/scanned/root")


def test_run_notify_command_reports_failures_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from synapse_channel import cli_cross_repo
    from synapse_channel.cli_cross_repo import run_notify_command

    run_notify_command(f"{sys.executable} -c 'raise SystemExit(3)'", ["+ x"], [], "/root")
    assert "notify command exited 3" in capsys.readouterr().err

    run_notify_command("/no/such/binary-anywhere", ["+ x"], [], "/root")
    assert "notify command failed" in capsys.readouterr().err

    def _hang(*args: object, **kwargs: object) -> object:
        raise subprocess.TimeoutExpired(cmd="sink", timeout=60.0)

    monkeypatch.setattr(cli_cross_repo.subprocess, "run", _hang)
    run_notify_command("sink", ["+ x"], [], "/root")
    assert "notify command failed" in capsys.readouterr().err


def test_coordination_facts_cover_claims_and_conflicts_only(tmp_path: Path) -> None:
    from synapse_channel.cli_cross_repo import coordination_facts
    from synapse_channel.core.cross_repo_graph import run_cross_repo_graph

    root = _conflicting_root(tmp_path)
    facts = coordination_facts(run_cross_repo_graph(str(root), db_path=None, focus=None))

    assert any(fact.startswith("version_conflict ") for fact in facts)
    assert all(fact.split()[0] in {"claim", "version_conflict"} for fact in facts)


def test_watch_refuses_dot_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _org(tmp_path)
    code, _, err = _run(["cross-repo", str(root), "--dot", "--watch"], capsys)
    assert code == 2
    assert "--watch does not combine with --dot" in err


def test_watch_refuses_a_non_positive_interval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)
    code, _, err = _run(["cross-repo", str(root), "--watch", "--interval", "0"], capsys)
    assert code == 2
    assert "--interval must be positive" in err


def test_watch_failure_mid_refresh_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = _run(["cross-repo", str(tmp_path / "absent"), "--watch", "--count", "1"], capsys)
    assert code == 2
    assert "missing repository root" in err


def test_watch_interrupt_is_a_clean_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _org(tmp_path)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("synapse_channel.cli_cross_repo.time.sleep", interrupt)
    code, out, _ = _run(["cross-repo", str(root), "--watch"], capsys)
    assert code == 0
    assert "Cross-repository dependency graph:" in out
