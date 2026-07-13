# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li

import json
from pathlib import Path

import pytest

from synapse_channel import opencode_claim_guard
from synapse_channel.file_claim_guard import GuardVerdict, MutationRequest
from synapse_channel.opencode_claim_guard import (
    OpenCodeClaimGuardError,
    evaluate_hook_event,
    parse_hook_request,
)


def _event(tool: str, tool_input: object, *, cwd: str = "/repo") -> str:
    return json.dumps(
        {
            "hook_event_name": "tool.execute.before",
            "tool_name": tool,
            "session_id": "ses-1",
            "tool_use_id": "call-1",
            "cwd": cwd,
            "tool_input": tool_input,
        }
    )


@pytest.mark.parametrize("tool", ["edit", "write"])
def test_parses_native_file_path_tools(tool: str) -> None:
    request = parse_hook_request(_event(tool, {"filePath": "src/a.py"}))
    assert request.cwd == Path("/repo")
    assert request.file_paths == (Path("src/a.py"),)


def test_parses_native_apply_patch_tool() -> None:
    request = parse_hook_request(
        _event(
            "apply_patch",
            {"patchText": "*** Begin Patch\n*** Update File: a.py\n@@\n-x\n+y\n*** End Patch"},
        )
    )
    assert request.file_paths == (Path("a.py"),)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "JSON object"),
        (_event("bash", {}), "only edit, write, and apply_patch"),
        (_event("edit", []), "tool_input object"),
        (_event("edit", {"filePath": "a"}, cwd="relative"), "must be absolute"),
        (_event("edit", {}), "filePath"),
        (
            _event(
                "apply_patch",
                {"patchText": "*** Begin Patch\n*** Unknown: x\n*** End Patch"},
            ),
            "unsupported control line",
        ),
    ],
)
def test_invalid_native_event_fails_closed(raw: str, message: str) -> None:
    with pytest.raises(OpenCodeClaimGuardError, match=message):
        parse_hook_request(raw)


@pytest.mark.asyncio
async def test_evaluator_converts_malformed_input_to_denial() -> None:
    verdict = await evaluate_hook_event(
        "not-json",
        identity="seat/one",
        uri="ws://unused",
        token=None,
        timeout=1.0,
    )
    assert verdict.allowed is False
    assert "not valid JSON" in verdict.reason


@pytest.mark.asyncio
async def test_valid_event_delegates_to_provider_neutral_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def allow(request: MutationRequest, **kwargs: object) -> GuardVerdict:
        captured["request"] = request
        captured.update(kwargs)
        return GuardVerdict(True)

    monkeypatch.setattr(opencode_claim_guard, "evaluate_mutation_request", allow)
    verdict = await evaluate_hook_event(
        _event("write", {"filePath": "a.py"}),
        identity="seat/one",
        uri="ws://unused",
        token=None,
        timeout=1.0,
    )
    assert verdict.allowed is True
    assert captured["provider"] == "OpenCode"
    assert isinstance(captured["request"], MutationRequest)
