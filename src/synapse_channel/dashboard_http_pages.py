# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard page and snapshot response preparation
"""Prepare dashboard shell and snapshot pages after the owner's HTTP admission.

``/snapshot.json`` and ``/studio.json`` serve the bounded snapshot
(:mod:`synapse_channel.dashboard_bounds`). ``/snapshot.json`` carries a weak
``ETag`` (``W/"…"``) over the document without the stamps that change on every
fetch (:func:`~synapse_channel.dashboard_live_transport.snapshot_fingerprint`,
the rule the live channel uses for ``unchanged``); ``?closed=1`` has its own.
A matching ``If-None-Match`` (weak comparison) is answered with ``304 Not
Modified``. The owner admits the request with the same read-token check before
anything here runs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from synapse_channel.dashboard_bounds import bound_snapshot
from synapse_channel.dashboard_cockpit import (
    COCKPIT_ASSETS as COCKPIT_ASSETS,
)
from synapse_channel.dashboard_cockpit import (
    load_cockpit_asset_bytes as load_cockpit_asset_bytes,
)
from synapse_channel.dashboard_live_transport import snapshot_fingerprint
from synapse_channel.dashboard_studio import (
    STUDIO_REFERENCE_PATH as STUDIO_REFERENCE_PATH,
)
from synapse_channel.dashboard_studio import (
    render_studio_reference_html as render_studio_reference_html,
)
from synapse_channel.dashboard_studio_command import (
    STUDIO_COMMAND_PATH as STUDIO_COMMAND_PATH,
)
from synapse_channel.dashboard_studio_command import (
    render_studio_command_html as render_studio_command_html,
)
from synapse_channel.studio_snapshot import STUDIO_SNAPSHOT_PATH
from synapse_channel.studio_snapshot import build_studio_snapshot as build_studio_snapshot

if TYPE_CHECKING:
    from typing import Protocol

    from synapse_channel.dashboard import DashboardSnapshot

    class ResponseWriter(Protocol):
        """Write a prepared response through the owner's hardened HTTP boundary."""

        def __call__(self, status: HTTPStatus, body: bytes, *, content_type: str) -> None:
            """Write one status, body and media type."""
            ...

    class TaggedResponseWriter(Protocol):
        """Write a prepared response that may carry extra headers, such as an ``ETag``."""

        def __call__(
            self,
            status: HTTPStatus,
            body: bytes,
            *,
            content_type: str,
            extra_headers: tuple[tuple[str, str], ...] = (),
        ) -> None:
            """Write one status, body, media type and any extra headers."""
            ...


@dataclass(frozen=True)
class SnapshotPageBindings:
    """Existing snapshot collection and rendering callbacks for one request.

    Parameters
    ----------
    fetch, unavailable_error
        Existing bounded snapshot fetch and its precise authored failure type.
    html
        Late-bound owner renderer, preserving facade overrides.
    a2a_state_file
        Existing optional A2A read-side state source.
    """

    fetch: Callable[[], DashboardSnapshot]
    unavailable_error: type[Exception]
    html: Callable[[DashboardSnapshot], str]
    a2a_state_file: Path | None


def serve_page_shell(
    path: str,
    *,
    refresh_seconds: int,
    snapshot_timeout_seconds: float,
    write: ResponseWriter,
) -> bool:
    """Serve an existing hub-independent page or asset when its path matches.

    Parameters
    ----------
    path
        Admitted URL path; authentication remains with the HTTP handler.
    refresh_seconds, snapshot_timeout_seconds
        Existing rendering settings for the Studio command shell.
    write
        Owner response writer preserving all browser headers.

    Returns
    -------
    bool
        True when a shell or asset was written; False for later feed routing.
    """
    asset_name = path.lstrip("/")
    if asset_name in COCKPIT_ASSETS:
        write(
            HTTPStatus.OK,
            load_cockpit_asset_bytes(asset_name),
            content_type=COCKPIT_ASSETS[asset_name],
        )
        return True
    if path == STUDIO_REFERENCE_PATH:
        # Renders no live data: a stable visual reference even when the hub is offline.
        write(
            HTTPStatus.OK, render_studio_reference_html().encode("utf-8"), content_type="text/html"
        )
        return True
    if path in {STUDIO_COMMAND_PATH, "/"}:
        # Hub-independent front door fills from /studio.json; classic stays at /classic.
        write(
            HTTPStatus.OK,
            render_studio_command_html(
                poll_seconds=refresh_seconds, snapshot_timeout_seconds=snapshot_timeout_seconds
            ).encode("utf-8"),
            content_type="text/html",
        )
        return True
    return False


def _opaque(tag: str) -> str:
    """Return an entity tag without its weakness marker, for weak comparison."""
    stripped = tag.strip()
    return stripped[2:] if stripped.startswith("W/") else stripped


def _matches(if_none_match: str | None, etag: str) -> bool:
    """Return whether ``If-None-Match`` is ``*`` or names ``etag`` by weak comparison."""
    if if_none_match is None:
        return False
    return any(
        tag.strip() == "*" or _opaque(tag) == _opaque(etag) for tag in if_none_match.split(",")
    )


def serve_snapshot_page(
    path: str,
    ports: SnapshotPageBindings,
    *,
    write: TaggedResponseWriter,
    query: str = "",
    if_none_match: str | None = None,
) -> None:
    """Collect one admitted snapshot and prepare its page representation.

    Parameters
    ----------
    path
        Snapshot, Studio snapshot or classic page path already selected by the owner.
    ports
        Existing per-server collection and rendering callbacks.
    write
        Owner response writer retaining the original status and media type.
    query
        The request's query; ``closed=1`` adds closed tasks to ``/snapshot.json``.
    if_none_match
        The request's ``If-None-Match`` header, if any.
    """
    closed_values = parse_qs(query, keep_blank_values=True).get("closed", ["0"])
    if path == "/snapshot.json" and closed_values not in (["0"], ["1"]):
        write(HTTPStatus.BAD_REQUEST, b"closed must be 0 or 1\n", content_type="text/plain")
        return
    try:
        snapshot = ports.fetch()
    except TimeoutError:
        write(
            HTTPStatus.SERVICE_UNAVAILABLE,
            b"snapshot busy or collection deadline exceeded; retry later\n",
            content_type="text/plain",
        )
        return
    except ports.unavailable_error as exc:
        write(HTTPStatus.SERVICE_UNAVAILABLE, f"{exc}\n".encode(), content_type="text/plain")
        return
    if path == "/snapshot.json":
        document = bound_snapshot(
            snapshot.to_dict(a2a_state_file=ports.a2a_state_file), closed=closed_values == ["1"]
        )
        etag = f'W/"{snapshot_fingerprint(document)}"'
        if _matches(if_none_match, etag):
            write(
                HTTPStatus.NOT_MODIFIED,
                b"",
                content_type="application/json",
                extra_headers=(("ETag", etag),),
            )
            return
        body = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        write(HTTPStatus.OK, body, content_type="application/json", extra_headers=(("ETag", etag),))
        return
    if path == STUDIO_SNAPSHOT_PATH:
        studio = build_studio_snapshot(
            bound_snapshot(snapshot.to_dict(a2a_state_file=ports.a2a_state_file))
        )
        write(
            HTTPStatus.OK,
            json.dumps(studio, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )
        return
    # /classic and /index.html retain server-side hub snapshot HTML (pre-Studio cockpit).
    write(HTTPStatus.OK, ports.html(snapshot).encode("utf-8"), content_type="text/html")
