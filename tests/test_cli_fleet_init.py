# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — `synapse fleet-init` one-command onboarding regressions

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

import pytest

from synapse_channel import cli_fleet_init, cli_participants
from synapse_channel.cli_fleet_init import (
    DEFAULT_WORKSPACE,
    _cmd_fleet_init,
    add_parsers,
    probe_seat,
    run_doctor_stage,
)
from synapse_channel.cli_participants import PROVIDERS


def _args(*argv: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_parsers(parser.add_subparsers())
    return parser.parse_args(["fleet-init", *argv])


def _all_available(provider: str) -> tuple[bool, str]:
    return True, f"{provider} binary found"


def _none_available(provider: str) -> tuple[bool, str]:
    return False, f"{provider} binary missing"


def _creator_recording(record: dict[str, object]) -> Callable[..., list[str]]:
    def create(path: Path, *, force: bool = False) -> list[str]:
        record.update({"path": path, "force": force})
        return [f"created {path}"]

    return create


def test_parser_defaults() -> None:
    args = _args()
    assert args.path is None
    assert args.fix is False
    assert args.force is False
    assert args.seat == []
    assert args.no_smoke is False


def test_unknown_seat_is_refused_before_any_stage(capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[str] = []

    def doctor(fix: bool) -> int:
        ran.append("doctor")
        return 0

    def demo() -> int:
        ran.append("demo")
        return 0

    args = _args("--seat", "claude", "--seat", "nonesuch")
    code = _cmd_fleet_init(
        args,
        doctor_stage=doctor,
        creator=_creator_recording({}),
        seat_probe=_all_available,
        demo_runner=demo,
    )
    assert code == 2
    assert "unknown --seat nonesuch" in capsys.readouterr().err
    assert ran == []  # nothing was started on a refused configuration


def test_happy_path_runs_all_stages_and_prints_the_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    record: dict[str, object] = {}
    args = _args()
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording(record),
        seat_probe=_all_available,
        demo_runner=lambda: 0,
    )
    assert code == 0
    assert record["path"] == Path(DEFAULT_WORKSPACE)
    assert record["force"] is False
    out = capsys.readouterr().out
    for header in (
        "== 1/4 doctor ==",
        "== 2/4 workspace ==",
        "== 3/4 model seats ==",
        "== 4/4 demo smoke ==",
        "== next steps ==",
    ):
        assert header in out
    assert f"created {DEFAULT_WORKSPACE}" in out
    for provider in PROVIDERS:
        assert f"{provider} available" in out
        assert f"synapse worker-session --identity {DEFAULT_WORKSPACE} -- {provider}" in out
    assert f'synapse arm --name {DEFAULT_WORKSPACE} --for "{DEFAULT_WORKSPACE},' in out
    assert "synapse doctor --fix" not in out  # doctor was healthy: no repair step


def test_failing_doctor_is_reported_but_does_not_abort(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args()
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 1,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: 0,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "doctor reported findings" in out
    assert "repair the local hub and waiter:  synapse doctor --fix" in out


def test_fix_flag_reaches_the_doctor_stage() -> None:
    seen: list[bool] = []

    def doctor(fix: bool) -> int:
        seen.append(fix)
        return 0

    args = _args("--fix")
    _cmd_fleet_init(
        args,
        doctor_stage=doctor,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: 0,
    )
    assert seen == [True]


def test_unsafe_workspace_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse(path: Path, *, force: bool = False) -> list[str]:
        raise FileExistsError(f"refusing to write into non-empty {path}")

    args = _args("taken")
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=refuse,
        seat_probe=_all_available,
        demo_runner=lambda: 0,
    )
    assert code == 2
    assert "refusing to write into non-empty taken" in capsys.readouterr().err


def test_explicit_path_and_force_reach_the_creator_and_the_plan(
    capsys: pytest.CaptureFixture[str],
) -> None:
    record: dict[str, object] = {}
    args = _args("fleets/alpha", "--force")
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording(record),
        seat_probe=_none_available,
        demo_runner=lambda: 0,
    )
    assert code == 0
    assert record == {"path": Path("fleets/alpha"), "force": True}
    out = capsys.readouterr().out
    assert 'synapse arm --name alpha --for "alpha,alpha/*"' in out


def test_no_smoke_skips_the_demo(capsys: pytest.CaptureFixture[str]) -> None:
    args = _args("--no-smoke")
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert code == 0
    assert "skipped (--no-smoke)" in capsys.readouterr().out


def test_a_failing_smoke_propagates_its_exit_code() -> None:
    args = _args()
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: 3,
    )
    assert code == 3


def test_declared_seats_are_planned_even_when_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args("--seat", "codex")
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: 0,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "warning: declared seat 'codex' is not available" in out
    assert f"synapse worker-session --identity {DEFAULT_WORKSPACE} -- codex" in out
    assert "-- claude" not in out  # undeclared providers stay out of a declared plan


def test_an_available_declared_seat_is_planned_without_a_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _args("--seat", "claude")
    code = _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_all_available,
        demo_runner=lambda: 0,
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "warning: declared seat" not in out
    assert f"synapse worker-session --identity {DEFAULT_WORKSPACE} -- claude" in out


@pytest.mark.parametrize(
    ("schema_verified", "readiness_note"),
    [
        (True, ""),
        (False, " [participant turns disabled]"),
    ],
)
def test_grok_readiness_note_tracks_schema_verification(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    schema_verified: bool,
    readiness_note: str,
) -> None:
    monkeypatch.setattr(cli_participants, "GROK_SCHEMA_VERIFIED", schema_verified)
    _cmd_fleet_init(
        _args(),
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_all_available,
        demo_runner=lambda: 0,
    )
    out = capsys.readouterr().out
    assert f"grok available: grok binary found{readiness_note}\n" in out
    assert "claude available: claude binary found\n" in out  # no note on other providers


def test_no_available_seats_omits_the_seating_step(capsys: pytest.CaptureFixture[str]) -> None:
    args = _args()
    _cmd_fleet_init(
        args,
        doctor_stage=lambda fix: 0,
        creator=_creator_recording({}),
        seat_probe=_none_available,
        demo_runner=lambda: 0,
    )
    out = capsys.readouterr().out
    assert "seat your agents" not in out
    for provider in PROVIDERS:
        assert f"{provider} unavailable" in out


def test_run_doctor_stage_builds_the_real_doctor_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_doctor(args: argparse.Namespace, **_: object) -> int:
        captured.update({"fix": args.fix, "json": getattr(args, "json", False)})
        return 7

    from synapse_channel import cli_doctor

    monkeypatch.setattr(cli_doctor, "_cmd_doctor", fake_doctor)
    assert run_doctor_stage(True) == 7
    assert captured == {"fix": True, "json": False}
    assert run_doctor_stage(False) == 7
    assert captured["fix"] is False


def test_probe_seat_reports_without_taking_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Health:
        available = True
        detail = "binary found"

    class _Probe:
        def health(self) -> _Health:
            return _Health()

    def fake_build(provider: str, **kwargs: object) -> _Probe:
        assert kwargs["probe"] is True  # a probe must never require a model or take a turn
        return _Probe()

    monkeypatch.setattr(cli_fleet_init, "build_participant", fake_build)
    assert probe_seat("claude") == (True, "binary found")
