# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — validated current-owned work reads
"""Validate owner-bound seek pages and bounded canonical-JSON record chunks."""

from __future__ import annotations

import json
import uuid
from typing import Any

from websockets.exceptions import WebSocketException

from synapse_channel.client.agent import SynapseAgent
from synapse_channel.core.owned_work import OwnedQuery, is_digest
from synapse_channel.core.protocol import MessageType
from synapse_channel.mcp.hub_inbox import AwaitReply

_COMMON = {"version", "available", "identity", "hub_id", "project", "mode"}


def _validate_binding(value: dict[str, Any], query: OwnedQuery, hub_id: str) -> None:
    """Require exact owner, project, protocol and connected hub before parsing data."""
    if (
        not _COMMON <= value.keys()
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["available"] is not True
        or value["identity"] != query.identity
        or not hub_id
        or len(hub_id) > 256
        or value["hub_id"] != hub_id
        or (query.hub_id and query.hub_id != hub_id)
        or value["project"] != query.project
        or value["mode"] != query.mode
        or type(value.get("has_more")) is not bool
    ):
        raise ValueError("invalid owned work binding")


def _validate_metadata(item: object, previous: str, cursor: str) -> str:
    """Require one strictly ordered opaque work key and its mutation generation."""
    if (
        not isinstance(item, dict)
        or set(item) != {"kind", "key", "item_version"}
        or item["kind"] not in ("tasks", "claims")
        or not is_digest(item["key"])
        or not is_digest(item["item_version"])
        or not previous < item["key"] <= cursor
    ):
        raise ValueError("invalid owned work metadata")
    key: str = item["key"]
    return key


def _validate_list(value: dict[str, Any], query: OwnedQuery) -> None:
    """Refuse oversized or non-progressing seek pages and changed revisions."""
    if (
        set(value) != _COMMON | {"revision", "cursor", "has_more", "items"}
        or not is_digest(value["revision"])
        or (query.revision and value["revision"] != query.revision)
        or not is_digest(value["cursor"], empty=True)
        or value["cursor"] < query.after_key
        or not isinstance(value["items"], list)
        or len(value["items"]) > query.limit
        or (value["has_more"] and value["cursor"] <= query.after_key)
    ):
        raise ValueError("invalid owned work seek page")
    previous = query.after_key
    for item in value["items"]:
        previous = _validate_metadata(item, previous, value["cursor"])


def _validate_positions(value: dict[str, Any], query: OwnedQuery) -> None:
    """Require exact integer offsets and consistent bounded fragment lengths."""
    if (
        any(type(value[k]) is not int for k in ("offset", "next_offset", "total_chars"))
        or value["offset"] != query.offset
        or not query.offset < value["next_offset"] <= value["total_chars"]
        or value["total_chars"] > 9223372036854775807
        or value["has_more"] != (value["next_offset"] < value["total_chars"])
    ):
        raise ValueError("invalid owned record position")
    if (
        type(value["content"]) is not str
        or not value["content"].isascii()
        or not 0 < len(value["content"]) <= query.limit_chars
        or len(value["content"]) != value["next_offset"] - query.offset
    ):
        raise ValueError("invalid owned record content")


def _validate_read(value: dict[str, Any], query: OwnedQuery) -> None:
    """Fence a full-record fragment to the exact requested key and digest."""
    if (
        set(value)
        != _COMMON
        | {
            "kind",
            "key",
            "item_version",
            "offset",
            "next_offset",
            "total_chars",
            "has_more",
            "content_sha256",
            "content",
        }
        or value["kind"] not in ("tasks", "claims")
        or value["key"] != query.key
        or value["item_version"] != query.item_version
        or not is_digest(value["content_sha256"])
        or (query.digest and value["content_sha256"] != query.digest)
    ):
        raise ValueError("invalid owned record binding")
    _validate_positions(value, query)


def validate_owned_page(value: object, query: OwnedQuery, connected_hub: str) -> dict[str, Any]:
    """Validate binding and the exact list or record contract before exposing work."""
    if not isinstance(value, dict):
        raise ValueError("invalid owned work response")
    _validate_binding(value, query, connected_hub)
    if query.mode == "list":
        _validate_list(value, query)
    else:
        _validate_read(value, query)
    return value


async def read_owned_work(agent: SynapseAgent, await_reply: AwaitReply, query: OwnedQuery) -> str:
    """Use the admitted bridge connection without local files or a global snapshot."""
    if query.identity != agent.name:
        raise ValueError("owned work query identity changed")
    expected_hub = agent.hub_id
    if not agent.ready_event.is_set():
        raise RuntimeError("owned work unavailable; caller progress unchanged")
    request_id = uuid.uuid4().hex

    async def send() -> None:
        await agent.send_message(
            MessageType.HISTORY_REQUEST,
            target="System",
            request_id=request_id,
            owned_work_query=query.encode(),
        )

    try:
        response = await await_reply(
            lambda frame: (
                frame.get("type") == MessageType.HISTORY_SNAPSHOT
                and frame.get("request_id") == request_id
            ),
            send,
        )
        if (
            response is None
            or response.get("target") != query.identity
            or agent.hub_id != expected_hub
        ):
            raise ValueError("owned response binding changed")
        page = validate_owned_page(response.get("owned_work"), query, expected_hub)
        return json.dumps(page, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, OSError, WebSocketException):
        raise RuntimeError("owned work unavailable; caller progress unchanged") from None
