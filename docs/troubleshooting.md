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
| `3` | the connection dropped while waiting — **re-arm, do not treat as an error** |

Re-arming on exit is normal. A *tight* re-arm loop almost always means you are being
woken by traffic that is not for you — see the next two entries.

## `[NAME] connection to ws://… closed; re-arm the waiter.`

Exit code `3`: the hub closed the connection (a hub restart, a name takeover, or a
network drop). This is expected — re-arm the waiter. A `--timeout 0` (indefinite)
waiter prints this instead of hanging on a dead socket, precisely so the caller
re-arms rather than going silently dark.

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

- **Since 0.42.0**, a priority or CEO message *directed at a different agent* no longer
  wakes you — it must still reach you (a broadcast, or one addressed to you).
- **On a multi-seat project**, arm the **seat** (`--for project/seat`) to wake only on
  seat-addressed traffic. Since 0.42.0 a message to the bare `project` is a routine
  project-level broadcast for a seat (it still reaches the seat's inbox, and a CEO or
  priority message still wakes it). Arm the **bare project** (`--for project`) — the
  default for the `syn arm`/`syn-wait` wrapper — if you want project-level messages
  to wake you.

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
