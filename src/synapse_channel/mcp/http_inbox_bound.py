# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote inbox pages that fit the reply limit
"""Shorten a caller-cursor inbox page so one large message cannot stall its reader."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mcp.types import CallToolResult, TextContent

OVERSIZE_NOTE = (
    "message exceeds the reply limit of this transport; read it over a native hub connection"
)
_REFUSAL = "remote MCP reply exceeds configured limit"


def oversize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Describe one withheld message by fixed, small fields.

    Parameters
    ----------
    row : dict[str, Any]
        Validated journal row addressed to the reading seat.

    Returns
    -------
    dict[str, Any]
        The row's sequence, sender and target, its serialised size and a note.
        The payload and every other field of the row are left out.
    """
    return {
        "seq": row["seq"],
        "sender": row.get("sender"),
        "target": row.get("target"),
        "oversize": True,
        "row_bytes": len(json.dumps(row)),
        "note": OVERSIZE_NOTE,
    }


def fit_inbox_page(result: object, fits: Callable[[object], bool]) -> None:
    """Rewrite an inbox page in place until it fits, keeping its reader moving.

    Messages are dropped from the end of the page; the cursor becomes the
    sequence of the last message kept and ``has_more`` is set, so the next read
    starts at the first message dropped. A first message that cannot fit alone
    is replaced by :func:`oversize_row`, which lets the caller move past it
    knowingly. The decision whether a row is a marker is local and never read
    from the row, so a sender cannot choose it.

    Parameters
    ----------
    result : object
        Successful tool result holding the page as JSON text.
    fits : Callable[[object], bool]
        The transport's own measure of a result against its reply limit.

    Raises
    ------
    RuntimeError
        When the value is not a structured tool result, or when even a page of
        one marker row does not fit.
    """
    if fits(result):
        return
    if not isinstance(result, CallToolResult) or result.structured_content is None:
        raise RuntimeError(_REFUSAL)
    page = json.loads(result.structured_content["result"])
    rows: list[dict[str, Any]] = page["messages"]
    marked = False
    while True:
        if len(rows) > 1:
            rows = rows[:-1]
        elif rows and not marked:
            rows, marked = [oversize_row(rows[0])], True
        else:
            raise RuntimeError(_REFUSAL)
        text = json.dumps({**page, "messages": rows, "cursor": rows[-1]["seq"], "has_more": True})
        result.content = [TextContent(type="text", text=text)]
        result.structured_content = {"result": text}
        if fits(result):
            return
