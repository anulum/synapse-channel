# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deterministic terminal-provider pane-state fixtures
"""Representative idle, busy, and modal screens for every pane provider."""

from __future__ import annotations

PROVIDER_SCREENS: dict[str, dict[str, str]] = {
    "codex": {
        "idle": "• Turn completed\n\n› \n  gpt-5.6 100% context left\n",
        "busy": "› owner task\n\nWorking (12s) · esc to interrupt\n",
        "modal": "Allow Codex to run this command?\n› 1. Yes\n  2. No\n",
        "update": (
            "✨ Update available! 0.151.0 -> 0.152.0\n"
            "› 1. Update now\n  2. Skip\n  3. Skip until next version\n"
            "Press enter to continue\n"
        ),
    },
    "claude": {
        "idle": "Claude Code\n\n❯ \n  ? for shortcuts\n",
        "busy": "❯ owner task\n\nThinking… esc to interrupt\n",
        "modal": "Do you want to allow this tool?\n❯ 1. Yes\n  2. No\n",
    },
    "kimi": {
        "idle": "Kimi Code\n\n> Ask Kimi\n",
        "busy": "> owner task\n\nRunning tool… press ctrl-c to cancel\n",
        "modal": "Permission required\n> 1. Allow once\n  2. Deny\n",
    },
    "grok": {
        "idle": "Grok CLI\n\n> Ask Grok\n",
        "busy": "> owner task\n\nGenerating… esc to interrupt\n",
        "modal": "Confirm command execution\n> 1. Continue\n  2. Cancel\n",
    },
    "gemini": {
        "idle": "Gemini CLI\n\nType your message or @path/to/file\n",
        "busy": "Type your message or @path/to/file\n\nThinking… esc to interrupt\n",
        "modal": "Trust this folder?\n> 1. Trust\n  2. Cancel\n",
    },
    "opencode": {
        "idle": (
            "OpenCode\n/media/anulum/GOTM/aaa_God_of_the_Math_Collection\n\n"
            'Ask anything... "Fix a TODO in the codebase"\n\nBUILD  ctrl+p cmd\n'
        ),
        "busy": "Ask anything...\n\nThinking… esc to interrupt\n",
        "modal": "Permission required\n> 1. Allow once\n  2. Deny\n",
    },
}
