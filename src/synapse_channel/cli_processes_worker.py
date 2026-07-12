# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — process CLI worker command
"""Worker process command for the ``synapse`` CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlparse

from synapse_channel.cli_processes_runtime import _run
from synapse_channel.client.llm_worker import SynapseLLMWorker
from synapse_channel.core.identity_keys import IdentityKeyError
from synapse_channel.core.logging_setup import configure_logging

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


def _egress_warning(provider: str, base_url: str) -> str | None:
    """Return a one-line warning when a worker will send channel context off-host.

    The ``openai`` provider posts recent channel context and the bearer token read
    from ``--api-key-env`` to its configured endpoint; any provider pointed at a
    non-loopback ``base_url`` likewise leaves the machine. The offline ``rule``
    backend never touches the network and returns ``None``.

    Parameters
    ----------
    provider : str
        The worker backend (``openai``, ``ollama``, ``rule``, or ``tiered``).
    base_url : str
        The model endpoint the worker will call.

    Returns
    -------
    str or None
        A warning describing what leaves the host, or ``None`` when the worker
        stays local.
    """
    if provider == "rule":
        return None
    host = (urlparse(base_url).hostname or "").lower()
    if provider != "openai" and host in _LOCAL_HOSTS:
        return None
    what = "recent channel context" + (" and the API key" if provider == "openai" else "")
    return f"this worker SENDS {what} to {base_url or 'the configured endpoint'}"


def _cmd_worker(
    args: argparse.Namespace,
    *,
    runner: Callable[[Coroutine[Any, Any, None]], None] = _run,
    logging_configurator: Callable[..., object] = configure_logging,
    on_worker: Callable[[SynapseLLMWorker], None] | None = None,
) -> int:
    """Run a single on-channel model worker until interrupted.

    ``--prefix`` is prepended to ``--name`` to form the registered identity, so
    the same role can run under several projects without a name clash on the hub.
    A worker that will send channel context off the local machine prints a loud
    egress warning to stderr before it starts.
    """
    logging_configurator(log_format=args.log_format, level=args.log_level)
    name = f"{args.prefix}{args.name}"
    warning = _egress_warning(args.provider, args.base_url)
    if warning:
        print(f"[{name}] WARNING: {warning}.", file=sys.stderr)
    try:
        worker = SynapseLLMWorker(
            name=name,
            uri=args.uri,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            max_context=args.max_context,
            reply_target_mode=args.reply_target_mode,
            min_reply_interval=args.min_reply_interval,
            token=args.token,
            task_classes=tuple(args.task_class) if args.task_class else ("chat",),
            heavy_model=args.heavy_model,
            capability_card_key_path=getattr(args, "capability_card_key", None),
            capability_card_key_id=getattr(args, "capability_card_key_id", ""),
            capability_card_project=getattr(args, "capability_card_project", ""),
            capability_card_lifetime_seconds=getattr(
                args, "capability_card_lifetime_seconds", 300.0
            ),
        )
    except (ImportError, IdentityKeyError, ValueError) as exc:
        print(f"[{name}] configuration error: {exc}", file=sys.stderr)
        return 2
    if on_worker is not None:
        on_worker(worker)
    try:
        runner(worker.run())
    except KeyboardInterrupt:
        print(f"\n[{name}] stopped by user.")
    return 0
