# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote inbox pages against the reply limit
"""A message larger than the HTTP reply limit must not stall the seat's inbox reader."""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx2
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from hub_e2e_helpers import running_hub
from mcp_http_helpers import access_token, https_server, operation_meta, provision
from synapse_channel.core.hub import SynapseHub
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app
from synapse_channel.mcp.http_inbox_bound import OVERSIZE_NOTE, fit_inbox_page, oversize_row

REFUSAL = "^remote MCP reply exceeds configured limit$"
LARGE = "x" * 300000


def _row(seq: int, payload: str, **extra: Any) -> dict[str, Any]:
    """Build one journal row as the bridge hands it to the transport."""
    return {
        "seq": seq,
        "type": "chat",
        "sender": "ALPHA/bob",
        "target": "ALPHA/alice",
        "payload": payload,
        **extra,
    }


def _result(rows: list[dict[str, Any]], *, cursor: int, has_more: bool) -> CallToolResult:
    """Wrap one page the way the SDK wraps a tool that returns text."""
    text = json.dumps(
        {"hub_id": "syn-unit", "cursor": cursor, "has_more": has_more, "messages": rows}
    )
    return CallToolResult(
        content=[TextContent(type="text", text=text)], structured_content={"result": text}
    )


def _page(result: CallToolResult) -> dict[str, Any]:
    """Read the page back and require both copies of the text to agree."""
    assert result.structured_content is not None
    text = result.structured_content["result"]
    assert [item.text for item in result.content if isinstance(item, TextContent)] == [text]
    page = json.loads(text)
    assert isinstance(page, dict)
    return page


def _under(limit: int) -> Any:
    """Measure a result by the length of its page text."""

    def fits(value: object) -> bool:
        assert isinstance(value, CallToolResult) and value.structured_content is not None
        return len(value.structured_content["result"]) <= limit

    return fits


def test_page_within_the_limit_is_left_untouched() -> None:
    """A page that fits keeps its rows, cursor and continuation flag."""
    result = _result([_row(4, "one"), _row(6, "two")], cursor=9, has_more=False)
    before = result.model_dump_json()
    fit_inbox_page(result, _under(10_000))
    assert result.model_dump_json() == before


def test_rows_are_dropped_from_the_end_and_the_cursor_follows_the_last_kept_row() -> None:
    """The reader continues at the first dropped row, never past it."""
    result = _result(
        [_row(4, "one"), _row(6, "y" * 400), _row(8, "three")], cursor=11, has_more=False
    )
    fit_inbox_page(result, _under(300))
    page = _page(result)
    assert page == {
        "hub_id": "syn-unit",
        "cursor": 4,
        "has_more": True,
        "messages": [_row(4, "one")],
    }


def test_first_row_that_cannot_fit_alone_becomes_a_marker_the_reader_can_pass() -> None:
    """Only fixed fields leave the row; a sender-chosen field cannot name a marker."""
    large = _row(6, "y" * 400, oversize=False, note="sender text", extra="z" * 50)
    result = _result([large, _row(8, "three")], cursor=11, has_more=False)
    fit_inbox_page(result, _under(350))
    page = _page(result)
    assert page == {
        "hub_id": "syn-unit",
        "cursor": 6,
        "has_more": True,
        "messages": [
            {
                "seq": 6,
                "sender": "ALPHA/bob",
                "target": "ALPHA/alice",
                "oversize": True,
                "row_bytes": len(json.dumps(large)),
                "note": OVERSIZE_NOTE,
            }
        ],
    }
    assert oversize_row(large) == page["messages"][0]


def test_row_that_claims_to_be_a_marker_is_still_replaced() -> None:
    """A large row carrying ``oversize`` itself gets the transport's own marker."""
    large = _row(6, "y" * 400, oversize=True)
    result = _result([large], cursor=6, has_more=False)
    fit_inbox_page(result, _under(350))
    assert _page(result)["messages"] == [oversize_row(large)]


def test_marker_that_does_not_fit_is_refused() -> None:
    """A limit below one marker row keeps the fixed refusal."""
    result = _result([_row(6, "y" * 400)], cursor=6, has_more=False)
    with pytest.raises(RuntimeError, match=REFUSAL):
        fit_inbox_page(result, _under(40))


