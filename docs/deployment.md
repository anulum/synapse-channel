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
synapse init --project myproject --identity myproject/worker --start-user-services
systemctl --user status synapse-hub
```

The hub then listens on `ws://localhost:8876`, persists to `~/synapse/hub.db`, and
mirrors the channel to `~/synapse/feed.ndjson`. To survive a full logout (no
session open), enable lingering once: `loginctl enable-linger "$USER"`.

If you prefer to inspect before installing, run:

```bash
synapse init --project myproject --identity myproject/worker
```

It prints exact `systemctl --user` commands. `synapse git-init` accepts the same
`--install-user-services` and `--start-user-services` flags, so claim-aware git
setup can also write/start the hub, presence, and wake-listener units.

### Sandboxing of the generated units

Every generated unit (hub, presence, wake listener) ships a systemd sandbox
block: `ProtectSystem=strict` with `ProtectHome=read-only` makes the whole
filesystem read-only to the service except its declared `ReadWritePaths=` —
`~/synapse` (event store, relay feed, mailbox cursors, owner leases) and, for
connecting clients, `~/.local/share/synapse` (the trust-on-first-use machine
key). `PrivateTmp`, `NoNewPrivileges`, `UMask=0077`, namespace/realtime/SUID
restrictions, and a per-role `LimitNOFILE` (65536 hub, 4096 listeners) complete
the set. The block is the strongest one a *user* service manager can apply:
directives that need capability-bounding-set changes (`ProtectClock=`,
`ProtectKernelModules=`, `PrivateDevices=`, `CapabilityBoundingSet=`) fail at
spawn with `218/CAPABILITIES` under `systemd --user`, so they are deliberately
absent — one shared module (`synapse_channel/service_hardening.py`) owns the
set, and the checked-in `deploy/*.service` templates are test-pinned to it.
The install paths create the writable directories up front because
`ReadWritePaths=` refuses to mount a path that does not exist. Measured with
`systemd-analyze security --user` on a live workstation, the block moves a
service from 9.8 (UNSAFE) to 7.4 (MEDIUM); the residual score reflects the
user-manager capability ceiling, not missing configuration.

## Permanent waiter (`synapse arm install`)

Install only the exact-identity waiter when the hub already exists or lives on
another machine:

```bash
# Inspect/write first; this does not install or start a hub.
synapse arm install --identity myproject/worker

# Write, reload systemd, and enable the escaped identity instance now.
synapse arm install --identity myproject/worker --start
systemctl --user status "$(systemd-escape --template=synapse-arm@.service -- 'myproject/worker')"
```

The generated user template runs `synapse arm --mailbox`, has
`Restart=always`, and is enabled under `default.target`. It therefore survives
terminal closure and recovers directed messages that landed during reconnect
gaps. Enable lingering once with `loginctl enable-linger "$USER"` if it must
remain up after every login session closes. To remove it, disable the same
escaped instance with `systemctl --user disable --now ...`; the shared template
can remain for other identities.

For a remote or secured hub, bake the URI and a protected token-file path into
the unit:

```bash
chmod 600 ~/.config/synapse/token
synapse arm install --identity myproject/worker \
  --uri wss://hub.example:8876 \
  --token-file ~/.config/synapse/token \
  --start
```

The installer stores an absolute token-file path in the unit, never the secret.
It refuses a raw `--token` or ambient
`SYNAPSE_TOKEN` because embedding either in a persistent unit would expose the
credential. `--start` returns nonzero if `systemd-escape`, `daemon-reload`, or
`enable --now` fails; a write-only install prints the exact follow-up commands.

This service is a permanent, model-token-free passive receiver: it keeps the exact
identity's mailbox reachable and writes wakes to the user journal, but does not
paste untrusted message bodies into a model terminal or spend provider tokens.
Use `agent-tmux`/`codex-tmux` when a running terminal provider also needs a fixed
safe prompt injected.

Native Windows Task Scheduler installation is not implemented or claimed.
`synapse arm install` exits `2` outside Linux; on Windows, use WSL with systemd
enabled and install the unit inside that distribution. This is the supported
permanent-waiter path until a real native Windows service is validated.

## Provider-independent presence

