# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — hand-off packets are checked against the world and carry no authority
"""A receiver re-checks what it is handed and acquires authority itself.

Real files and a real git repository back each packet. Changing an input or
moving a repository after the packet was built is detected, and the claim that
rested on it is no longer supported. An agent's word and a tool's report stay labelled
as unverified. A packet that tries to carry a lease, epoch or token is refused.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.work_state import (
    AUTHORITY_FIELDS,
    SCHEMA,
    WorkStateError,
    build_packet,
    load_packet,
    validate_packet,
    verify_packet,
)


def _git(repo: Path, *args: str) -> None:
    """Run git in ``repo`` with a fixed, isolated identity and configuration."""
    environment = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, env=environment
    )


def _repo(tmp_path: Path) -> Path:
    """Create a repository with one commit and return its path."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "a")
    return repo


def _packet(tmp_path: Path) -> tuple[dict[str, Any], Path, Path]:
    """Build a packet over a real report file and a real repository."""
    report = tmp_path / "report.txt"
    report.write_text("result 42\n", encoding="utf-8")
    repo = _repo(tmp_path)
    packet = build_packet(
        "PROJECT/task-1",
        inputs=[report],
        artefacts=[repo],
        completed_steps=["computed the report"],
        unknown_effects=["wake-abc"],
        remaining_conditions=["publish after review"],
        claims=[
            {"text": "the report is correct", "origin": "agent_claim"},
            {"text": "tests passed", "origin": "tool_result"},
            {"text": "report holds 42", "origin": "verified_artefact", "subject": str(report)},
        ],
    )
    return packet, report, repo


def test_an_unchanged_world_verifies_and_labels_every_claim(tmp_path: Path) -> None:
    """An unchanged world is consistent, and every claim is labelled by its origin."""
    packet, report, _repo_path = _packet(tmp_path)

    result = verify_packet(packet)

    assert result["consistent"] is True
    assert [claim["standing"] for claim in result["claims"]] == [
        "unverified: the previous agent's word",
        "unverified: a tool's report, not checked by the receiver",
        "subject unchanged since the packet; the claim's text is not checked",
    ]
    assert result["subjects"][str(report)]["status"] == "unchanged"
    assert result["authority"] == "none carried; claim the task from the current hub state"
    assert result["unknown_effects"] == ["wake-abc"]


def test_changed_and_missing_subjects_break_the_claims_that_rest_on_them(tmp_path: Path) -> None:
    """A changed or missing subject withdraws the support of the claims resting on it."""
    packet, report, repo = _packet(tmp_path)
    report.write_text("result 41\n", encoding="utf-8")
    (repo / "b.txt").write_text("b", encoding="utf-8")
    _git(repo, "add", "b.txt")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", "b")

    changed = verify_packet(packet)
    report.unlink()
    missing = verify_packet(packet)

    assert changed["consistent"] is False
    assert changed["subjects"][str(repo)]["status"] == "changed"
    assert changed["claims"][2]["standing"] == "no longer supported: subject changed"
    assert missing["claims"][2]["standing"] == "no longer supported: subject missing"
    assert missing["subjects"][str(report)]["observed"] == ""


@pytest.mark.parametrize("field", sorted(AUTHORITY_FIELDS))
def test_a_packet_never_carries_authority(tmp_path: Path, field: str) -> None:
    """Every authority-bearing field is refused."""
    packet, _report, _repo_path = _packet(tmp_path)

    with pytest.raises(WorkStateError, match="never carries authority"):
        validate_packet({**packet, field: "x"})


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"schema": "v0"}, "has exactly the fields"),
        ({"extra": 1}, "has exactly the fields"),
        ({"task_id": ""}, "printable text"),
        ({"inputs": {"x": "md5:1"}}, "sha256:<64 hex>"),
        ({"artefacts": {"repo": "short"}}, "40-hex commits"),
        ({"completed_steps": "one"}, "list of at most"),
        ({"claims": "x"}, "list of at most"),
        ({"claims": [{"text": "x"}]}, "text, origin"),
        ({"claims": [{"text": "x", "origin": "rumour"}]}, "origin is one of"),
        ({"claims": [{"text": "x", "origin": "agent_claim", "subject": "p"}]}, "exactly the"),
        ({"claims": [{"text": "x", "origin": "verified_artefact", "subject": "p"}]}, "subject is"),
    ],
)
def test_malformed_packets_are_refused(
    tmp_path: Path, changes: dict[str, Any], message: str
) -> None:
    """Each malformed field is refused with its reason."""
    packet, _report, _repo_path = _packet(tmp_path)

    with pytest.raises(WorkStateError, match=message):
        validate_packet({**packet, **changes})


