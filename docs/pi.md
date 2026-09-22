<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# pi participant and claim guard

The optional pi participant runs the exact verified
`@earendil-works/pi-coding-agent` **0.87.1** RPC host as a child process. Node.js
22.19 or later is required. Install the separately packaged extension from the
source checkout with `cd integrations/pi && npm ci --ignore-scripts`. Use its
`node_modules/.bin/pi` executable or another exact 0.87.1 binary. The Python
participant refuses a different version.

Without an explicit claim binding, pi runs with **no tools**. This is useful for
model-only turns and for testing the provider and session contract:

```bash
synapse participant ask pi "Reply with one sentence" \
  --model ollama/gemma3:1b \
  --pi-binary ./integrations/pi/node_modules/.bin/pi \
  --pi-directory "$PWD" --json
```

Configure the chosen pi model and its credentials using pi's own documented
configuration. Synapse does not supply or infer a provider account. A completed
`participant.turn_result` carries the provider's reported input/output tokens,
cost, stop reason and an exact UUID resume token. Use `--pi-resume-session UUID`
for a later CLI turn. Sessions live under the owner's XDG state directory by
default; `--pi-session-dir` selects another owner-private directory. An unknown
resume UUID is refused rather than silently starting a new session.

## Enable claim-checked coding tools

First claim the intended files on the live hub with the exact Synapse seat and
task. Query that claim's current epoch:

```bash
synapse adapters pi-claim-status \
  --identity PROJECT/seat --project PROJECT \
  --repository "$PWD" --task-id TASK-ID \
  --uri ws://localhost:8876
```

If the command reports `eligible: true`, pass its numeric `epoch` together with
the same project, repository and task. The participant then loads the pinned
extension and enables only pi's built-in `read`, `grep`, `find`, `ls`, `write`,
`edit` and `bash` tools. Before sending a tool-enabled prompt, it requires pi's
RPC command catalog to confirm that this exact extension file loaded:

```bash
synapse participant ask pi "Edit the claimed file" \
  --model PROVIDER/MODEL --identity PROJECT/seat \
  --pi-binary ./integrations/pi/node_modules/.bin/pi \
  --pi-directory "$PWD" --pi-extension ./integrations/pi/index.ts \
  --pi-project PROJECT --pi-repository "$PWD" \
  --pi-task-id TASK-ID --pi-epoch EPOCH \
  --pi-hub-uri ws://localhost:8876
```

For a secured hub, add `--pi-token-file` with an owner-only file. The extension
passes that path to the Synapse hook; it does not put token bytes in the pi
command line or its JSONL stream. The token file must be outside the repository,
so the pi read and search tools cannot discover it there. `read`, `grep`, `find`
and `ls` are limited to paths whose current physical location is inside the
repository; paths through symlinks to outside files are denied. If a claim is
released, expired, reissued at
a new epoch, or the hub cannot be reached, `write` and `edit` fail closed. A
denied tool call is reported as an error result. `bash` is always denied because
its effects cannot be bounded by parsing command text. Unknown tools are also
denied. The session ID observed by pi must equal the one fixed when the child
was launched.

The hook is a **cooperative pre-tool check**, not an operating-system sandbox.
It governs calls through this participant and the loaded extension; it does
not take custody of arbitrary child processes, other pi sessions, raw pi RPC
clients, or direct filesystem writes. A file claim is checked immediately
before each supported mutation, while another process may still change the
workspace afterward. Run untrusted models or extensions under an independent
OS-enforced sandbox when stronger custody is required.

Pi RPC uses LF-delimited UTF-8 JSON records. A command response acknowledges
receipt only; Synapse waits for `agent_settled` after tool calls, retries and
queued follow-ups before returning the participant result. A terminated child,
malformed/oversize frame or timeout yields an error, and the owned process
group is reaped. Delivery through the hub retains its documented at-least-once
provider-effect boundary: a completed replay is deduplicated by the delivery
bridge, but an effect interrupted by a process crash needs reconciliation.

See the [Participant CLI](cli.md) and [delivery contract](coordination-spec.md)
for the surrounding bus semantics.