An agent's wake loop (a backgrounded `synapse wait`) gives prompt wakes, but it
dies with the agent — so when a turn-based assistant is down or its API is rate
limited, the project drops off the roster. Decouple *reachability* from the agent
with a presence holder: a per-project systemd template that holds the hub
connection and is restarted by systemd if it ever dies.

`synapse init --start-user-services` installs this as `synapse-presence@...`.
The checked-in `deploy/synapse-presence@.service` remains a copyable template for
operators who manage units by hand.

It registers as `myproject-presence`, costs nothing (it holds a socket — no model),
and keeps the project visible in `synapse who` and addressable even while the agent
is offline. No message is lost meanwhile — the hub records them durably — so the
returning agent catches up with `synapse relay --project myproject`. The two
layers are complementary: the presence holder is project-level visibility; the
exact-identity `synapse arm install` service is a durable passive receiver; and
the tmux bridge supplies active terminal promptness when a provider is running.

> **Presence is not a wake.** The presence holder keeps the project in the roster and
> the feed durable, but it does **not** wake the agent. Use an active `syn arm` /
> `synapse arm` listener for passive receiver promptness, or `synapse codex-tmux`
> when an existing Codex terminal must receive a fixed wake prompt. The presence
> daemon is a safety net for reachability and durability, not a substitute for
> either wake path.

## Provider-neutral worker session

Use `worker-session` when launching a coding agent from a terminal:

```bash
synapse worker-session --identity myproject/worker -- codex --sandbox danger-full-access
```

The launcher exports `SYN_PROJECT` and `SYN_IDENTITY` before the provider starts.
For interactive terminal providers (`codex`, `claude`, `kimi`, `grok`) launched
from an interactive terminal, it starts or attaches a persistent tmux session,
starts a directed waiter for that identity, and attaches the current terminal to
the tmux session. Non-terminal commands keep the temporary `syn arm` sidecar
path. The listener is only a local socket holder; it does not spend model tokens
while waiting.

## Codex tmux wake transport

Use `codex-tmux` only when you need to inspect or control the tmux wake path
manually:

```bash
synapse codex-tmux start --identity myproject/codex-main --session myproject-codex --cwd "$PWD"
synapse codex-tmux wait --identity myproject/codex-main --session myproject-codex --cwd "$PWD"
```

The wait loop blocks on `synapse wait` and then injects one fixed prompt into the
tmux pane. It never pastes the Synapse message body into the terminal; the
provider reads its inbox after the prompt. DIRECTOR-style routing can sit above
this later, but the local tmux transport remains the only component that writes
to the terminal.

## Fresh terminal auto-connect

Install the shell hook once when you want every new terminal to join the local
coordination layer automatically:

```bash
synapse install-shell-hook --shell auto
```

For Bash, Fish, and Zsh, the installed block loads the current package hook from
`synapse shell-hook` on shell startup. Each prompt exports `SYN_PROJECT` and
`SYN_IDENTITY` and keeps a background `synapse arm` listener alive for that
terminal. The listener is only a socket holder; it does not call a model or spend
provider tokens while waiting.

Before long-running fleet sessions, run `synapse doctor`. The doctor check
includes root-filesystem pressure by default; pass `--disk-path <workspace>` when
the workspace, build tree, or package cache lives on a different mount.

After upgrading a local fleet, run:

```bash
synapse doctor --project myproject --id worker --redeploy-checklist
```

The checklist prints package, service, roster, durable-state, and git-hook checks
for the installed executable, hub service, presence daemon, wake listener, SQLite
event log, and claim-aware hooks. It does not restart services by itself; run the
printed commands when you are ready to bounce the hub and reconnect the fleet.
Use `--db-path` if your hub service stores the event log somewhere other than
`~/synapse/hub.db`.

For multi-seat fleets on one machine, start the hub with
[`--team-secure`](team-secure.md) (token + identity trust + role grants + private
directed messages). For an exposed or multi-host bind, add
[`--paranoid`](paranoid-mode.md) (token, durable log, per-message auth, ACL,
native WSS) or use both together. For a multi-seat hub that is also
network-exposed, [`--secure`](secure-mode.md) composes both profiles and adds
bounded per-agent, per-host, and per-host-connection flood limits in one switch.
Use the [A2A deployment threat
model](a2a-deployment-threat-model.md) for exposed `synapse a2a-serve`
deployments.
The planned [at-rest encryption profile](at-rest-encryption.md) is the storage
hook behind that checklist; it defines key storage, rotation, backup recovery,
and local-first tradeoffs before any encrypted store migration ships.

