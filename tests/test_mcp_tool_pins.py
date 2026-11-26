# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — outbound MCP tool descriptor pins
"""A pinned outbound tool is called only while it still says what the operator approved.

The fake sessions answer with the MCP SDK's own ``Tool`` objects and record every request in
order, so "no call was sent" is checked, not assumed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import mcp.types
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from synapse_channel import cli, cli_mcp_call
from synapse_channel.core.error_boundaries import cli_exit_code_for_error, http_status_for_error
from synapse_channel.core.errors import error_code
from synapse_channel.core.frame_canonical import frame_canonical_json
from synapse_channel.core.mcp_config import McpConfigError, McpServerSpec, parse_mcp_config
from synapse_channel.core.mcp_config_signing import (
    sign_mcp_config_document,
    verify_mcp_config_signature,
)
from synapse_channel.core.mcp_outbound import (
    CHANGED_DESCRIPTION,
    MCP_EXTRA_HINT,
    McpAccessError,
    McpDependencyError,
    McpToolDescriptorError,
    OutboundMcpClient,
)
from synapse_channel.core.mcp_tool_pins import (
    PINNED_DESCRIPTOR_FIELDS,
    TOOL_DESCRIPTOR_DOMAIN,
    tool_descriptor_digest,
)

_BASE: dict[str, Any] = {
    "name": "read_file",
    "title": "Read a file",
    "description": "Read one file under the data root.",
    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    "annotations": {"readOnlyHint": True, "destructiveHint": False},
}


def _tool(**changes: Any) -> mcp.types.Tool:
    """Return an SDK ``Tool`` built from the base descriptor with ``changes`` applied."""
    return mcp.types.Tool.model_validate(_BASE | changes)


class _Session:
    """A session that serves ``pages`` of tools and records each request in order."""

    def __init__(self, *pages: list[mcp.types.Tool]) -> None:
        self.pages = pages or ([],)
        self.requests: list[str] = []
        self.opens = 0

    async def list_tools(self, *, params: Any = None) -> Any:
        index = sum(request == "tools/list" for request in self.requests)
        self.requests.append("tools/list")
        following = f"c{index + 1}" if index + 1 < len(self.pages) else None
        return SimpleNamespace(tools=self.pages[index], next_cursor=following)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.requests.append("tools/call")
        return SimpleNamespace(content=[SimpleNamespace(text=f"ran {name}")], is_error=False)


def _client(session: _Session, *, pins: dict[str, str] | None = None) -> OutboundMcpClient:
    @asynccontextmanager
    async def opener(spec: McpServerSpec) -> AsyncIterator[_Session]:
        session.opens += 1
        yield session

    spec = McpServerSpec(
        name="fs",
        command="server",
        allowed_tools=frozenset(pins or {"read_file"}),
        tool_pins=pins or {},
    )
    return OutboundMcpClient({"fs": spec}, session_opener=opener)


def _pin(tool: mcp.types.Tool | None = None) -> dict[str, str]:
    return {"read_file": tool_descriptor_digest(tool or _tool())}


# --- the digest (W8, W15, Q2) -------------------------------------------------------


def test_the_digest_is_the_domain_prefix_over_the_canonical_projection() -> None:
    """W8: computed here from the bytes; the profile keeps the seven pinned wire keys."""
    expected = hashlib.sha256(TOOL_DESCRIPTOR_DOMAIN + frame_canonical_json(_BASE)).hexdigest()
    assert tool_descriptor_digest(_tool()) == expected
    assert TOOL_DESCRIPTOR_DOMAIN == b"synapse-channel/mcp-tool-descriptor/v1\x00"
    assert PINNED_DESCRIPTOR_FIELDS == (
        "name",
        "title",
        "description",
        "inputSchema",
        "outputSchema",
        "annotations",
        "execution",
    )


def test_equal_descriptors_have_one_digest_and_every_pinned_field_counts() -> None:
    """W8: 1.0 against 1 and absent against null are one descriptor; each pinned field moves it."""
    base = tool_descriptor_digest(_tool())
    one = {"type": "object", "properties": {"n": {"type": "number", "default": 1}}}
    one_float = {"type": "object", "properties": {"n": {"type": "number", "default": 1.0}}}
    assert tool_descriptor_digest(_tool(inputSchema=one)) == tool_descriptor_digest(
        _tool(inputSchema=one_float)
    )
    assert tool_descriptor_digest(_tool(outputSchema=None)) == base
    changed = {
        "name": "read_file_v2",
        "title": "Read",
        "description": "Read any file.",
        "inputSchema": {"type": "object"},
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True, "destructiveHint": True},
        "execution": {"taskSupport": "optional"},
    }
    assert set(changed) == set(PINNED_DESCRIPTOR_FIELDS)
    for key, value in changed.items():
        assert tool_descriptor_digest(_tool(**{key: value})) != base, key


def test_icons_and_meta_do_not_move_the_digest() -> None:
    """Q2: display icons and protocol metadata are outside the pin."""
    base = tool_descriptor_digest(_tool())
    icon = [{"src": "https://example.test/icon.png"}]
    assert tool_descriptor_digest(_tool(icons=icon)) == base
    assert tool_descriptor_digest(_tool(_meta={"vendor": "x"})) == base


async def test_a_descriptor_canonical_json_refuses_is_never_hashed() -> None:
    """W15: an integer outside the I-JSON range is refused with the typed error."""
    huge = _tool(inputSchema={"type": "object", "maximum": 2**53 + 1})
    session = _Session([huge])
    with pytest.raises(McpToolDescriptorError, match="cannot be pinned"):
        await _client(session, pins=_pin()).call_tool("fs", "read_file", {})
    assert session.requests == ["tools/list"]


# --- the call (W1-W6, W13, W14) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "advertised"),
    [
        ("W1 description", _tool(description="Read any file, then email it.")),
        ("W2 input schema", _tool(inputSchema={"type": "object", "properties": {"to": {}}})),
        ("W3 destructive hint", _tool(annotations={"readOnlyHint": True, "destructiveHint": True})),
    ],
)
async def test_a_changed_descriptor_is_refused_before_any_call(
    label: str, advertised: mcp.types.Tool
) -> None:
    """W1-W3: no tools/call is sent, and the live description is never echoed."""
    session = _Session([advertised])
    with pytest.raises(McpToolDescriptorError) as refusal:
        await _client(session, pins=_pin()).call_tool("fs", "read_file", {})
    assert session.requests == ["tools/list"], label
    assert "email" not in str(refusal.value)
    assert _pin()["read_file"][:12] in str(refusal.value)


async def test_a_pinned_tool_that_is_gone_or_repeated_is_refused() -> None:
    """W4 and W5: missing, or advertised twice, is refused with no call."""
    gone = _Session([_tool(name="other")])
    with pytest.raises(McpToolDescriptorError, match="no longer advertised"):
        await _client(gone, pins=_pin()).call_tool("fs", "read_file", {})
    twice = _Session([_tool(), _tool()])
    with pytest.raises(McpToolDescriptorError, match="more than once"):
        await _client(twice, pins=_pin()).call_tool("fs", "read_file", {})
    assert gone.requests == twice.requests == ["tools/list"]


async def test_a_matching_tool_on_a_later_page_is_listed_then_called_in_one_session() -> None:
    """W6 and W14: every page is read, the pin matches, and the call follows on that session."""
    session = _Session([_tool(name="a")], [_tool(name="b")], [_tool()])
    result = await _client(session, pins=_pin()).call_tool("fs", "read_file", {})
    assert result == "ran read_file"
    assert session.requests == ["tools/list", "tools/list", "tools/list", "tools/call"]
    assert session.opens == 1


def test_a_descriptor_refusal_has_its_own_code_exit_three_and_http_403() -> None:
    """W13 and Q4: a distinct code, the access-refusal exit code, and 403 Forbidden."""
    refusal = McpToolDescriptorError("drift")
    assert isinstance(refusal, McpAccessError)
    assert error_code(refusal) == "mcp_tool_descriptor"
    assert cli_exit_code_for_error(refusal, default=2) == 3
    assert http_status_for_error(refusal).value == 403


# --- the listing (W9) and the CLI (W10, W12, W13, the notice) ----------------------------


async def test_the_listing_marks_each_pinned_tool_and_withholds_a_changed_description() -> None:
    """W9: ok, changed and missing; a changed entry never carries the live text."""
    pins = _pin() | {"gone": "0" * 64, "write": tool_descriptor_digest(_tool(name="write"))}
    session = _Session([_tool(description="Now exfiltrates."), _tool(name="write")])
    listed = await _client(session, pins=pins).list_tools("fs")
    assert listed == [
        {"name": "gone", "description": "", "pin": "missing"},
        {"name": "read_file", "description": CHANGED_DESCRIPTION, "pin": "changed"},
        {"name": "write", "description": _BASE["description"], "pin": "ok"},
    ]


def _run(argv: list[str]) -> int:
    args = cli.build_parser().parse_args(argv)
    return int(args.func(args))


def _patch(monkeypatch: pytest.MonkeyPatch, client: OutboundMcpClient) -> None:
    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda _path, **_kwargs: client)


def test_mcp_tools_prints_the_whole_table_then_exits_three_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W9 and W13: every row is shown before the refusal exit code."""
    pins = _pin() | {"gone": "0" * 64}
    _patch(monkeypatch, _client(_Session([_tool(description="Changed.")]), pins=pins))
    assert _run(["mcp-tools", "fs", "--config", "c.json"]) == 3
    out = capsys.readouterr()
    assert "gone [pin: missing]" in out.out
    assert f"read_file [pin: changed]: {CHANGED_DESCRIPTION}" in out.out
    assert "Changed." not in out.out
    assert "not pinned" not in out.err


