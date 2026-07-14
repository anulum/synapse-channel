# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode native fail-closed plugin renderer
"""Render the dependency-free OpenCode mutation hook plugin."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence

from synapse_channel.opencode_claim_guard import MAX_HOOK_EVENT_BYTES

PLUGIN_OWNER_MARKER = "synapse-channel:opencode-claim-guard:v1"
"""Marker proving that Synapse owns an installed plugin file."""

DEFAULT_MAX_OUTPUT_BYTES = 65_536
"""Maximum combined diagnostic stream bytes accepted from the hook process."""


def _validated_argv(argv: Sequence[str]) -> tuple[str, ...]:
    result = tuple(argv)
    if not result or any(not isinstance(item, str) or not item for item in result):
        raise ValueError("OpenCode hook argv must contain non-empty strings.")
    if any(item == "--token" or item.startswith("--token=") for item in result):
        raise ValueError("OpenCode plugin argv must not embed a raw hub token.")
    return result


def render_opencode_plugin(
    *,
    hook_argv: Sequence[str],
    timeout_seconds: float,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> str:
    """Return an OpenCode plugin that denies on every ambiguous hook outcome.

    The generated JavaScript uses ``Bun.spawn`` with an argv array, writes one
    bounded path-only or patch-only JSON event to stdin, caps the combined
    output streams, enforces a wall-clock
    deadline, and accepts only an explicit ``{"allowed": true}`` response.
    Neither raw tokens nor shell strings are persisted.
    """
    argv = _validated_argv(hook_argv)
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 600:
        raise ValueError("OpenCode plugin timeout must be finite and in (0, 600].")
    if not isinstance(max_output_bytes, int) or not 1_024 <= max_output_bytes <= 1_048_576:
        raise ValueError("OpenCode plugin output limit must be between 1024 and 1048576 bytes.")

    argv_json = json.dumps(argv, ensure_ascii=True)
    timeout_ms = math.ceil(timeout_seconds * 1_000)
    return f"""// {PLUGIN_OWNER_MARKER}
const HOOK_ARGV = {argv_json};
const TIMEOUT_MS = {timeout_ms};
const MAX_INPUT_BYTES = {MAX_HOOK_EVENT_BYTES};
const MAX_OUTPUT_BYTES = {max_output_bytes};
const MUTATION_TOOLS = new Set(["edit", "write", "apply_patch"]);

async function readBounded(stream, budget) {{
  const reader = stream.getReader();
  const chunks = [];
  let total = 0;
  while (true) {{
    const {{ done, value }} = await reader.read();
    if (done) break;
    budget.used += value.byteLength;
    if (budget.used > MAX_OUTPUT_BYTES) throw new Error("hook output exceeded its bounded limit");
    total += value.byteLength;
    chunks.push(value);
  }}
  const joined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {{
    joined.set(chunk, offset);
    offset += chunk.byteLength;
  }}
  return new TextDecoder("utf-8", {{ fatal: true }}).decode(joined);
}}

function safeReason(value) {{
  if (typeof value !== "string" || value.length === 0) return "claim verification denied";
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").slice(0, 1000);
}}

function boundedToolInput(tool, args) {{
  const key = tool === "apply_patch" ? "patchText" : "filePath";
  const value = args?.[key];
  if (typeof value === "string" && new TextEncoder().encode(value).byteLength > MAX_INPUT_BYTES) {{
    throw new Error("Synapse claim verification input exceeded its bounded limit");
  }}
  return {{ [key]: value }};
}}

export const SynapseClaimGuard = async (pluginInput) => ({{
  "tool.execute.before": async (input, output) => {{
    if (!MUTATION_TOOLS.has(input.tool)) return;
    const toolInput = boundedToolInput(input.tool, output.args);
    const payload = JSON.stringify({{
      hook_event_name: "tool.execute.before",
      tool_name: input.tool,
      session_id: input.sessionID,
      tool_use_id: input.callID,
      cwd: pluginInput.directory,
      tool_input: toolInput,
    }});
    if (new TextEncoder().encode(payload).byteLength > MAX_INPUT_BYTES) {{
      throw new Error("Synapse claim verification input exceeded its bounded limit");
    }}
    const process = Bun.spawn(HOOK_ARGV, {{ stdin: "pipe", stdout: "pipe", stderr: "pipe" }});
    process.stdin.write(payload);
    process.stdin.end();
    const timer = setTimeout(() => process.kill(), TIMEOUT_MS);
    let stdout;
    let exitCode;
    try {{
      const outputBudget = {{ used: 0 }};
      const values = await Promise.all([
        readBounded(process.stdout, outputBudget),
        readBounded(process.stderr, outputBudget),
        process.exited,
      ]);
      stdout = values[0];
      exitCode = values[2];
    }} catch (_error) {{
      process.kill();
      throw new Error("Synapse claim verification failed closed");
    }} finally {{
      clearTimeout(timer);
    }}
    if (exitCode !== 0) throw new Error("Synapse claim verification failed closed");
    let verdict;
    try {{
      verdict = JSON.parse(stdout);
    }} catch (_error) {{
      throw new Error("Synapse claim verification returned an invalid verdict");
    }}
    if (verdict?.allowed === true) return;
    if (verdict?.allowed === false) {{
      throw new Error(`Synapse file claim denied: ${{safeReason(verdict.reason)}}`);
    }}
    throw new Error("Synapse claim verification returned an ambiguous verdict");
  }},
}});
"""