The hook does not infer the project from the current git checkout by default.
Unassigned terminals join `SYNAPSE_DEFAULT_PROJECT`, or the neutral `user` lane
when unset. Bind a terminal or provider session to a project explicitly with
`SYN_PROJECT`/`SYN_IDENTITY`, or opt a repository into auto-binding with:

```bash
mkdir -p .synapse
printf '%s\n' myproject > .synapse/project
```

Set `SYNAPSE_AUTO_PROJECT_FROM_CWD=1` only when you intentionally want legacy
CWD-derived project names.

The hook also wraps common provider commands through `synapse worker-session`:
`codex`, `claude`, `kimi`, `grok`, `gemini`, `agent`, `ask`, and `ollama`. That
keeps cloud providers and local LLM entry points on the same identity path from
process start. In an interactive terminal, Codex/Claude/Kimi/Grok use the
persistent tmux wake bridge automatically. Disable tmux autostart for terminal
providers with `SYNAPSE_PROVIDER_TMUX=0`, or disable the hook for one terminal
with:

```bash
export SYNAPSE_AUTO_CONNECT=0   # Bash/Zsh
set -gx SYNAPSE_AUTO_CONNECT 0  # Fish
```

## Container

```bash
docker compose up -d          # builds the image and starts the hub
docker compose logs -f hub
```

The compose file publishes the port on `127.0.0.1` only and stores the durable log
in the `synapse-data` volume. A container has to bind `0.0.0.0` internally for the
published port to reach it, and the hub refuses an off-loopback bind without a token;
the compose command passes `--insecure-off-loopback` to accept that, which is safe
only because the publish stays on loopback. To expose the hub beyond this host, change
the publish and swap `--insecure-off-loopback` for `--token` (see the compose file).

Build and run the image directly if you prefer — the same off-loopback opt-in applies:

```bash
docker build -t synapse-channel .
docker run -d --name synapse-hub -p 127.0.0.1:8876:8876 -v synapse-data:/data \
  synapse-channel hub --host 0.0.0.0 --db /data/hub.db --relay-log /data/feed.ndjson \
  --insecure-off-loopback
```

On a release the `docker` workflow publishes `ghcr.io/anulum/synapse-channel`; on every
change to the image or the compose file it also runs a compose smoke that waits for the
container to report healthy.

## Exposure and security

The hub binds loopback and runs unauthenticated by default — correct for one
operator on one machine. Before exposing it beyond `localhost`:

- The recommended team shape is a token **and** TLS together: `synapse hub
  --host 0.0.0.0 --token "$SYNAPSE_TOKEN" --tls-certfile ./hub.crt
  --tls-keyfile ./hub.key` serves native `wss://` (the certificate and key must
  be PEM files readable by the hub process), or terminate TLS at a reverse
  proxy and keep the hub bound to a private interface behind it. Native TLS
  protects the transport; it does not replace `--token` or per-host limits.
- A token alone still binds: `synapse hub --host 0.0.0.0 --token
  "$SYNAPSE_TOKEN"`. The hub **refuses to start** off-loopback without a token
  (pass `--insecure-off-loopback` to accept the risk and bind anyway), and a
  token presented over plaintext `ws://` logs a startup advisory — the shared
  token and every coordination frame are readable on the network path. Treat
  token-without-TLS as the fallback for a trusted LAN, not the team default.
- For shared or exposed hosts, cap connection churn from one remote host with
  `--max-connections-per-host <n>`. This counts simultaneous sockets, including
  sockets still authenticating, and complements `--host-rate`, which limits frame
  rate rather than connection count.
- In compose, change the port mapping to `8876:8876` **and** set `SYNAPSE_TOKEN`
  (uncomment the `command:` block). Clients then pass `--token "$SYNAPSE_TOKEN"`.
- The token is a proportionate gate (constant-time check), not a cryptographic
  identity system; put real network controls in front of a multi-host hub.