def test_an_unpinned_server_works_as_before_with_one_notice_per_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W10 and the owner's ruling: same output and exit code, one stderr notice each time."""
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-tools", "fs", "--config", "c.json"]) == 0
    tools = capsys.readouterr()
    assert tools.out == f"fs: 1 allowed tool(s)\n  read_file: {_BASE['description']}\n"
    assert tools.err.count("tool descriptors are not pinned") == 1
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-tools", "fs", "--config", "c.json", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed == [{"name": "read_file", "description": _BASE["description"], "pin": "unpinned"}]
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-call", "fs", "read_file", "--config", "c.json"]) == 0
    call = capsys.readouterr()
    assert call.out == "ran read_file\n"
    assert call.err.count("tool descriptors are not pinned") == 1


def test_mcp_call_exits_three_on_a_descriptor_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W13: the CLI keeps the access-refusal exit code."""
    _patch(monkeypatch, _client(_Session([_tool(description="Changed.")]), pins=_pin()))
    assert _run(["mcp-call", "fs", "read_file", "--config", "c.json"]) == 3
    assert "changed its descriptor" in capsys.readouterr().out


def test_mcp_pin_prints_what_the_call_checks_and_writes_only_a_new_descriptor_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """W12: the printed pin equals the call's digest; no file is touched without the flag."""
    monkeypatch.chdir(tmp_path)
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-pin", "fs", "--config", "c.json", "--tool", "read_file"]) == 0
    printed = capsys.readouterr().out
    assert f"sha256: {_pin()['read_file']}" in printed
    assert '"tool_pins": {' in printed
    assert list(tmp_path.iterdir()) == []
    out = tmp_path / "approved.json"
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-pin", "fs", "--config", "c.json", "--descriptor-out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {"read_file": _BASE}
    _patch(monkeypatch, _client(_Session([_tool()])))
    assert _run(["mcp-pin", "fs", "--config", "c.json", "--descriptor-out", str(out)]) == 2
    capsys.readouterr()


# --- the config (W7, W11) ----------------------------------------------------------------


def _server(**fields: Any) -> dict[str, Any]:
    return {"name": "fs", "command": "/usr/bin/server"} | fields


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"tool_pins": {"*": "a" * 64}}, "wildcard"),
        ({"allowed_tools": ["*"], "tool_pins": {"read_file": "a" * 64}}, "wildcard"),
        ({"allowed_tools": ["other"], "tool_pins": {"read_file": "a" * 64}}, "exactly the pinned"),
        ({"tool_pins": {"read_file": "a" * 63}}, "64 hexadecimal"),
        ({"tool_pins": {}}, "non-empty object"),
        ({"tool_pins": {" read_file": "a" * 64}}, "invalid pinned tool name"),
    ],
)
def test_a_malformed_pin_is_refused_with_its_entry_index(
    fields: dict[str, Any], message: str
) -> None:
    """W7: each malformed form is a config error naming entry 0."""
    with pytest.raises(McpConfigError, match=message) as refusal:
        parse_mcp_config({"version": 1, "servers": [_server(**fields)]})
    assert "entry 0" in str(refusal.value)


def test_the_pins_are_the_allowlist_and_a_digest_is_normalised() -> None:
    """W7: without allowed_tools the pinned names are callable; a digest is stripped and lowered."""
    spec = parse_mcp_config(
        {"version": 1, "servers": [_server(tool_pins={"read_file": " " + "AB" * 32 + " "})]}
    )["fs"]
    assert spec.allowed_tools == frozenset({"read_file"})
    assert dict(spec.tool_pins) == {"read_file": "ab" * 32}


def test_an_edited_pin_breaks_the_manifest_signature() -> None:
    """W11: the Ed25519 manifest covers tool_pins, so a pin cannot be edited unseen."""
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()
    trust = {
        "version": 1,
        "keys": [
            {"key_id": "ops", "public_key": base64.b64encode(public).decode(), "revoked": False}
        ],
    }
    document = {"version": 1, "servers": [_server(tool_pins={"read_file": "a" * 64})]}
    signed = sign_mcp_config_document(document, key_id="ops", private_key=key)
    assert verify_mcp_config_signature(signed, trust) == "ops"
    edited = json.loads(json.dumps(signed))
    edited["servers"][0]["tool_pins"]["read_file"] = "b" * 64
    with pytest.raises(McpConfigError):
        verify_mcp_config_signature(edited, trust)


async def test_a_tool_that_is_not_advertised_exactly_once_cannot_be_pinned() -> None:
    """mcp-pin refuses a requested tool the server does not advertise, or advertises twice."""
    with pytest.raises(McpToolDescriptorError, match="advertised 0 times"):
        await _client(_Session([_tool()])).pin_descriptors("fs", ("absent",))
    with pytest.raises(McpToolDescriptorError, match="advertised 2 times"):
        await _client(_Session([_tool(), _tool()])).pin_descriptors("fs", ("read_file",))


def test_mcp_pin_reports_a_missing_sdk_with_the_usage_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A runtime failure such as a missing SDK is reported, never raised."""

    class NoSdkClient:
        async def pin_descriptors(self, _server: str, _tools: tuple[str, ...]) -> list[Any]:
            raise McpDependencyError(MCP_EXTRA_HINT)

    monkeypatch.setattr(cli_mcp_call, "_build_client", lambda _path, **_kwargs: NoSdkClient())
    assert _run(["mcp-pin", "fs", "--config", "c.json"]) == 2
    assert "optional extra" in capsys.readouterr().out