def test_non_objects_and_non_repositories_are_refused(tmp_path: Path) -> None:
    """A packet that is not an object, or a non-repository artefact, is refused."""
    not_a_mapping: Any = ["packet"]
    with pytest.raises(WorkStateError, match="JSON object"):
        validate_packet(not_a_mapping)
    with pytest.raises(WorkStateError, match="not a git repository"):
        build_packet("T", artefacts=[tmp_path])
    path = tmp_path / "p.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(WorkStateError, match="is not JSON"):
        load_packet(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(WorkStateError, match="JSON object"):
        load_packet(path)


def test_the_cli_builds_and_verifies_a_packet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``synapse work-state build`` then ``verify``; exit 0, 1 after a change, 2 on refusal."""
    report = tmp_path / "report.txt"
    report.write_text("result 42\n", encoding="utf-8")
    repo = _repo(tmp_path)
    out = tmp_path / "handoff.json"
    build = [
        "work-state",
        "build",
        "--task",
        "PROJECT/task-1",
        "--out",
        str(out),
        "--input",
        str(report),
        "--artefact",
        str(repo),
        "--step",
        "computed",
        "--unknown",
        "op-1",
        "--condition",
        "review",
        "--agent-claim",
        "done",
        "--tool-result",
        "tests ok",
        "--verified",
        str(report),
        "holds 42",
    ]

    assert main(build) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == SCHEMA
    assert main(["work-state", "verify", str(out)]) == 0
    assert json.loads(capsys.readouterr().out)["consistent"] is True
    report.write_text("changed\n", encoding="utf-8")
    assert main(["work-state", "verify", str(out)]) == 1
    capsys.readouterr()

    assert main(build) == 2
    assert "work-state:" in capsys.readouterr().err
    (tmp_path / "bad.json").write_text(json.dumps({"lease": "x"}), encoding="utf-8")
    assert main(["work-state", "verify", str(tmp_path / "bad.json")]) == 2
    assert "never carries authority" in capsys.readouterr().err


def _cli_packet(tmp_path: Path, packet: object) -> list[str]:
    """Write ``packet`` as JSON and return the verify command for it."""
    path = tmp_path / "given.json"
    path.write_text(json.dumps(packet), encoding="utf-8")
    return ["work-state", "verify", str(path)]


def test_a_path_is_either_an_input_or_an_artefact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A repository given as an input too cannot hide the failed file check."""
    packet, _report, repo = _packet(tmp_path)
    packet["inputs"][str(repo)] = "sha256:" + "0" * 64

    assert main(_cli_packet(tmp_path, packet)) == 2
    assert "either an input or an artefact" in capsys.readouterr().err
    out = tmp_path / "both.json"
    assert (
        main(
            [
                "work-state",
                "build",
                "--task",
                "T",
                "--out",
                str(out),
                "--input",
                str(repo / "a.txt"),
                "--artefact",
                str(repo),
                "--input",
                str(repo),
            ]
        )
        == 2
    )
    assert not out.exists()


def test_subjects_keep_their_meaning_across_working_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Built from sender/ with relative paths, verified from receiver/: the sender's files count."""
    sender, receiver = tmp_path / "sender", tmp_path / "receiver"
    for place in (sender, receiver):
        place.mkdir()
        (place / "input.txt").write_text("original\n", encoding="utf-8")
    _repo(sender)
    packet = tmp_path / "handoff.json"
    monkeypatch.chdir(sender)
    assert (
        main(
            [
                "work-state",
                "build",
                "--task",
                "T",
                "--out",
                str(packet),
                "--input",
                "input.txt",
                "--artefact",
                "repo",
                "--verified",
                "input.txt",
                "input is original",
            ]
        )
        == 0
    )
    stored = json.loads(packet.read_text(encoding="utf-8"))
    assert set(stored["inputs"]) == {str(sender / "input.txt")}
    assert set(stored["artefacts"]) == {str(sender / "repo")}
    (sender / "input.txt").write_text("changed\n", encoding="utf-8")
    monkeypatch.chdir(receiver)

    assert main(["work-state", "verify", str(packet)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["claims"][0]["standing"] == "no longer supported: subject changed"
    stored["inputs"] = {"input.txt": stored["inputs"][str(sender / "input.txt")]}
    stored["claims"] = []
    assert main(_cli_packet(tmp_path, stored)) == 2
    assert "is absolute" in capsys.readouterr().err


def test_wrong_claim_types_and_undecodable_packets_are_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A list as a claim's subject or origin, or non-UTF-8 bytes, exit 2 without a traceback."""
    packet, _report, _repo_path = _packet(tmp_path)
    subject = {**packet, "claims": [{"text": "x", "origin": "verified_artefact", "subject": []}]}
    origin = {**packet, "claims": [{"text": "x", "origin": []}]}

    assert main(_cli_packet(tmp_path, subject)) == 2
    assert "subject is one of" in capsys.readouterr().err
    assert main(_cli_packet(tmp_path, origin)) == 2
    assert "origin is one of" in capsys.readouterr().err
    raw = tmp_path / "raw.json"
    raw.write_bytes(b"\xff\xfe{}")
    assert main(["work-state", "verify", str(raw)]) == 2
    assert "is not UTF-8 text" in capsys.readouterr().err
