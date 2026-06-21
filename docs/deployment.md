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
