# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for `synapse ops list` and `synapse ops describe`
"""The real entry point reading real catalogue files of both schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.cli import main
from synapse_channel.cli_ops import MAX_CATALOGUE_BYTES, default_catalogue_path
from synapse_channel.operations.catalogue import Catalogue, LegacyInventory
from synapse_channel.operations.row import ArgumentBinding, CallerObligation, NativeBinding
from test_operations_row import list_sessions, qualified, read_session
from test_operations_spec import queue_message


def _file(tmp_path: Path) -> Path:
    ready = qualified(list_sessions(side_effects=("none on the host",)))
    catalogue = Catalogue(
        [
            ready,
            list_sessions(operation="session.search", qualification="help_text", variant="by-word"),
            qualified(list_sessions(host="msi", os="windows", needs_restart=True)),
            read_session(
                effects=("read", "approve"),
                obligations=(CallerObligation("item/permissions/requestApproval", "refuse"),),
                output_projection="redacted-v1",
            ),
            list_sessions(
                operation="session.export",
                binding=NativeBinding("export", executable="kimi", fixed=("export",)),
                arguments=(
                    ArgumentBinding("session", "string", "position", "<id>", position=0),
                    ArgumentBinding(
                        "parts", "enum", "flag", "--part", False, ("log", "turns"), max_items=2
                    ),
                ),
                reserved_natives=(),
                effects=("host",),
                output_mode="text",
                output_schema="",
            ),
        ]
    )
    data = catalogue.to_json()
    rows = data["operations"]
    assert isinstance(rows, list)
    # One row whose binding was changed after its qualification.
    stale = {**ready.to_json(), "operation": "session.loaded.list", "reserved_natives": []}
    rows.append(stale)
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _legacy_file(tmp_path: Path) -> Path:
    legacy = LegacyInventory(
        [queue_message(), queue_message(host="msi", os="windows", transport="rpc")]
    )
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(legacy.to_json()), encoding="utf-8")
    return path


def test_list_prints_one_line_per_operation_and_filters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "list", "--catalogue", path]) == 0
    everything = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--host", "msi", "--vendor", "kimi"]) == 0
    one = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--transport", "rpc"]) == 0
    by_request = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--vendor", "gemini"]) == 0
    none = capsys.readouterr().out

    assert everything[0] == "6 operation(s)" and len(everything) == 7
    for line in (
        "workstation kimi cli 2.1.1 linux  session.list  cli/user  read  run_once (may run)",
        "workstation kimi cli 2.1.1 linux  session.search#by-word  cli/user  read  "
        "help_text (not run)",
        "workstation kimi cli 2.1.1 linux  session.loaded.list  cli/user  read  "
        "run_once (binding changed since, not run)",
        "workstation kimi cli 2.1.1 linux  session.export  cli/user  host  unverified (not run)",
    ):
        assert line in everything
    assert one == [
        "1 operation(s)",
        "msi kimi cli 2.1.1 windows  session.list  cli/user  read  run_once (may run)",
    ]
    assert by_request == [
        "1 operation(s)",
        "workstation codex app-server 0.159.1 linux  session.read  rpc/session  approve  "
        "unverified (not run)",
    ]
    assert none == "0 operation(s)\n"


@pytest.mark.parametrize(
    ("effects", "disclosures", "projection", "may_run"),
    [
        (("read", "host"), (), "", False),
        (("access",), (), "", False),
        (("read",), ("credential_bearing",), "", False),
        (("read",), ("credential_bearing",), "names-only-v1", True),
    ],
)
def test_qualified_rows_display_the_catalogue_admission_constraints(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    effects: tuple[str, ...],
    disclosures: tuple[str, ...],
    projection: str,
    may_run: bool,
) -> None:
    """List and describe keep a qualified but refused row marked as not run."""
    row = qualified(
        list_sessions(effects=effects, disclosures=disclosures, output_projection=projection)
    )
    path = tmp_path / "qualified.json"
    path.write_text(json.dumps(Catalogue([row]).to_json()), encoding="utf-8")

    for arguments in (["list"], ["describe", "session.list"]):
        assert main(["ops", *arguments, "--catalogue", str(path)]) == 0
        output = capsys.readouterr()
        assert output.err == ""
        assert "run_once" in output.out
        assert ("may run" in output.out) is may_run
        assert ("not run" in output.out) is not may_run


def test_describe_prints_every_row_of_an_operation_in_full(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "describe", "session.list", "--catalogue", path]) == 0
    blocks = capsys.readouterr().out.split("\n\n")
    assert main(["ops", "describe", "session.read", "--catalogue", path]) == 0
    request = capsys.readouterr().out.splitlines()
    assert main(["ops", "describe", "session.export", "--catalogue", path]) == 0
    export = capsys.readouterr().out.splitlines()
    assert main(["ops", "describe", "session.delete", "--catalogue", path]) == 1
    absent = capsys.readouterr()

    assert len(blocks) == 2
    local = next(block for block in blocks if "at workstation" in block).splitlines()
    assert local == [
        "operation: session.list (variant default)",
        "component: kimi cli 2.1.1 on linux at workstation (reports: 2.1.1)",
        "transport: cli; scope: user",
        "native name: session list",
        "invoked as: /opt/kimi/bin/kimi session list --json",
        "effects: read; every permission needed: operation-read",
        "output: json (kimi.session-list.v1)",
        "interactive: no; idempotent: yes; needs restart: no",
        "qualification: run_once (evidence: evidence/run-1); may run",
        "argument cwd: string, optional; flag --cwd",
        "argument all: boolean, optional; switch --all",
        "argument limit: integer, optional from 1 to 500; flag --limit",
        "side effect: none on the host",
    ]
    assert "needs restart: yes" in next(block for block in blocks if "at msi" in block)
    assert request[3:7] == [
        "native name: thread/read",
        "invoked as: thread/read",
        "effects: read, approve; every permission needed: operation-read, operation-approve"
        "; a named person must consent",
        "output: json; discloses: session_content; projection: redacted-v1",
    ]
    assert "qualification: unverified; not run" in request
    assert "argument session: uuid, required; parameter threadId" in request
    assert "argument sources: list of at most 3 string, optional; parameter sourceKinds" in request
    assert request[-1] == (
        "the component may ask: item/permissions/requestApproval; ended by: refuse"
    )
    assert "effects: host; every permission needed: none can be granted" in export
    assert "output: text" in export
    assert "argument session: string, required; position <id>" in export
    assert (
        "argument parts: list of at most 2 enum, optional one of log, turns; flag --part" in export
    )
    assert (absent.out, absent.err) == ("", "synapse ops: session.delete is not listed\n")


def test_a_file_of_the_earlier_schema_is_printed_and_marked_as_not_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_legacy_file(tmp_path))

    assert main(["ops", "list", "--catalogue", path]) == 0
    listed = capsys.readouterr().out.splitlines()
    assert main(["ops", "list", "--catalogue", path, "--transport", "rpc", "--host", "msi"]) == 0
    filtered = capsys.readouterr().out.splitlines()
    assert main(["ops", "describe", "session.queue_message", "--catalogue", path]) == 0
    blocks = capsys.readouterr().out.split("\n\n")
    assert main(["ops", "list", "--catalogue", path, "--json", "--host", "msi"]) == 0
    as_data = json.loads(capsys.readouterr().out)

    assert listed == [
        "2 operation(s)",
        "msi codex 0.159.1 windows  session.queue_message  rpc/session  "
        "run_once (earlier schema, not run)",
        "workstation codex 0.159.1 linux  session.queue_message  cli/session  "
        "run_once (earlier schema, not run)",
    ]
    assert filtered == ["1 operation(s)", listed[1]]
    assert blocks[1].splitlines() == [
        "operation: session.queue_message",
        "client: codex 0.159.1 on linux at workstation",
        "transport: cli; scope: session",
        "permission named: ops.session.write",
        "qualification stated: run_once; earlier schema: listed, and not run",
        "side effect: a message is queued into the session",
    ]
    assert [(row["host"], row["required_authority"]) for row in as_data] == [
        ("msi", "ops.session.write")
    ]


def test_json_output_is_the_rows_as_plain_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(_file(tmp_path))

    assert main(["ops", "list", "--catalogue", path, "--json", "--vendor", "codex"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert main(["ops", "describe", "session.search", "--catalogue", path, "--json"]) == 0
    described = json.loads(capsys.readouterr().out)

    assert [(row["vendor"], row["binding"]["method"]) for row in listed] == [
        ("codex", "thread/read")
    ]
    assert [(row["operation"], row["variant"], row["qualification"]) for row in described] == [
        ("session.search", "by-word", "help_text")
    ]


def test_text_from_the_file_cannot_move_the_cursor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hostile = list_sessions(
        side_effects=("clears \x1b[2J the screen",),
        evidence="e\x1b[31m",
        version_printed="v\x1b[0m",
    )
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(Catalogue([hostile]).to_json()), encoding="utf-8")
    legacy = tmp_path / "legacy.json"
    old = queue_message(side_effects=("clears \x1b[2J the screen",))
    legacy.write_text(json.dumps(LegacyInventory([old]).to_json()), encoding="utf-8")

    assert main(["ops", "describe", "session.list", "--catalogue", str(path)]) == 0
    assert main(["ops", "describe", "session.queue_message", "--catalogue", str(legacy)]) == 0
    assert main(["ops", "describe", "x\x1b[2J.y", "--catalogue", str(path)]) == 1
    captured = capsys.readouterr()

    assert "\x1b" not in captured.out and "\x1b" not in captured.err
    assert captured.out.count("side effect: clears ") == 2


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
        "synapse ops: cannot read the catalogue file",
        "synapse ops: cannot use the catalogue: the catalogue file is not JSON",
        "synapse ops: cannot use the catalogue: the catalogue file is not JSON",
        "synapse ops: cannot use the catalogue: not an operation catalogue of a known schema",
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


@pytest.mark.parametrize(
    "damage",
    [
        lambda row: row["arguments"][0].__setitem__("choices", [1]),
        lambda row: row["arguments"][0].__setitem__("choices", "normal"),
        lambda row: row["arguments"][0].__setitem__("description", 5),
        lambda row: row["arguments"][0].__setitem__("name", 7),
        lambda row: row["arguments"][0].__setitem__("native", "--json"),
        lambda row: row["arguments"].__setitem__(0, "cwd"),
        lambda row: row.__setitem__("arguments", {"cwd": "string"}),
        lambda row: row.__setitem__("side_effects", "lists sessions"),
        lambda row: row.__setitem__("side_effects", [3]),
        lambda row: row.__setitem__("evidence", 12),
        lambda row: row.__setitem__("vendor", ["kimi"]),
        lambda row: row.__setitem__("needs_restart", "no"),
        lambda row: row.__setitem__("qualification", None),
        lambda row: row.__setitem__("operation", "thread.list"),
        lambda row: row.__setitem__("binding", "kimi session list"),
        lambda row: row.__setitem__("required_authority", "ops.read"),
    ],
)
@pytest.mark.parametrize("command", [["list"], ["describe", "session.list"], ["list", "--json"]])
def test_a_catalogue_with_a_value_of_the_wrong_kind_is_refused_by_every_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], damage: Any, command: list[str]
) -> None:
    data = json.loads(json.dumps(Catalogue([list_sessions()]).to_json()))
    rows = data["operations"]
    assert isinstance(rows, list)
    damage(rows[0])
    path = tmp_path / "catalogue.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    code = main(["ops", *command, "--catalogue", str(path)])
    captured = capsys.readouterr()

    assert (code, captured.out) == (2, "")
    assert captured.err.startswith("synapse ops: cannot use the catalogue: ")
    assert "Traceback" not in captured.err and len(captured.err.splitlines()) == 1


@pytest.mark.parametrize(
    "damage",
    [
        lambda row: row["arguments"][1].__setitem__("choices", "normal"),
        lambda row: row.__setitem__("effects", ["read"]),
        lambda row: row.__setitem__("vendor", ["codex"]),
    ],
)
def test_a_file_of_the_earlier_schema_stays_strict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], damage: Any
) -> None:
    data = json.loads(json.dumps(LegacyInventory([queue_message()]).to_json()))
    damage(data["operations"][0])
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    assert main(["ops", "list", "--catalogue", str(path)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err.startswith("synapse ops: cannot use the catalogue: ")


def test_a_file_that_cannot_be_opened_gives_one_fixed_sentence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "catalogue.json"
    directory.mkdir()

    assert main(["ops", "list", "--catalogue", str(directory)]) == 2

    assert capsys.readouterr().err == "synapse ops: cannot read the catalogue file\n"
