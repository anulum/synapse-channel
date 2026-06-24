# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the opt-in text/JSON logging configuration

from __future__ import annotations

import io
import json
import logging
import sys

from synapse_channel.core.logging_setup import JsonFormatter, configure_logging


def test_json_formatter_emits_base_fields() -> None:
    record = logging.makeLogRecord(
        {
            "name": "synapse.hub",
            "levelname": "INFO",
            "msg": "claimed %s",
            "args": ("T1",),
            "created": 1700.0,
        }
    )
    out = json.loads(JsonFormatter().format(record))
    assert out == {"ts": 1700.0, "level": "INFO", "logger": "synapse.hub", "message": "claimed T1"}


def test_json_formatter_merges_extra_context() -> None:
    record = logging.makeLogRecord(
        {"name": "synapse", "levelname": "INFO", "msg": "m", "agent": "A", "task_id": "T1"}
    )
    out = json.loads(JsonFormatter().format(record))
    assert out["agent"] == "A"
    assert out["task_id"] == "T1"


def test_json_formatter_renders_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.makeLogRecord(
            {"name": "synapse", "levelname": "ERROR", "msg": "failed", "exc_info": sys.exc_info()}
        )
    out = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in out["exc_info"]


def test_configure_logging_text_installs_one_quiet_handler() -> None:
    logger = configure_logging(log_format="text", level="WARNING")
    assert logger.name == "synapse"
    assert len(logger.handlers) == 1
    assert logger.level == logging.WARNING
    assert logger.propagate is False
    assert not isinstance(logger.handlers[0].formatter, JsonFormatter)


def test_configure_logging_json_uses_the_json_formatter() -> None:
    logger = configure_logging(log_format="json")
    assert isinstance(logger.handlers[0].formatter, JsonFormatter)


def test_configure_logging_is_idempotent() -> None:
    configure_logging()
    logger = configure_logging()
    assert len(logger.handlers) == 1


def test_configure_logging_unknown_format_falls_back_to_text() -> None:
    logger = configure_logging(log_format="xml")
    assert not isinstance(logger.handlers[0].formatter, JsonFormatter)


def test_configure_logging_writes_json_lines_to_the_given_stream() -> None:
    buffer = io.StringIO()
    logger = configure_logging(log_format="json", level="INFO", stream=buffer)
    logger.info("hello", extra={"agent": "A"})
    record = json.loads(buffer.getvalue())
    assert record["message"] == "hello"
    assert record["agent"] == "A"
    assert record["level"] == "INFO"