def test_empty_page_over_the_limit_is_refused() -> None:
    """Nothing can be dropped from a page without rows."""
    result = _result([], cursor=6, has_more=False)
    with pytest.raises(RuntimeError, match=REFUSAL):
        fit_inbox_page(result, _under(10))


@pytest.mark.parametrize(
    "value",
    [
        "not a tool result",
        CallToolResult(content=[TextContent(type="text", text="unstructured")]),
    ],
)
def test_value_without_a_structured_page_is_refused(value: object) -> None:
    """Only a structured tool result can be shortened."""
    with pytest.raises(RuntimeError, match=REFUSAL):
        fit_inbox_page(value, lambda _value: False)


async def test_large_message_over_https_is_named_and_the_reader_moves_on(tmp_path: Path) -> None:
    """Messages of 3, 300,000 and 5 characters are all accounted for at the default limit."""
    issuer = Ed25519PrivateKey.generate()
    path = tmp_path / "grants.json"
    trust = provision(path, issuer)
    policy = json.loads(path.read_text())
    policy["subjects"]["alice"]["projects"]["ALPHA"]["tools"].append("synapse_inbox_page")
    path.write_text(json.dumps(policy))
    store = EventStore(tmp_path / "hub.db")
    hub = SynapseHub(
        hub_id="syn-inbox-bound",
        journal=store,
        identity_trust_bundle=trust,
        require_identity_binding=True,
    )
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=path,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
                operation_timeout=10,
                request_bytes=1048576,
            )
            async with https_server(app, tmp_path) as (url, tls), AsyncExitStack() as stack:
                sessions: dict[str, ClientSession] = {}
                for subject in ("alice", "bob"):
                    client = await stack.enter_async_context(
                        httpx2.AsyncClient(
                            verify=tls,
                            timeout=20,
                            trust_env=False,
                            headers={
                                "Authorization": "Bearer "
                                + access_token(
                                    issuer, sub=subject, scope="synapse:read synapse:mutate"
                                )
                            },
                        )
                    )
                    read, write = await stack.enter_async_context(
                        streamable_http_client(url + "/mcp", http_client=client)
                    )
                    session = await stack.enter_async_context(ClientSession(read, write))
                    await session.initialize()
                    sessions[subject] = session
                alice, bob = sessions["alice"], sessions["bob"]
                for label, text in (("before", "one"), ("large", LARGE), ("after", "three")):
                    sent = await bob.call_tool(
                        "synapse_send",
                        {"target": "ALPHA/alice", "message": text},
                        meta=operation_meta("bound-" + label),
                    )
                    assert not sent.is_error

                pages: list[dict[str, Any]] = []
                arguments: dict[str, Any] = {}
                for _read in range(4):
                    result = await alice.call_tool("synapse_inbox_page", arguments)
                    assert not result.is_error
                    assert isinstance(result, CallToolResult)
                    page = _page(result)
                    pages.append(page)
                    arguments = {"hub_id": page["hub_id"], "after_seq": page["cursor"]}

                first, second, third, last = pages
                assert [row["payload"] for row in first["messages"]] == ["one"]
                assert first["has_more"] is True
                assert first["cursor"] == first["messages"][0]["seq"]

                (marker,) = second["messages"]
                assert marker == {
                    "seq": marker["seq"],
                    "sender": "ALPHA/bob",
                    "target": "ALPHA/alice",
                    "oversize": True,
                    "row_bytes": marker["row_bytes"],
                    "note": OVERSIZE_NOTE,
                }
                assert marker["seq"] > first["cursor"]
                assert marker["row_bytes"] > len(LARGE)
                assert second["has_more"] is True
                assert second["cursor"] == marker["seq"]

                assert [row["payload"] for row in third["messages"]] == ["three"]
                assert third["messages"][0]["seq"] > marker["seq"]
                assert third["has_more"] is False
                assert last["messages"] == [] and last["has_more"] is False

                repeated = await alice.call_tool(
                    "synapse_inbox_page",
                    {"hub_id": first["hub_id"], "after_seq": first["cursor"]},
                )
                assert isinstance(repeated, CallToolResult)
                assert _page(repeated) == second
    finally:
        store.close()
