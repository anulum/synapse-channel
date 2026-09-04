# Troubleshooting

Symptom → cause → fix for the problems you are most likely to hit. Every message
quoted below is one the tools actually print, so you can match on it directly.

## `[NAME] Could not reach hub at ws://…`

The client could not open a WebSocket to the hub. In order of likelihood:

- **The hub is not running.** Start one — `synapse hub` (or `synapse team` for a hub
  plus workers). Confirm it is up with `synapse health` (exit `0` reachable, `1` not).
- **The `--uri` does not match the hub.** The default is `ws://localhost:8876`; if the
  hub runs on another `--host`/`--port`, pass the matching `--uri` to every client.
- **The port is taken or firewalled.** Start the hub on a free port
  (`synapse hub --port 8899`) and point clients at it.
- **It is a secured hub and you sent no token** — see [authentication](#a-secured-hub-refuses-me) below.

## `[NAME] hub at capacity: too many connections … (code 4013)`

The hub is up and serving every already-connected agent, but its connection
table is full, so it closed your new socket with code `4013`. This is **not** an
outage — the clients already online keep working. It happens when many terminals
accumulate: each holds a command socket and a persistent `-rx` waiter, and
presence daemons add more, so a large fleet can reach the ceiling.

- **Free a slot.** Reap stale waiters you no longer need (`syn-reap` removes this
  identity's own waiter sidecar) and stop terminals that have exited but left
  daemons behind.
- **Raise the ceiling.** Restart the hub with a higher budget —
  `synapse hub --max-clients 512` (the default is 256). Restarting drops every
  socket momentarily; live agents reconnect on their own.
- **Retry.** The cap is transient under churn; a moment later a slot usually frees.

## A waiter exits at once, or seems to loop re-arming

`synapse wait` is a *one-shot* wake primitive — it is meant to exit and be re-armed:

| Exit code | Meaning |
| --- | --- |
| `0` | a matching message arrived (it is printed) |
| `1` | the hub was unreachable |
| `2` | it waited the full `--timeout` and nothing arrived |
| `3` | a finite wait's connection dropped — **re-arm, do not treat as an error** |

An unbounded `--timeout 0` wait re-arms itself after its first established
connection drops and keeps retrying temporary hub unavailability. Re-arming a
finite wait on exit is normal. A *tight* external re-arm loop almost always
means you are being woken by traffic that is not for you — see the next two
entries.

If that waiter is supposed to wake a terminal provider unattended, do not make
the agent itself responsible for re-arming it. Install the exact-seat bridge
with `synapse waker install --identity NAME --session SESSION --cwd "$PWD"
--agent-command PROVIDER --start`, then inspect all layers with `synapse waker
status --identity NAME`. A healthy verdict requires durable desired state,
active systemd service, valid tmux identity binding, and a live provider pane.

## The message is durable but the terminal agent did not wake

This usually means only the passive `synapse arm` receiver is alive, or an
unsupervised `agent-tmux wait` process exited. Presence and mailbox delivery are
not proof that a provider turn was invoked.

1. Run `synapse waker status --identity NAME`.
2. If `desired state: inhibited`, read the recorded reason and use `waker
   resume` only after the malfunction is resolved.
3. If the service is inactive but desired state is armed, inspect
   `journalctl --user -u "$(systemd-escape --template=synapse-waker@.service --
   NAME)"` and the reported main status/restart count.
4. If the provider is unavailable, inspect the exact tmux session and its
   `SYN_PROJECT`/`SYN_IDENTITY` binding. Do not restart or replace the terminal
   merely to make the service green.
5. If `pending wake: yes`, the routing hint is retained while the provider is
   busy, modal, starting, or otherwise unsafe for injection.

To terminate a malfunctioning bridge without losing the provider terminal, use
`synapse waker stop --identity NAME --reason "…"`. The inhibit is written
before systemd stops the exact bridge, and only an explicit `waker resume`
permits automatic restart again.

## `[NAME] connection to ws://… closed; re-arm the waiter.`

The hub closed the connection (for example, during a hub restart or network
drop). This is expected. A finite wait exits with code `3` so its caller can
re-arm it. A `--timeout 0` (indefinite) wait prints the diagnostic, re-arms
internally after a bounded delay, and continues through temporary connection
failures. A superseding waiter or identity refusal remains a terminal verdict;
the old waiter must yield rather than steal or hammer the governed identity.

### Right after a hub restart, `who` / `health` fail and the port shows high Recv-Q

A mass reconnect storm is normal when dozens of waiters re-arm together. Wait
for the accept queue to drain (seconds to half a minute on a large fleet)
before diagnosing further. Prefer staggered restarts, a modest systemd
`RestartSec`, and reaping stale waiters before the bounce. Details:
[Warm-start reconnect storm](deployment.md#warm-start-reconnect-storm-mass-waiter-re-arm).

If this repeats, inspect the hub log. Accepted takeovers, takeover cooldown
refusals, name conflicts, and name-switch denials are logged with the sender
name, remote host, and close reason, without chat or task payloads.

If one name is taken over again and again — two waiters launched for the same
identity will each take the name back from the other, about once per cooldown —
the hub stops the war rather than logging it forever: after a few takeovers of one
name within a short window it **quarantines** the name, pinning whichever socket
holds it and refusing further takeovers for a minute (one `takeover quarantine …
reason=oscillation` warning, not a per-second stream). The fix for the underlying
churn is to run a single waiter per identity; the quarantine just keeps a duplicate
from disrupting the live owner.

## I wake on messages that are not addressed to me

A `--directed-only` waiter wakes on a message addressed to **you**, to a **group glob**
you are in (`quantum/*`), a **CEO** message, or a **`--priority`** message. Routine
broadcasts to `all` are suppressed.

An interactive `agent-tmux` pane bridge is stricter: it wakes only for an exact
identity, role, or group target. A global priority or CEO broadcast stays in the
inbox and does not inject a provider prompt. This prevents one announcement from
spending a provider turn in every terminal.

- **Since 0.42.0**, a priority or CEO message *directed at a different agent* no longer
  wakes you — it must still reach you (a broadcast, or one addressed to you).
- **On a multi-seat project**, arm the **seat** (`--for project/seat`) to wake only on
  seat-addressed traffic. Since 0.42.0 a message to the bare `project` is a routine
  project-level broadcast for a seat (it still reaches the seat's inbox, and a CEO or
  priority message still wakes it). Arm the **bare project** (`--for project`) — the
  default for the `syn arm`/`syn-wait` wrapper — if you want project-level messages
  to wake you.

## A wake prompt is pasted but remains in the input composer

`agent-tmux` must complete both halves of delivery: bracketed paste followed by
an Enter key after a second safety probe. Current provider composers may render
their input marker above status/footer rows, so the probe classifies the complete
visible pane rather than only its last few lines. It still refuses busy, modal,
unknown, or cross-identity panes and requires the exact fixed routing prompt to
remain visible, allowing only terminal line wrapping, before Enter is sent.
It does not equate `tmux send-keys` exit zero with provider consumption. A
post-submit capture must show that the prompt disappeared or that a newer idle
composer appeared after it. During asynchronous startup an ignored Enter leaves
the single prompt staged and pending; the bridge waits for a safe pane and retries
only Enter, never the prompt paste.

If `agent-tmux status` reports `pane readiness: update-required`, the update
chooser belongs to the already running managed provider process; it may be in a
detached tmux session even when visible terminals and the currently installed
binary are up to date. Do not kill or restart a visible Kitty/tmux terminal and
do not assume a package update changed the resident process. Check the exact
session named by the bridge, its identity binding, absolute provider command,
and the local pending-wake record.

New Synapse-managed Codex sessions disable startup update checks through
`--config check_for_update_on_startup=false`, which Codex documents for
centrally managed updates. Existing sessions are preserved. If one is already
on an update chooser, `agent-tmux` marks compatibility degraded and keeps its
pane receiver advertised while the wake stays pending; it does not select a
numbered choice or relaunch the provider. A version handover requires separate
owner authority and must preserve the old terminal until the replacement
session, exact binding, wake consumption, and semantic reply are all proven.

The diagnostic layers are intentionally distinct:

- `-rx` reachable: durable mailbox transport is available;
- `-pane-rx` visible: a pane bridge is currently advertised;
- `pending wake: yes`: a routing hint is retained locally but not yet consumed;
- `pane readiness: idle`: the provider composer can accept the fixed wake;
- semantic reply: the model actually handled the newest actionable message.

Do not report the first layer as proof of the last.

If `syn-name` reports `user/terminal-*` while the command runs inside a plausible
project checkout, upgrade or refresh the local runtime. That identity is only a
non-project fallback; the repository project must win. Pin a multi-seat identity
explicitly (`SYN_PROJECT=<project>` plus `SYN_IDENTITY=<project>/<seat>`) and
verify the matching `agent-tmux` session binding before re-arming the bridge.

## Messages are in the feed but `syn-inbox` shows nothing

Two independent causes:

- **The inbox cursor is consume-on-read and shared.** `syn inbox` advances a per-project
  cursor; if a prior drain (a boot read, a wake handler) already passed those messages,
  a later read shows empty. Re-read against the raw feed (`~/synapse/feed.ndjson`) to
  recover them, or give each reader its own cursor file.
- **A reply went to a name outside your project namespace.** Project membership is the
  `project/id` **slash** form. A reply addressed to `project-keeper` (a **hyphen** suffix)
  is *not* in project `project`, so `syn inbox --project project` will not show it. Send
  as the bare project (`project`) or a slash sub-identity (`project/keeper`), not a
  hyphen-suffixed name, so replies route back to where you read.

## A name conflict, or my agent's own sends are refused

A waiter must not hold the **bare** identity it waits for: the bare name equals the
sender name, so a message to the project would evict the waiter. Arm it as `name-rx`
(the wrapper does this by default). A re-arming waiter *takes over* its own name,
evicting a ghost holder of `name-rx`; if a fresh send is refused with a name conflict,
another live connection already holds that name.

## A secured hub refuses me

A hub started with `--token <secret>` requires that token. Present it with `--token`,
`--token-file <path>` (so it is not visible in `ps`), or the `SYNAPSE_TOKEN` environment
variable — precedence is `--token` → `--token-file` → env. An unauthenticated socket gets
no welcome or roster and is closed after `--auth-timeout` seconds (default 10), so an idle
connection cannot sit on the `--max-clients` budget. On an **open** hub the same
deadline applies to the first name-binding registration (`4012` reason
`registration timeout`); a first frame that does not bind is closed with
`registration required` (`4010`).

## A client is closed with `too many connections from host`

The hub enforces `--max-connections-per-host` (default **32**) and that remote
host already has that many sockets open. Close stale clients, raise the cap for
trusted local fan-out, or pass `0` to disable the per-host connection-count
limit. This is separate from `--host-rate`, which meters frames rather than open
sockets.

## `synapse doctor` warns that a filesystem is nearly full

`synapse doctor` checks the root filesystem by default because a full root disk
can break shell hooks, pipx shims, logs, and service state even when the project
checkout lives elsewhere. The warning includes the path, free MiB, and used
percentage. Free space by moving build trees, caches, logs, or virtualenvs off
the pressured filesystem before running long-lived coordination sessions.

To inspect the mount that holds a specific checkout or runtime tree:

```bash
synapse doctor --disk-path /media/anulum/GOTM
synapse doctor --disk-path "$XDG_RUNTIME_DIR"
```

## `Could not acquire lock 'TASK': …` / `release refused for 'TASK': …`

- **Lock denied or timed out** — another agent holds the lease. Wait, coordinate, or
  raise `--timeout`. `synapse lock` serialises a command across agents by holding a lease
  for its duration.
- **Release refused** — you do not own that claim, or the hub did not answer. Releasing is
  idempotent; releasing something you do not hold is a no-op, not an error.

## A worker never replies on the channel

- Check the backend: `synapse worker --provider ollama --model <m> --base-url <url>`. For a
  no-network smoke test use `--provider rule` (deterministic canned replies).
- The model server (Ollama or any OpenAI-compatible endpoint) must be reachable at
  `--base-url`, and `--api-key-env` must name an environment variable that holds the key.
- A worker throttles itself with `--min-reply-interval`; it also ignores its own and
  service messages, so it will not answer presence or system traffic.

## `git error: …` on `git-claim` / `git-hook` / `git-release`

The git-aware commands resolve the branch and changed paths **client-side**, so run them
from inside a git working tree, on a branch. `synapse git-hook install` bakes the absolute
`synapse` path into the hook; pass `--synapse-bin` if it cannot be resolved from `PATH`.

## The hub refuses to start when bound off-loopback without a token

Binding `--host 0.0.0.0` (or any non-loopback address) **without** `--token` is **refused**,
not just warned about: the connection secret is the only thing standing between the channel
and the network, so the hub will not start unexposed by accident. Require `--token`, and if
you enable `--metrics`, require `--metrics-token` so the endpoint does not leak operational
metadata. To bind an unauthenticated off-loopback hub anyway (a trusted private network),
pass `--insecure-off-loopback` to downgrade the refusal to a warning.

## The hub refuses to start with a token over plaintext `ws://` off loopback

Binding off loopback *with* `--token` but without native TLS is also **refused**:
the shared token and every coordination frame would travel plaintext `ws://` and
be readable on the network path. Add native TLS (`--tls-certfile` and
`--tls-keyfile`) or front the hub with a `wss://`-terminating reverse proxy on a
private interface. To bind token-over-plaintext anyway on a trusted private
network, pass `--insecure-off-loopback` to downgrade the refusal to a warning;
`--paranoid` makes native WSS mandatory with no override.

## The hub reports degraded health and refuses mutations

Startup found one or more malformed rows in the durable SQLite event log. The hub
recovers the valid prefix/suffix so health and read-only queries remain available,
but the reconstructed state may omit an affected claim, release, or other mutation.
It therefore refuses **all** state-changing frames instead of pretending the partial
projection is authoritative. `/health` reports `status: degraded` and the
`journal_corrupt_rows` count; `/metrics` exposes `synapse_journal_corrupt_rows`.

Do not edit or delete the row ad hoc. First stop the hub and establish the lowest
sequence every ingest consumer has already settled. Then run the offline recovery:

```bash
synapse compact ~/synapse/hub.db \
  --floor-seq <settled-seq> \
  --drop-corrupt \
  --archive-report ./corrupt-recovery.html
```

The archive records only the row sequence, safe original kind, validation reasons,
and a domain-separated SHA-256 digest; it never copies the malformed raw payload.
It is persisted as a planned operation before deletion, so an archive-write
failure leaves the database unchanged. Rows above the floor remain untouched.
Restart the hub and verify health is `ok`
and `journal_corrupt_rows` is `0` before allowing agents to mutate state again.
Use `--all` instead of `--floor-seq` only when no read-side consumer can still need
any event in the log.

## Still stuck?

- `synapse <command> --help` documents every flag.
- The [CLI reference](cli.md) and the [coordination model](coordination-model.md) cover the
  full surface and the concepts behind it.
- Report a reproducible problem on the [issue tracker](https://github.com/anulum/synapse-channel/issues);
  see [`SUPPORT.md`](https://github.com/anulum/synapse-channel/blob/main/SUPPORT.md).
