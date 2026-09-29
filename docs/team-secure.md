# Team-secure mode

`synapse hub --team-secure` is the multi-seat *trust* profile for a local fleet
of coding agents that share one hub. It fails closed unless connection identity
is proven, role claims are granted, and directed messages are audience-routed.

It is intentionally lighter than [`--paranoid`](paranoid-mode.md):

| Concern | `--team-secure` | `--paranoid` |
| --- | --- | --- |
| Connect token | Required | Required |
| Durable `--db` | Recommended | Required |
| Identity binding (`--identity-trust`) | Required | Optional (still a missing-hook note) |
| Role-claim grants (`--role-grants`) | Required | Optional |
| Private directed messages | Forced on | Optional |
| Per-message HMAC | Recommended | Required |
| ACL enforcement | Recommended | Required |
| Native WSS (TLS) | Recommended when off-loopback | Required |

Use **`--team-secure` alone** for a loopback multi-agent workstation. Use
**`--team-secure --paranoid`** (plus the material both profiles demand) when the
same multi-seat hub is also network-exposed.

## What it enforces

1. **`--token` / `--token-file` / `SYNAPSE_TOKEN`** — identity and role grants are
   only as strong as the connect gate.
2. **`--identity-trust FILE`** — Ed25519 trust bundle; the profile forces
   `--require-identity-binding` so a socket must prove its registration before
   a name binds.
3. **`--role-grants FILE`** — deny-by-default store (written by `synapse role`);
   the profile forces `--require-role-claim` so unauthorised roles are dropped
   instead of squatted.
4. **Private directed messages** — forced on, so a directed chat is delivered
   only to its recipients (and `-rx` sidecars) plus identities with the ACL
   `observe` grant, not to every socket.
5. **Durable sequence floors, when they can run** — with
   `--require-message-auth` and a durable replay ledger (`--db` or
   `--message-auth-replay-db`), an unset `--message-auth-sequence-floor-mode`
   becomes `compat`: each key's high-water sequence is recorded, and nothing is
   refused on that basis. `strict` stays an explicit choice. Clients up to Core
   0.99.31 number their frames from 1 in every process, so a strict floor
   refuses such a restarted agent's first frames (`sequence_mismatch`). Later
   clients derive the sequence from the clock and pass `strict` after a
   restart. Choose `strict` once every seat runs such a client. Both behaviours
   are tested through the public claim route.

On startup the hub prints what was enforced and a short **recommended next**
list (message-auth, ACL, TLS/`--paranoid`, durable `--db`) when those are still
off. Recommendations never block startup.

## Minimal loopback example

```bash
# Once: identity key + trust bundle, role grant store
synapse identity keygen --sender proj/claude --key-id claude-1 \
  --private-out claude.pem --trust trust.json
synapse role grant proj/coordinator --to proj/claude --store role-grants.json

synapse hub --db ~/synapse/hub.db --token-file ~/synapse/token \
  --team-secure \
  --identity-trust trust.json \
  --role-grants role-grants.json
```

Agents that connect must use the shared token **and** sign registration under a
key enrolled in the trust bundle. Every client already signs with this machine's
auto-provisioned key, so the shortest path for a seat is to enrol that key for
all the names it uses, in one call per machine:

```bash
synapse identity machine-key --sender proj/claude --sender proj/claude-rx --trust trust.json
```

On a different machine than the hub, run it without `--trust` and add the printed
entry to the hub's bundle. Role heartbeats only stick when the grant store
**or** an ACL `role-claim` rule allows them. Directed chat is no longer a
broadcast to every connected socket. A trusted monitor may replay another
identity's mailbox only with an ACL `mailbox` rule (self and `-rx` still work
without one).

## Doctor checklist

```bash
synapse doctor --multi-seat \
  --token-file ~/synapse/token \
  --identity-trust trust.json \
  --role-grants role-grants.json
```

With a multi-seat roster (or `--multi-seat`), doctor warns when the connect
token, trust bundle, or role-grant store is missing, and points at this profile.
It also flags **deaf agents** (live seats without a matching `-rx` waiter).

## Related

- [Identity and ACL](identity-and-acl.md)
- [Paranoid mode](paranoid-mode.md) (production / exposed bind preset)
- [Deployment](deployment.md)
- [Quick start — multi-seat golden path](quickstart.md)
