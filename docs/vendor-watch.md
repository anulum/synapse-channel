<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Vendor compatibility watch

The read-only vendor watch compares official release evidence with the exact
versions accepted by Synapse integrations. It does not install a host, run a
model, change an account or widen support. The tracked
`integrations/vendor-watch/compatibility.json` names
the review owner, review date and individually verified surfaces. `null`
means no accepted host version has been proven. An installed CLI version is an
observation, not a compatibility verdict.

Run a current check locally:

```bash
python tools/vendor_watch.py --strict --report build/vendor-watch-report.json
```

The command probes installed host `--version` flags without a model turn and
fetches only the six allowlisted official HTTPS sources. It writes a dated JSON
report with source URL, publication date, latest stable version, installed and
verified versions, notes digest and changed-notes signal, status and review priority. Source failure is
`source_unavailable`, never `current`. `--strict` exits nonzero if a source is
unavailable, the named manual review is overdue, or the latest unverified notes
flag possible security or breaking changes. Release-note keyword
priority is an advisory triage hint, not proof of a security vulnerability.
The report stores no release body, credentials or provider text.

The daily `.github/workflows/vendor-watch.yml` workflow publishes
the same report as a short-lived Actions artifact and job summary. The Core
maintenance seat reviews the report at least weekly and before each integration
release. A breaking, permission, hook or security change gets immediate
triage. The existing OpenCode compatibility workflow separately runs its
host-specific smoke; this watch does not duplicate that runtime gate.

After a new official release, run the affected host's official validator and
an isolated real integration journey. Update its accepted version only when
those checks pass, or mark the affected capability unsupported and open an owned
follow-up. Compare the release notes and schema; a newest tag alone is not a
feature guarantee. Update `reviewed_at` only after the review record is
complete, with source date, exact version, tested behavior, decision and next
review. No automatic version bump, paid call, marketplace action or deployment
follows from detection.

The 2026-09-19 live check found newer official releases than the local hosts:
Claude Code 2.1.278 versus installed/tested 2.1.273; Codex CLI 0.155.1 versus
installed 0.154.0; Gemini CLI 0.60.0 versus installed 0.59.0; OpenCode 1.18.31
versus installed/tested 1.17.20. Pi had no local executable; its official
stable release was 0.85.1. The official MCP specification repository's latest
release tag was 2026-07-28; no remote MCP profile is marked verified. These
are dated observations, not claims that the newer releases are compatible.

The same-day isolated host review subsequently accepted Claude Code 2.1.278
and OpenCode 1.18.31 for the packaged integrations. Claude's official strict
plugin validator, isolated plugin load and live hub claim guard passed without
a model turn. OpenCode's official Linux x64 release asset was checked against
its published SHA-256; real JSONL, ACP session creation and prompt through a
local scripted provider, authenticated server and live hub claim guard journeys
passed. The matrix records
the new tested versions and retains the previous 2.1.273 and 1.17.20 versions
as rollback references. The workstation's installed hosts were not upgraded.
The same-day C03 acceptance also verified Pi 0.85.1 with a local model,
native extension and real hub claim checks; its optional coding mode remains
subject to the documented cooperative hook boundary.
The OpenCode macOS, Windows and Linux arm64 release assets have official
digests in the compatibility manifest; their executable workflow tests are
separate platform gates when the local change is published.

On 2026-09-20, Pi 0.86.0 was reviewed immediately after its official release.
Its provider stream context, JSON value types and `user_bash` handler contract
changed; this adapter does not implement those APIs. The exact npm host passed
the TypeScript guard check and test, loaded the extension through its RPC
command catalog, and completed two local Ollama turns across an exact session
resume. The compatibility matrix now accepts 0.86.0 for these paths and keeps
0.85.1 as the verified rollback reference. A model-driven claim mutation on
0.86.0 and other operating systems remain separate verification work.

On 2026-09-22, Claude Code 2.1.280 passed its official strict plugin validator,
isolated plugin load, local MCP hub claim journey and allowed/denied write hook
checks on Linux. The installed binary identified itself as 2.1.280; the matrix
accepts that version for the exercised plugin path and retains 2.1.278 as the
previous verified rollback version. The same review admitted Pi 0.87.1 after
an exact RPC host load, TypeScript extension check, local Ollama turn and
resume, and the focused live hub guard suite. Pi 0.86.0 remains its previous
verified rollback version.

OpenCode 1.18.32 passed the official Linux x64 archive digest and binary
version checks, the compatibility contract, and real JSONL, ACP, authenticated
server and claim journeys. All twelve archive digests in its manifest match the
immutable release metadata; the other operating systems still require their
own executable workflow runs. OpenCode 1.18.31 remains the previous verified
rollback version. On 2026-09-22, an isolated Codex CLI 0.156.0 Linux
app-server discovered the manual stdio Synapse tools, read live state from a
local hub, acquired and released a claim, and received a refusal when a second
Codex host identity claimed the same path. The global CLI and active profile
were unchanged. A local Ollama model turn timed out, so this acceptance covers
the Codex host tool API, not model-directed tool selection. Post-thread
`mcpServerStatus/list` returned a handshake error while direct tool calls
worked; pre-thread inventory listed the enabled tools. Gemini CLI has no
accepted packaged extension; its 0.60.0 release does not change that
classification. Remote MCP admission remains with C09.
