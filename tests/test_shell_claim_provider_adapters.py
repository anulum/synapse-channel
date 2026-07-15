# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — cross-provider shell claim adapter contracts

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from synapse_channel.claude_claim_guard import (
    evaluate_hook_event as evaluate_claude,
)
from synapse_channel.claude_claim_guard import parse_hook_request as parse_claude
from synapse_channel.codex_claim_guard import evaluate_hook_event as evaluate_codex
from synapse_channel.codex_claim_guard import parse_hook_request as parse_codex
from synapse_channel.file_claim_guard import GuardVerdict
from synapse_channel.gemini_claim_guard import evaluate_hook_event as evaluate_gemini
from synapse_channel.gemini_claim_guard import parse_hook_request as parse_gemini
from synapse_channel.grok_claim_guard import evaluate_hook_event as evaluate_grok
from synapse_channel.grok_claim_guard import parse_hook_request as parse_grok
from synapse_channel.kimi_claim_guard import evaluate_hook_event as evaluate_kimi
from synapse_channel.kimi_claim_guard import parse_hook_request as parse_kimi
from synapse_channel.opencode_claim_guard import evaluate_hook_event as evaluate_opencode
from synapse_channel.opencode_claim_guard import parse_hook_request as parse_opencode
from synapse_channel.shell_claim_guard import ShellRequest

Parser = Callable[[str], object]
Evaluator = Callable[..., Awaitable[GuardVerdict]]


def _event(provider: str, root: Path) -> str:
    common = {
        "session_id": f"{provider}-session",
        "cwd": str(root),
    }
    if provider == "claude":
        return json.dumps(
            common
            | {
                "tool_use_id": "tool",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
            }
        )
    if provider == "codex":
        return json.dumps(
            common
            | {
                "tool_use_id": "tool",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
            }
        )
    if provider == "kimi":
        return json.dumps(
            common
            | {
                "tool_call_id": "tool",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
            }
        )
    if provider == "gemini":
        return json.dumps(
            common
            | {
                "timestamp": "2026-07-15T00:00:00Z",
                "hook_event_name": "BeforeTool",
                "tool_name": "run_shell_command",
                "tool_input": {"command": "true"},
            }
        )
    if provider == "grok":
        return json.dumps(
            {
                "sessionId": "grok-session",
                "toolUseId": "tool",
                "cwd": str(root),
                "hookEventName": "PreToolUse",
                "toolName": "run_terminal_command",
                "toolInput": {"command": "true"},
            }
        )
    return json.dumps(
        common
        | {
            "tool_use_id": "tool",
            "hook_event_name": "tool.execute.before",
            "tool_name": "bash",
            "tool_input": {},
        }
    )


_PROVIDERS: tuple[tuple[str, Parser, Evaluator], ...] = (
    ("claude", parse_claude, evaluate_claude),
    ("codex", parse_codex, evaluate_codex),
    ("kimi", parse_kimi, evaluate_kimi),
    ("gemini", parse_gemini, evaluate_gemini),
    ("grok", parse_grok, evaluate_grok),
    ("opencode", parse_opencode, evaluate_opencode),
)


@pytest.mark.parametrize(("provider", "parser", "_evaluator"), _PROVIDERS)
def test_provider_parsers_discard_shell_command(
    tmp_path: Path, provider: str, parser: Parser, _evaluator: Evaluator
) -> None:
    request = parser(_event(provider, tmp_path))
    assert isinstance(request, ShellRequest)
    assert request.session_id == f"{provider}-session"
    assert request.tool_use_id == ("2026-07-15T00:00:00Z" if provider == "gemini" else "tool")
    assert request.cwd == tmp_path
    assert not hasattr(request, "command")


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "_parser", "evaluator"), _PROVIDERS)
async def test_every_provider_delegates_shell_to_whole_worktree_policy(
    tmp_path: Path, provider: str, _parser: Parser, evaluator: Evaluator
) -> None:
    async def state(**_kwargs: object) -> dict[str, Any]:
        return {
            "active_claims": [
                {
                    "task_id": "SHELL",
                    "owner": "seat/one",
                    "status": "claimed",
                    "worktree": str(tmp_path),
                    "paths": [],
                    "git": {"branch": "main", "base": "main"},
                }
            ]
        }

    verdict = await evaluator(
        _event(provider, tmp_path),
        identity="seat/one",
        uri="ws://hub",
        token=None,
        timeout=0.1,
        state_fetcher=state,
        git_runner=lambda _args: f"{tmp_path}\nmain",
    )
    assert verdict.allowed
