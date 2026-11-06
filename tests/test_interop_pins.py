# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the interop lane's pins, documented matrix and lane entry
"""The documented interop matrix names exactly the pinned clients; the lane reports honestly."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tools import interop_a2a, interop_lane, interop_mcp_python, interop_mcp_ts, interop_pins
from tools.interop_pins import (
    InteropPinError,
    advisory,
    check_documented,
    documented,
    pins,
)
from tools.interop_record import JourneyRecord

pytestmark = pytest.mark.docs_contract

ROOT = Path(__file__).resolve().parent.parent


def test_the_documented_matrix_names_exactly_the_pins() -> None:
    check_documented()
    pinned = pins()
    assert set(pinned) == set(interop_pins.CLIENTS)
    assert documented() == {client: {version} for client, version in pinned.items()}


def _copy_pins(tmp_path: Path) -> Path:
    for relative in (
        ".github/requirements/requirements-dev.txt",
        ".github/requirements/requirements-interop-clients.txt",
        "clients/interop-mcp-ts/package-lock.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return tmp_path


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda text: text.replace("| 1.2.3 |", "| 1.3.0 |"), "pin is"),
        (lambda text: text.replace("| `a2a-sdk` |", "| `other-sdk` |"), "names"),
        (lambda text: text.replace("<!-- interop-matrix:start -->", ""), "no interop matrix"),
    ],
)
def test_a_matrix_that_drifts_from_the_pins_is_refused(
    tmp_path: Path, edit: object, message: str
) -> None:
    doc = tmp_path / "interop.md"
    assert callable(edit)
    doc.write_text(edit((ROOT / "docs" / "interop.md").read_text(encoding="utf-8")), "utf-8")
    with pytest.raises(InteropPinError, match=message):
        check_documented(ROOT, doc)


def test_a_missing_pin_is_refused(tmp_path: Path) -> None:
    root = _copy_pins(tmp_path)
    clients = root / ".github/requirements/requirements-interop-clients.txt"
    clients.write_text(clients.read_text("utf-8").replace("a2a-sdk==", "a2a-sdk>="), "utf-8")
    with pytest.raises(InteropPinError, match="a2a-sdk is not pinned"):
        pins(root)
    lock_path = root / "clients/interop-mcp-ts/package-lock.json"
    lock = json.loads(lock_path.read_text("utf-8"))
    del lock["packages"]["node_modules/@modelcontextprotocol/sdk"]
    lock_path.write_text(json.dumps(lock), "utf-8")
    with pytest.raises(InteropPinError, match="TypeScript SDK"):
        pins(root)


def test_the_advisory_reports_newer_releases_and_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pinned = pins()
    latest = dict(pinned, **{"a2a-sdk": "99.0.0"})
    assert advisory(latest) == [f"a2a-sdk: pinned {pinned['a2a-sdk']}, upstream latest 99.0.0"]
    assert advisory(pinned) == []
    report = tmp_path / "latest.json"
    report.write_text(json.dumps(latest), "utf-8")
    assert interop_pins.main(["--check-doc", "--advisory", str(report)]) == 0
    out = capsys.readouterr().out
    assert "interop matrix matches the pins" in out
    assert "::notice title=Interop client update available::a2a-sdk" in out
    report.write_text(json.dumps(pinned), "utf-8")
    assert interop_pins.main(["--advisory", str(report)]) == 0
    assert "every interop client pin is the latest" in capsys.readouterr().out
    assert pins() == pinned


def test_the_rows_cover_every_requested_client() -> None:
    labels = [label for label, _step, _runner in interop_lane._rows(None, mcp_ts=False)]
    assert labels == ["mcp-python-sdk/stdio", "mcp-python-sdk/streamable-http"]
    full = interop_lane._rows(Path("/client/python"), mcp_ts=True)
    assert [label for label, _step, _runner in full] == [
        "mcp-python-sdk/stdio",
        "mcp-python-sdk/streamable-http",
        "mcp-typescript-sdk/stdio",
        "mcp-typescript-sdk/streamable-http",
        "a2a-sdk/http-json",
    ]
    assert full[-1][1] == "journal-chat"


def test_the_run_identity_comes_from_actions_or_git(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_SHA", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    assert interop_lane._run_url() == ""
    assert len(interop_lane._commit()) == 40
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "anulum/synapse-channel")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    assert interop_lane._commit() == "f" * 40
    assert interop_lane._run_url() == "https://github.com/anulum/synapse-channel/actions/runs/7"


def _record(failed: str) -> JourneyRecord:
    return JourneyRecord(
        client="c",
        version="1",
        integrity="",
        transport="stdio",
        journey="j",
        steps=[],
        journal_sha256="",
        failed_step=failed,
    )


@pytest.mark.parametrize(("met", "code"), [(True, 0), (False, 1)])
def test_the_lane_exits_by_whether_every_outcome_was_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, met: bool, code: int
) -> None:
    seen: dict[str, object] = {}

    def recorded_lane(
        work: Path, *, a2a_python: Path | None, mcp_ts: bool
    ) -> tuple[list[JourneyRecord], list[str], bool]:
        seen.update(work=work, a2a_python=a2a_python, mcp_ts=mcp_ts)
        return [_record(""), _record("initialize-first")], ["lane-secret"], met

    monkeypatch.setattr(interop_lane, "run_lane", recorded_lane)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    out = tmp_path / "record.json"
    arguments = ["--out", str(out), "--work", str(tmp_path / "w"), "--mcp-ts"]
    assert interop_lane.main([*arguments, "--a2a-python", "/client/python"]) == code
    assert seen == {"work": tmp_path / "w", "a2a_python": Path("/client/python"), "mcp_ts": True}
    document = json.loads(out.read_text("utf-8"))
    assert [journey["passed"] for journey in document["journeys"]] == [True, False]
    assert "lane-secret" not in out.read_text("utf-8")


def test_each_row_runs_its_own_client_and_the_lane_judges_every_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, ...]] = []

    def python_row(transport: str, control: str, work: Path) -> tuple[JourneyRecord, list[str]]:
        calls.append(("python", transport, control))
        failed = {"none": "", "surface-disabled": "initialize-first"}.get(
            control, "journal-claim-granted"
        )
        return _record(failed), ["python-secret"]

    def ts_row(transport: str, control: str, work: Path) -> tuple[JourneyRecord, list[str]]:
        calls.append(("ts", transport, control))
        return _record(""), []

    def a2a_row(
        control: str, work: Path, *, client_python: Path
    ) -> tuple[JourneyRecord, list[str]]:
        calls.append(("a2a", str(client_python), control))
        return _record(""), []

    monkeypatch.setattr(interop_mcp_python, "run_journey", python_row)
    monkeypatch.setattr(interop_mcp_ts, "run_journey", ts_row)
    monkeypatch.setattr(interop_a2a, "run_journey", a2a_row)
    records, secrets, all_met = interop_lane.run_lane(
        tmp_path, a2a_python=Path("/client/python"), mcp_ts=True
    )
    assert len(records) == 15 and len(calls) == 15
    assert ("a2a", "/client/python", "journal-oracle") in calls
    assert secrets.count("python-secret") == 6
    # The TypeScript and A2A stand-ins pass every mode, so their controls are not met.
    assert all_met is False
    out = capsys.readouterr().out
    assert (
        "mcp-python-sdk/stdio journal-oracle: failed_step=journal-claim-granted required=True"
        in out
    )
    assert "a2a-sdk/http-json surface-disabled: failed_step=- required=False" in out
    assert (tmp_path / "mcp-typescript-sdk-stdio" / "none").is_dir()


def test_the_pin_tool_does_nothing_without_a_request(capsys: pytest.CaptureFixture[str]) -> None:
    assert interop_pins.main([]) == 0
    assert capsys.readouterr().out == ""


def test_the_pinned_hashes_are_the_ones_pip_enforces(tmp_path: Path) -> None:
    for client in ("mcp-python-sdk", "a2a-sdk"):
        hashes = interop_pins.pinned_hashes(client).split(",")
        assert hashes == sorted(hashes) and all(h.startswith("sha256:") for h in hashes)
    root = _copy_pins(tmp_path)
    clients = root / ".github/requirements/requirements-interop-clients.txt"
    text = clients.read_text("utf-8")
    clients.write_text(text.replace("--hash=sha256:", "--hash=md5:"), "utf-8")
    with pytest.raises(InteropPinError, match="no hashes"):
        interop_pins.pinned_hashes("a2a-sdk", root)
