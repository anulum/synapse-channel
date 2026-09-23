<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Vendor and provider discovery

Discovery is a read-only candidate intake. It reads the public
[Models.dev provider catalog](https://github.com/anomalyco/models.dev/blob/dev/README.md#api),
the [official MCP Registry](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/official-registry-api.md),
and a bounded public GitHub repository-topic search. A catalog mention can
be wrong, stale or impersonated. It never installs an extension, chooses an
API route, receives credentials or establishes compatibility.

Run a local scan:

```bash
python tools/vendor_discovery.py --strict \
  --report build/vendor-discovery.json \
  --next-catalog build/vendor-catalog-proposal.json
```

The tracked `integrations/vendor-discovery/sources.json`
names the weekly owner and manual alias decisions. The tracked
`integrations/vendor-discovery/catalog.json` is the committed
observation baseline for provider and MCP signals. Raw unreviewed GitHub host
names are kept in the owner-private review record, not this public baseline.
Its records are unreviewed signals, not admitted integrations. The command
leaves both tracked inputs untouched and writes a dated
report plus a proposed next catalog. A maintainer reviews the report before
replacing the baseline; unreviewed host search hits must stay out of a public
catalog until source and publisher review is recorded. The proposal contains identifiers, publishers, first
and last sighting times and per-source SHA-256 digests. The report contains
the small source claims that produced each review item. Raw model lists,
credential names and release bodies are not retained in the baseline.

Candidate kinds distinguish a model/API provider, an MCP server and an agent
host. Each has a publisher and product identifier; API endpoint and transport
are separate source claims. Account, privacy, pricing, support level and
licence are unknown unless a feed explicitly supplies a signal, and even that
signal needs independent review. Aliases must be hand-entered for a proven
rename. A publisher mismatch after aliasing becomes `publisher_conflict`, not
an automatic merge.

The MCP scan reads at most three 100-record pages of updates from the last
seven days. GitHub topic search reads at most 100 best-match and 100 recently
updated results. The scanner inspects at most 40 host trees, prioritising
Pages repositories without detected code or licence, and checks at most 25
new MCP repository URLs. It reads bounded GitHub API metadata, README and
workflow text; it never follows Pages or download links. A host with source
code, release asset digest, older owner history and detected licence ranks
above a recent push. A README/Pages download lure paired with a tiny source-free
tree and a five-minute write-and-commit workflow is rejected. Uninspected hosts
have no suggested integration lane. An unreachable MCP repository is held for
broken provenance. Inspection limits and unavailable metadata are visible in
the report. These partial
sources never prove that an absent candidate was removed. Only a complete
Models.dev snapshot, a complete small GitHub result, or an explicit registry
`deleted` status can propose withdrawal. A failed feed is `unavailable` or
`stale`; it never becomes an empty snapshot. HTTPS source hosts, query shapes,
response sizes and timeouts are fixed. Only canonical GitHub repository API
URLs are fetched for candidate inspection; no candidate website is fetched.

The weekly `.github/workflows/vendor-discovery.yml` workflow publishes the
report and proposed baseline as seven-day artifacts. The Core maintenance
seat reviews them weekly and before adding a new integration. To promote a
candidate, create an owned C15 host-package or C07 provider task. Review its
official documentation, publisher control, licence, security, protocol and
version pin, privacy/data locality, authentication, entitlement and costs.
Then run a real isolated host or API journey. Fleet F09 can consume the
accepted compatibility contract only after Core has verified it. Discovery
does not send outreach, spend API credit, publish support claims or deploy.

The 2026-09-19 first scan recorded 622 unreviewed observations: 222 provider
entries from a complete Models.dev response, 300 MCP entries from the bounded
incremental pages and 100 GitHub topic results. The latter two feeds were
partial. The first report queued all 622 for triage; a repeat against the
saved baseline queued no unchanged entries. The 0.99.27 public baseline omits
the 100 host names; their original record is retained privately for review.
These counts describe the specific dated snapshot, not available or supported
Synapse integrations.