For reverse-proxy deployments, terminate TLS at the proxy and keep the hub bound
to loopback or a private interface behind it. In both native and proxy-terminated
deployments, clients use `wss://host:port` and still pass the shared token for a
secured hub.

For **federation** traffic, treat the proxy as part of the trust boundary. A
plain TLS-terminating reverse proxy presents the proxy certificate to the remote
peer, not the hub certificate; socket-level client certificates also stop at the
proxy unless the proxy runs a separate verified forwarding policy. That is fine
for ordinary token-gated clients, but it is not the same as direct mTLS or a
certificate-pinned hub-to-hub path. Federated peers that rely on certificate
pins or hub-side client certificates should use one of these paths:

- Direct native WSS/mTLS to the hub process.
- TCP/TLS passthrough, so the hub still owns the TLS handshake and sees client
  certificates.
- A private tailnet path, paired with the normal token and pinned-certificate
  ceremony when `wss://` is used.

Declare the intended mode in diagnostics before relying on the path:

```bash
synapse doctor --federation-peer atelier=wss://atelier.example:8876 \
  --federation-path atelier=tls-passthrough \
  --federation-token "$SYNAPSE_TOKEN"
```

`--federation-path atelier=tls-terminating-proxy` intentionally fails for
certificate-pinned federation: it is a different trust boundary, not a direct
hub mTLS path.

A worked example with [Caddy](https://caddyserver.com) terminating TLS in
front of a loopback hub (`reverse_proxy` speaks WebSocket without extra
directives). The hub runs privately with its token:

```console
$ synapse hub --port 8899 --token "$SYNAPSE_TOKEN" --db ~/synapse/hub.db
```

and this `Caddyfile` publishes it as `wss://` on 8443:

```text
{
	auto_https off
}

https://localhost:8443 {
	tls /certs/cert.pem /certs/key.pem
	reverse_proxy 127.0.0.1:8899
}
```

Clients then connect through the proxy:

```console
$ synapse who --uri wss://localhost:8443 --token "$SYNAPSE_TOKEN"
Online (1 agents · 0 waiters):
  USER
```

This exact configuration was validated end to end (Caddy 2 in a container
with host networking, a self-signed certificate with a `localhost` SAN, the
client trusting it via `SSL_CERT_FILE=cert.pem`). For a real deployment,
substitute your hostname for `localhost`, drop the `auto_https off` global
block and the `tls` line, and Caddy provisions publicly trusted certificates
itself; the client-side `SSL_CERT_FILE` override is then unnecessary because
the certificate chains to the system trust store. The proxy terminates TLS
only — the hub still requires its `--token`, and per-host limits keep
applying to the proxy's forwarded connections as one host, so set
`--max-connections-per-host` with that in mind.

Do not reuse this terminating Caddy shape as the certificate-pinned federation
path unless the intended peer pin is the proxy certificate and the deployment has
a separate policy for client identity at the proxy. For the hub certificate to
remain the pinned object, use direct native WSS/mTLS or TCP/TLS passthrough.

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

On `SIGTERM` or `SIGINT`, the hub stops accepting new sockets, closes active
WebSocket sessions through the server close path, and bounds the close handshake
with `--shutdown-close-timeout` (default 5 seconds). Authoritative mutations are
appended when the hub accepts them; shutdown does not batch unflushed claims for
later. If `--db` is enabled, a claim accepted before the stop event replays from
the event log on the next start.

When a waiter re-arms right after its process was killed, its old name can still
linger on the hub for a few seconds (until the keepalive reaps it). A 0.29.0+ client
re-arms with **takeover**: the hub evicts the stale holder (closing it with code
`4010` *superseded*) and rebinds the name, so the re-arm succeeds instead of failing
with a `4009` name conflict. Takeover needs **both ends on 0.29.0+** — the client to
ask for it, the hub to perform the eviction — and a 15-second keepalive reaps a
genuine ghost quickly as the backstop. The swap is atomic from every other
session's point of view: the hub rebinds the name to the new socket *before* the
eviction close handshake runs, so a directed message racing the takeover is
delivered to the new owner, never to the evicted socket, and two takeovers
racing each other can never co-bind one name. The hub logs takeover outcomes without
message payloads: accepted takeovers, cooldown refusals, plain name conflicts,
and name-switch denials include the sender name, remote host, and close reason.

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
