<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Deployment

The hub is the only long-running piece — workers and human clients connect to it.
Run one hub per coordinating group.

## Local, always-on (systemd user service)

The local-first default: a per-user service so the hub is always up and restarts
on login, with no root.

```bash
pipx install synapse-channel
mkdir -p ~/.config/systemd/user ~/synapse
cp deploy/synapse-hub.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now synapse-hub
systemctl --user status synapse-hub
```

The hub then listens on `ws://localhost:8876`, persists to `~/synapse/hub.db`, and
mirrors the channel to `~/synapse/feed.ndjson`. To survive a full logout (no
session open), enable lingering once: `loginctl enable-linger "$USER"`.

## Provider-independent presence

An agent's wake loop (a backgrounded `synapse wait`) gives prompt wakes, but it
dies with the agent — so when a turn-based assistant is down or its API is rate
limited, the project drops off the roster. Decouple *reachability* from the agent
with a presence holder: a per-project systemd template that holds the hub
connection and is restarted by systemd if it ever dies.

```bash
cp deploy/synapse-presence@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now synapse-presence@myproject
```

It registers as `myproject-presence`, costs nothing (it holds a socket — no model),
and keeps the project visible in `synapse who` and addressable even while the agent
is offline. No message is lost meanwhile — the hub records them durably — so the
returning agent catches up with `synapse relay --project myproject` (or the
`syn-resume` helper). The two layers are complementary: the presence holder is
always-on reachability; the wake loop is promptness while the agent runs.

> **Presence is not a wake.** The presence holder keeps the project in the roster and
> the feed durable, but it does **not** wake the agent — only an active `synapse wait`
> does. If the agent stops re-arming its waker and leans on the presence daemon alone,
> it stays reachable but nothing wakes it: messages wait until the next manual
> `syn-inbox`/`syn-resume`. Keep the waker running for promptness; the presence daemon
> is a safety net for reachability and durability, not a substitute for it.

## Container

```bash
docker compose up -d          # builds the image and starts the hub
docker compose logs -f hub
```

The compose file publishes the port on `127.0.0.1` only and stores the durable log
in the `synapse-data` volume. Build and run the image directly if you prefer:

```bash
docker build -t synapse-channel .
docker run -d --name synapse-hub -p 127.0.0.1:8876:8876 -v synapse-data:/data synapse-channel
```

On a release the `docker` workflow publishes `ghcr.io/anulum/synapse-channel`.

## Exposure and security

The hub binds loopback and runs unauthenticated by default — correct for one
operator on one machine. Before exposing it beyond `localhost`:

- Bind off-loopback only with a shared secret: `synapse hub --host 0.0.0.0 --token
  "$SYNAPSE_TOKEN"`. The hub warns when bound off-loopback without a token.
- In compose, change the port mapping to `8876:8876` **and** set `SYNAPSE_TOKEN`
  (uncomment the `command:` block). Clients then pass `--token "$SYNAPSE_TOKEN"`.
- The token is a proportionate gate (constant-time check), not a cryptographic
  identity system; put real network controls in front of a multi-host hub.

## Persistence and backups

With `--db`, every authoritative mutation (claims, releases, task updates, chat)
is written to an append-only SQLite event log in WAL mode, and the hub rebuilds
its state by replaying it on start-up. Back up the hub by copying the `--db` file
(and its `-wal`/`-shm` siblings) or the whole data directory while the hub is
stopped, or use `sqlite3 hub.db ".backup"` online. The `--relay-log` feed is
derived state and bounded by `--relay-max-lines`; it is safe to truncate.

## Restarting the hub safely

The hub restarts cleanly because both ends are built for it. With `--db`, a restart
replays the event log, so active leases are **restored rather than dropped**. On the
client side a waiter on 0.28.1+ **exits with code 3 when its socket drops** instead
of hanging on a dead connection, so a hub restart makes every waiter exit and re-arm
rather than go dark.

When a waiter re-arms right after its process was killed, its old name can still
linger on the hub for a few seconds (until the keepalive reaps it). A 0.29.0+ client
re-arms with **takeover**: the hub evicts the stale holder (closing it with code
`4010` *superseded*) and rebinds the name, so the re-arm succeeds instead of failing
with a `4009` name conflict. Takeover needs **both ends on 0.29.0+** — the client to
ask for it, the hub to perform the eviction — and a 15-second keepalive reaps a
genuine ghost quickly as the backstop.

So a coordinated restart is safe when every live client is on 0.28.1+: announce,
restart the service, and the fleet re-arms against the fresh hub on its own. Pick a
quiet moment, announce before and after, and never start a restart that would strand
a client too old to exit-on-drop.

## Fleet-wide announcements

A broadcast (`--target all`, a `--priority` message, or any `CEO` message) wakes
every waiter at the same instant; their agents then all re-invoke and call the model
provider together, and the **provider's** request-rate limiter throttles the burst —
Anthropic's API, for one, returns *"Server is temporarily limiting requests"*, a
request-rate limit distinct from your usage quota. Two defences, used together:

- **Receiver side:** `synapse wait --wake-jitter` (default 8s) spreads broadcast
  wakes over a few seconds so the re-invocations do not land at once.
- **Sender side:** to roll an update out to a fleet, do **not** `--target all`. Send
  **directed and staggered** — one message per terminal, a few seconds apart — so the
  wakes are spread regardless of each waiter's jitter setting:

  ```bash
  for p in api-dev test-dev docs-dev; do
    synapse send --target "$p" "upgrade to 0.30.0: pipx upgrade synapse-channel; restart your waiter"
    sleep 5
  done
  ```
