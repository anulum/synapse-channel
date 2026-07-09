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

On startup the hub prints what was enforced and a short **recommended next**
list (message-auth, ACL, TLS/`--paranoid`, durable `--db`) when those are still
off. Recommendations never block startup.

## Minimal loopback example

```bash
# Once: identity key + trust bundle, role grant store
synapse identity keygen --subject proj/claude --out-key claude.pem --enroll trust.json
synapse role grant proj/coordinator proj/claude --store role-grants.json

synapse hub --db ~/synapse/hub.db --token-file ~/synapse/token \
  --team-secure \
  --identity-trust trust.json \
  --role-grants role-grants.json
```

Agents that connect must use the shared token **and** sign registration under a
key enrolled in the trust bundle. Role heartbeats only stick when the grant store
allows them. Directed chat is no longer a broadcast to every connected socket.

## Related

- [Identity and ACL](identity-and-acl.md)
- [Paranoid mode](paranoid-mode.md) (production / exposed bind preset)
- [Deployment](deployment.md)
