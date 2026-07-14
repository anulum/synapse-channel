# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — terminal-safety tests for read-only hub query rendering
"""Terminal-control regressions for human-readable hub query output."""

from __future__ import annotations

import pytest

from synapse_channel.cli_query_rendering import (
    _print_board,
    _print_manifest,
    _render_approvals,
    _render_dead_letters,
    _render_state,
    _render_who,
)


def test_hub_query_renderers_make_remote_controls_visible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hostile = "remote\x1b]52;c;YQ==\x07\nforged\u202e"

    _render_who([hostile])
    _render_state(
        {
            "active_claims": [
                {
                    "task_id": hostile,
                    "status": hostile,
                    "owner": hostile,
                    "paths": [hostile],
                    "checkpoint": hostile,
                    "git": {"branch": hostile, "base": hostile},
                }
            ]
        }
    )
    _render_dead_letters(
        {
            "dead_letters": [
                {
                    "target": hostile,
                    "count": 1,
                    "last_sender": hostile,
                    "last_ts": 0.0,
                }
            ]
        }
    )
    _render_approvals(
        {
            "pending_relay_approvals": [
                {
                    "action": hostile,
                    "namespace": hostile,
                    "task_id": hostile,
                    "requester": hostile,
                }
            ]
        }
    )
    _print_board(
        {
            "tasks": [
                {
                    "status": hostile,
                    "task_id": hostile,
                    "title": hostile,
                    "depends_on": [hostile],
                }
            ],
            "ready": [hostile],
            "progress": [{"author": hostile, "kind": hostile, "task_id": hostile, "text": hostile}],
        }
    )
    _print_manifest(
        [
            {
                "agent": hostile,
                "task_classes": [hostile],
                "model": hostile,
                "description": hostile,
                "verification": {"result": hostile},
            }
        ]
    )

    rendered = capsys.readouterr().out
    assert "\\x1b]52;c;YQ==\\x07\\nforged\\u202e" in rendered
    assert "\x1b" not in rendered
    assert "\x07" not in rendered
    assert "\u202e" not in rendered
