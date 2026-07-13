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

## Required operator material

`--secure` still needs the operator to supply real material — the flag checks
presence; the existing loaders remain the authority for parsing and cryptographic
validation. Deliver every secret from an owner-only (`chmod 600`) file: an argv
value is visible to anyone on the machine running `ps`, so the file-backed
companions are the production forms.

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
line (`#` comments allowed), so key rotation is an edit to one root-owned file;
entries merge with any argv `--message-auth-key` values. The loader refuses a
file that other users can read and reports problems by flag and path, never by
content. Missing material fails closed with one aggregate error. Add
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
