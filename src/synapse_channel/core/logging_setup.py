# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — opt-in text/JSON logging configuration for the daemon commands
"""Logging configuration for the long-running ``synapse`` commands.

The library never configures logging — attaching handlers is the application's
choice, not a library's. The long-running entry points (``synapse hub`` and
``synapse worker``) call :func:`configure_logging` to attach exactly one stream
handler to the ``synapse`` logger, in either a human-readable text format or
line-delimited JSON suited to a log aggregator. Short-lived query commands print
their result to stdout and need no logging at all.
"""

from __future__ import annotations

import json
import logging
from typing import IO, Any

ROOT_LOGGER_NAME = "synapse"
"""The logger namespace every module logs under (``synapse.hub``, ``synapse.worker``)."""

LOG_FORMATS = ("text", "json")
"""Accepted ``--log-format`` values."""

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
"""Accepted ``--log-level`` values."""

DEFAULT_LOG_FORMAT = "text"
"""Format used when none is requested."""

DEFAULT_LOG_LEVEL = "INFO"
"""Level used when none is requested; a hub is a server, so it logs its activity."""

_TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
"""Human-readable single-line layout."""

# Attributes present on every freshly-made LogRecord. Anything else on a record is
# caller-supplied structured context (e.g. ``logger.info(..., extra={"agent": "A"})``)
# and is merged into the JSON output rather than dropped.
_RESERVED_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a log record as one JSON object per line.

    The base fields are the timestamp, level, logger name, and message. An
    exception, when attached, is rendered into an ``exc_info`` string, and any
    non-reserved attribute supplied via ``extra=`` is merged in, so structured
    context survives into the aggregator instead of being flattened into the
    message text.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Return the record serialised as a compact JSON object."""
        payload: dict[str, Any] = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_KEYS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    *,
    log_format: str = DEFAULT_LOG_FORMAT,
    level: str = DEFAULT_LOG_LEVEL,
    stream: IO[str] | None = None,
) -> logging.Logger:
    """Attach a single stream handler to the ``synapse`` logger and return it.

    Idempotent: each call replaces the logger's handlers with exactly one, so a
    re-invocation never stacks duplicates. Propagation is turned off so the record
    is emitted once by this handler even when the embedding process also configures
    the root logger.

    Parameters
    ----------
    log_format : str, optional
        ``"text"`` (human-readable) or ``"json"`` (one JSON object per line); an
        unrecognised value falls back to text.
    level : str, optional
        A standard logging level name. Defaults to :data:`DEFAULT_LOG_LEVEL`.
    stream : IO[str] or None, optional
        Destination stream; ``None`` uses ``sys.stderr`` (the handler default).

    Returns
    -------
    logging.Logger
        The configured ``synapse`` logger.
    """
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler: logging.Handler = logging.StreamHandler(stream)
    handler.setFormatter(
        JsonFormatter() if log_format == "json" else logging.Formatter(_TEXT_FORMAT)
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger
