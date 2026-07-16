# Secure mode

`synapse hub --secure` is the strict multi-seat production umbrella. It composes
the [`--team-secure`](team-secure.md) trust profile and the
[`--paranoid`](paranoid-mode.md) exposed-hub profile, then adds bounded per-agent,
per-host, and per-host-connection flood limits. One flag turns on the strongest
shipped hub posture and prints one consolidated report.

`--secure` generates no credentials, reuses no secret for a second purpose, and
enables no metrics surface. When required operator material is missing it fails
closed **before** any socket binds or durable store opens, and it lists every
absent input in a single error so the operator can supply them all in one pass.

The mode is for a multi-seat hub that is also network-exposed. For a
loopback-only multi-seat hub use `--team-secure`; for a single-owner exposed hub
use `--paranoid`. `--secure` is an unambiguous superset of both, not a third
overlapping definition of "secure".

## What the umbrella composes

`--secure` forces and validates every gate the two subordinate profiles own:

- a connect token;
- a durable event store (`--db`);
- Ed25519 connection identity binding (`--identity-trust`);
- role-claim grants (`--role-grants`);
- private directed routing;
- sender-bound per-message authentication (`--message-auth-key`);
- deny-by-default ACL enforcement (`--acl-policy`);
- native WSS (`--tls-certfile`/`--tls-keyfile`);
- bearer-only metrics authentication when `--metrics` is enabled;
- the metrics query-token and insecure-off-loopback relaxations disabled.

`team_secure.py` and `paranoid.py` remain the single authorities for their own
checks; the umbrella turns both on and reports once instead of twice.

## Flood limits

`--secure` bounds aggregate traffic. When a rate is left disabled it receives the
named preset default; a stricter positive value is preserved; a value above a
preset ceiling is refused so the named posture is never silently weakened. The
burst allowances are held to the same discipline as the rates: a burst above its
ceiling is refused, and a zeroed burst beside a stricter rate receives the preset
default so the token bucket is never unbounded or absent.

| Limit | Preset default | Ceiling | Behaviour |
| --- | --- | --- | --- |
| Per-agent message rate | 100/s | 100/s | disabled → default; ≤100 kept; >100 refused |
| Per-agent burst | 20 | 20 | disabled → default; ≤20 kept; >20 refused |
| Per-host frame rate | 500/s | 500/s | disabled → default; ≤500 kept; >500 refused |
| Per-host burst | 100 | 100 | disabled → default; ≤100 kept; >100 refused |
| Connections per host | 10 | 10 | disabled → default; 1–10 kept; >10 refused |

A disabled value and an explicit `--rate 0` are indistinguishable and both mean
"disabled"; under `--secure` that is replaced by the named ceiling. Non-finite
values (`nan`, `inf`) are rejected for every rate and burst — at the argument
parser for every hub run, and again by the preset for programmatic callers —
because `nan` compares false against every ceiling and would otherwise disable
the limiter while appearing configured. These numbers follow the external
audit's recommendations and let a small fleet share one loopback or NAT host
while bounding aggregate traffic; they are an approval point, not an empirical
throughput claim.

## Auto flood-enable without `--secure` (REV-SEC-06)

When the hub is **not** started with `--secure`, flood limits left at the
disabled default (`0`) can still be filled automatically if the startup posture
is exposed. The decision lives in `core/rate_policy.py` and is applied at hub
startup after security profiles run and before limiters are built.

Any of the following triggers auto-enable of the same secure bounded defaults
used above (operator-positive limits are preserved; `--secure` already fills and
stands down this path):

- non-loopback bind;
- a connect token (or token file) is configured;
- multi-seat intent (see below);
- bridge exposed (see below).

**`--expect-multi-seat`** (default off) declares that more than one agent seat is
expected. Multi-seat intent is also inferred from `--team-secure` / `--secure`,
`--require-role-claim`, `--require-identity-binding`,
`--private-directed-messages`, a non-empty `--identity-trust` path, or a
non-empty `--role-grants` path. Use the explicit flag when those are absent but
the hub still serves multiple seats on loopback.

**`--bridge-exposed`** (default off) declares that an A2A and/or MCP bridge is
knowingly reachable alongside this hub (separate process or co-located).
Operators running `synapse a2a-serve` or `synapse mcp` against the hub should
pass it: bridge traffic can flood the hub without a connect token, and the hub
does not auto-detect those separate processes.

Local-first loopback single-seat hubs with no token, no multi-seat intent, and
no bridge declaration stay unbounded. Neither flag generates credentials or
enables a bridge; they only feed the exposure posture for flood auto-enable.

## Required operator material

`--secure` still needs the operator to supply real material — the flag checks
presence; the existing loaders remain the authority for parsing and cryptographic
validation. Deliver every secret from an owner-only (`chmod 600`) regular file
owned by the effective hub service user: an argv value is visible to anyone on
the machine running `ps`, so the file-backed companions are the production
forms on POSIX systems.

```bash
synapse hub --secure \
  --token-file /run/secrets/hub-token \
  --db /var/lib/synapse/hub.db \
  --identity-trust /etc/synapse/identity-trust.json \
  --role-grants /etc/synapse/role-grants.json \
  --message-auth-key-file /run/secrets/hub-hmac-keys \
  --acl-policy /etc/synapse/acl.json \
  --tls-certfile /etc/synapse/hub.crt \
  --tls-keyfile /etc/synapse/hub.key
```

`--message-auth-key-file` holds one `KEY_ID:SECRET:SENDER[,SENDER...]` entry per
line (`#` comments allowed), so key rotation is an edit to one file owned by the
same unprivileged account that runs the hub; entries merge with any argv
`--message-auth-key` values. The loader opens one bounded descriptor with
`O_NOFOLLOW`, then requires that same descriptor to be a regular file owned by
the effective service user with no group/other permissions. Symlinks,
non-regular files, foreign owners, oversized content, and path-replacement races
fail closed. Errors name only the flag and path, never content. Platforms that
cannot prove POSIX ownership and mode invariants refuse these file forms; run
the hub under WSL/POSIX or use a separately validated native secret provider.
Missing material fails closed with one aggregate error. Add
`--metrics-token-file` (or `--metrics-token`) when `--metrics` is enabled; the
explicit argv token wins when both are present, mirroring `--token`/`--token-file`.

## What the report says

Startup prints one `secure mode enforced:` line naming the composed gates, one
`secure mode effective limits:` line with the exact rates and connection cap in
force, and one `secure mode missing hooks:` line listing controls the preset does
**not** compose (reusing the paranoid vocabulary). The command never implies that
one flag makes an exposed deployment safe; it makes the current posture obvious,
repeatable, and auditable.

## Boundaries

`--secure` is a composition and enforcement switch, not a key manager. It does not
rotate credentials, verify client certificates, load signed-event trust, or
certify a deployment. Those controls ship separately and are listed in the missing
hooks report so an operator can add them deliberately.
