# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for `synapse ops list` and `synapse ops describe`
"""The real entry point reading real catalogue files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_ops import MAX_CATALOGUE_BYTES, default_catalogue_path
from synapse_channel.operations.catalogue import Catalogue
from test_operations_spec import queue_message


def _file(tmp_path: Path) -> Path:
    catalogue = Catalogue(
        [
            queue_message(),
            queue_message(operation="session.list", qualification="help_text", evidence=""),
            queue_message(host="msi", os="windows", needs_restart=True),
            queue_message(vendor="kimi", client_version="1.4.0", arguments=(), side_effects=()),
        ]
    )
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(catalogue.to_json()), encoding="utf-8")
    return path


def test_list_prints_one_line_per_operation_and_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "list", "--catalogue", path]) == 0
    everything = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--host", "msi", "--vendor", "codex"]) == 0
    one = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--vendor", "gemini"]) == 0
    none = capsys.readouterr().out

    assert everything[0] == "4 operation(s)" and len(everything) == 5
    assert (
        "workstation codex 0.159.1 linux  session.queue_message  cli/session  run_once (may run)"
        in everything
    )
    assert (
        "workstation codex 0.159.1 linux  session.list  cli/session  help_text (not run)"
        in everything
    )
    assert one == [
        "1 operation(s)",
        "msi codex 0.159.1 windows  session.queue_message  cli/session  run_once (may run)",
    ]
    assert none == "0 operation(s)\n"


def test_describe_prints_every_row_of_an_operation_in_full(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "describe", "session.queue_message", "--catalogue", path]) == 0
    blocks = capsys.readouterr().out.split("\n\n")
    assert main(["ops", "describe", "session.list", "--catalogue", path]) == 0
    listed_only = capsys.readouterr().out.splitlines()
    assert main(["ops", "describe", "session.delete", "--catalogue", path]) == 1
    absent = capsys.readouterr()

    assert len(blocks) == 3
    kimi = next(block for block in blocks if "client: kimi" in block).splitlines()
    assert kimi == [
        "operation: session.queue_message",
        "client: kimi 1.4.0 on linux at workstation",
        "transport: cli; scope: session",
        "permission needed: ops.session.write",
        "interactive: no; idempotent: no; needs restart: no",
        "qualification: run_once (evidence: planning/CODEX_NATIVE_QUEUE_QUALIFICATION_2026-10-04)"
        "; may run",
    ]
    msi = next(block for block in blocks if "at msi" in block)
    assert "needs restart: yes" in msi
    assert "argument thread: uuid, required" in msi
    assert "argument priority: enum, optional one of normal, high" in msi
    assert "side effect: a message is queued into the session" in msi
    assert "qualification: help_text; listed, and not run" in listed_only
    assert (absent.out, absent.err) == ("", "synapse ops: session.delete is not listed\n")


def test_json_output_is_the_rows_as_plain_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "list", "--catalogue", path, "--json", "--vendor", "kimi"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert main(["ops", "describe", "session.list", "--catalogue", path, "--json"]) == 0
    described = json.loads(capsys.readouterr().out)

    assert [row["vendor"] for row in listed] == ["kimi"]
    assert [(row["operation"], row["qualification"]) for row in described] == [
        ("session.list", "help_text")
    ]


def test_text_from_the_file_cannot_move_the_cursor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = queue_message(side_effects=("clears \x1b[2J the screen",), evidence="e\x1b[31m")
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(Catalogue([hostile]).to_json()), encoding="utf-8")

    assert main(["ops", "describe", "session.queue_message", "--catalogue", str(path)]) == 0
    assert main(["ops", "describe", "x\x1b[2J.y", "--catalogue", str(path)]) == 1
    captured = capsys.readouterr()

    assert "\x1b" not in captured.out and "\x1b" not in captured.err
    assert "side effect: clears " in captured.out


def test_a_file_that_is_not_a_catalogue_is_refused_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = {
        "absent.json": None,
        "text.json": b"not json",
        "binary.json": b"\xff\xfe",
        "other.json": b'{"schema": "other", "operations": []}',
        "large.json": b" " * (MAX_CATALOGUE_BYTES + 1),
    }
    reasons = []
    for name, content in cases.items():
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        assert main(["ops", "list", "--catalogue", str(path)]) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        reasons.append(captured.err.strip())

    assert reasons == [
        "synapse ops: cannot use the catalogue: No such file or directory",
        "synapse ops: cannot use the catalogue: the catalogue file is not JSON",
        "synapse ops: cannot use the catalogue: the catalogue file is not JSON",
        "synapse ops: cannot use the catalogue: not an operation catalogue of this schema",
        "synapse ops: cannot use the catalogue: the catalogue file is larger than the limit",
    ]
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert default_catalogue_path() == (
        tmp_path / "data" / "synapse" / "operations" / "catalogue.json"
    )
    assert main(["ops", "list"]) == 2
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "h"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "h"))
    assert default_catalogue_path() == (
        tmp_path / "h" / ".local" / "share" / "synapse" / "operations" / "catalogue.json"
    )
