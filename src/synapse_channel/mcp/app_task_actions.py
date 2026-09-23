# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded local app task MCP actions
"""Expose task state without returning private prompts or account records."""

from __future__ import annotations

import json
from typing import Any

from synapse_channel.core import app_tasks


def _public(task: dict[str, Any]) -> str:
    """Keep the prompt, returned content and private allowance source local."""
    allowance = task["allowance"]
    return json.dumps(
        {
            "task_id": task["task_id"],
            "state": task["state"],
            "expires_at": task["bundle"]["expires_at"],
            "allowance": {
                "unit": allowance["unit"],
                "source_age_seconds": allowance["source_age_seconds"],
                "balance_evidence": allowance["balance_evidence"],
            },
            "usage": task["usage"],
        },
        sort_keys=True,
    )


def offer(bundle: dict[str, Any], actor: str) -> str:
    """Queue a caller-supplied prompt using the private C04 ledger."""
    try:
        return _public(app_tasks.offer(app_tasks.default_app_task_store(), bundle, actor=actor))
    except app_tasks.AppTaskError as exc:
        if str(exc).startswith("allowance "):
            raise app_tasks.AppTaskError("private allowance unavailable") from None
        raise


def status(task_id: str) -> str:
    """Read redacted task state."""
    return _public(app_tasks.get(app_tasks.default_app_task_store(), task_id))


def attach(task_id: str, result: dict[str, Any], actor: str) -> str:
    """Attach untrusted returned content without echoing it to the model."""
    return _public(
        app_tasks.attach(
            app_tasks.default_app_task_store(),
            task_id,
            result,
            actor=actor,
            require_offerer=True,
        )
    )
