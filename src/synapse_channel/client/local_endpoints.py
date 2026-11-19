# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the operator's list of local model endpoints
"""The model endpoints a local worker may reach, as the operator declares them.

A worker's ``ollama`` and ``tiered`` providers speak the OpenAI-compatible
protocol to any base URL. With a hosted URL and a key in the environment they
would send paid calls with no ``--allow-paid-api``, no budget and no quote. So
they reach only endpoints this list declares. A hosted model is reached only
through a named paid profile, which has those controls.

The list is an operator-owned JSON file::

    {"schema": "synapse-local-endpoints.v1", "endpoints": ["http://localhost:11434/v1"]}

It is read without following a symlink and must be readable by its owner only.
Without a file at the documented default path, only the local Ollama default is
declared, so a default setup keeps working.

Matching is exact on scheme, host and port after normalisation, and on the path
as a prefix that ends at a segment boundary. Loopback is never assumed to be
local: a gateway on ``localhost`` can forward to a paid API, so it is local only
when it is listed. A listed endpoint that forwards to a paid API is the
operator's responsibility.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlsplit

from synapse_channel.core.errors import SynapseError
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes

LOCAL_ENDPOINTS_SCHEMA = "synapse-local-endpoints.v1"
"""The schema name every local endpoint list carries."""

LOCAL_ENDPOINTS_FILENAME = "local-endpoints.json"
"""The list's file name in the user's Synapse configuration directory."""

DEFAULT_LOCAL_ENDPOINTS: tuple[str, ...] = (
    "http://localhost:11434/v1",
    "http://127.0.0.1:11434/v1",
)
"""The endpoints declared when no list file exists: the local Ollama default."""

MAX_LOCAL_ENDPOINTS_BYTES = 65536
"""The largest list file read."""

_DEFAULT_PORTS: Mapping[str, int] = {"http": 80, "https": 443}


class LocalEndpointError(SynapseError, ValueError):
    """An endpoint list or a model endpoint that a local worker must refuse."""

    code = "local_endpoint"


def default_local_endpoints_path(environ: Mapping[str, str] | None = None) -> Path:
    """Return ``$XDG_CONFIG_HOME/synapse/local-endpoints.json``, or the same under ``~/.config``.

    Parameters
    ----------
    environ : Mapping[str, str] or None
        The environment; :data:`os.environ` by default.

    Returns
    -------
    Path
        The documented default location of the list.
    """
    env = os.environ if environ is None else environ
    configured = env.get("XDG_CONFIG_HOME", "").strip()
    root = Path(configured) if configured else Path.home() / ".config"
    return root / "synapse" / LOCAL_ENDPOINTS_FILENAME


def normalise_endpoint(url: str) -> tuple[str, str, int, str]:
    """Return ``(scheme, host, port, path)`` of an HTTP base URL in comparable form.

    The scheme and host are lower-cased, the default port is made explicit and
    a trailing slash is removed from the path.

    Raises
    ------
    LocalEndpointError
        For a URL that is not plain HTTP or HTTPS with a host, that carries
        credentials, a query or a fragment, or that has an invalid port. The
        message never repeats the URL, which could hold a credential.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        raise LocalEndpointError("a model endpoint must be an http or https URL with a host")
    if parts.username is not None or parts.password is not None:
        raise LocalEndpointError("a model endpoint must not carry credentials")
    if parts.query or parts.fragment:
        raise LocalEndpointError("a model endpoint must not carry a query or a fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise LocalEndpointError("a model endpoint has an invalid port") from exc
    return scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[scheme], parts.path.rstrip("/")


def endpoint_is_listed(url: str, listed: Iterable[str]) -> bool:
    """Return whether ``url`` is one of the ``listed`` endpoints or lies below one.

    Scheme, host and port must be equal; the path must equal a listed path or
    continue it after a ``/``, so ``/v1`` covers ``/v1/chat`` but not ``/v1x``.
    """
    scheme, host, port, path = normalise_endpoint(url)
    for entry in listed:
        entry_scheme, entry_host, entry_port, entry_path = normalise_endpoint(entry)
        if (scheme, host, port) != (entry_scheme, entry_host, entry_port):
            continue
        if path == entry_path or path.startswith(f"{entry_path}/"):
            return True
    return False


def load_local_endpoints(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    """Return the operator's declared local endpoints.

    Parameters
    ----------
    path : Path or None
        An explicit list file, which must exist. ``None`` reads the default
        path when a file is there, and otherwise returns
        :data:`DEFAULT_LOCAL_ENDPOINTS`.
    environ : Mapping[str, str] or None
        The environment used to find the default path.

    Returns
    -------
    tuple[str, ...]
        The declared base URLs, each a valid endpoint.

    Raises
    ------
    LocalEndpointError
        When the file is a symlink, not owner-only, unreadable, larger than
        :data:`MAX_LOCAL_ENDPOINTS_BYTES`, or not exactly the documented shape.
    """
    target = default_local_endpoints_path(environ) if path is None else path
    if path is None and not os.path.lexists(target):
        return DEFAULT_LOCAL_ENDPOINTS
    try:
        raw = read_owner_only_file_bytes(
            target, purpose="local endpoint list", max_bytes=MAX_LOCAL_ENDPOINTS_BYTES
        )
    except (OSError, SecurePathError) as exc:
        raise LocalEndpointError(f"cannot read the local endpoint list: {exc}") from exc
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalEndpointError("the local endpoint list must be a UTF-8 JSON object") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "endpoints"}
        or document["schema"] != LOCAL_ENDPOINTS_SCHEMA
        or not isinstance(document["endpoints"], list)
        or not all(isinstance(entry, str) for entry in document["endpoints"])
    ):
        raise LocalEndpointError(
            f'the local endpoint list must be {{"schema": "{LOCAL_ENDPOINTS_SCHEMA}", '
            '"endpoints": [<base URL>, ...]}'
        )
    endpoints = tuple(document["endpoints"])
    for entry in endpoints:
        normalise_endpoint(entry)
    return endpoints
