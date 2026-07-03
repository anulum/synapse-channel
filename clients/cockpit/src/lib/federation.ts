// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the federation posture feed: hub identity, peers, partition honesty

// PROPOSED server contract (the dashboard does not serve /federation.json yet;
// the Python side owns the final shape and this parser tolerates partial
// payloads field-by-field):
//
//   {
//     "hub_id":  str,          — the serving hub's identity
//     "domain":  str,          — this hub's federation domain, "" when unfederated
//     "peerings": [{"domain": str, "state": "active"|"revoked"|"stale",
//                   "imported_at": epoch|null, "fingerprint": str}],
//     "namespaces": [{"namespace": str,
//                     "outcome": "local"|"remote"|"ungoverned"|"partitioned",
//                     "owner_hub": str, "contesting": [str]}]
//   }
//
// Partition honesty is the row's reason to exist: a `partitioned` namespace —
// more than one hub asserting ownership — must be loud, because the hub is
// refusing claims there until the operators heal the split.

import { createEndpointFeed, type EndpointFeed, type FeedState } from "./feed";

/** One imported peering as the federation store records it. */
export interface PeeringView {
  readonly domain: string;
  /** Lifecycle state the server reports: `active`, `revoked`, or `stale`. */
  readonly state: string;
  /** Epoch seconds the bundle was imported, or null when unknown. */
  readonly importedAt: number | null;
  readonly fingerprint: string;
}

/** One governed namespace and who asserts ownership of it. */
export interface NamespaceView {
  readonly namespace: string;
  /** The ownership outcome: `local`, `remote`, `ungoverned`, or `partitioned`. */
  readonly outcome: string;
  readonly ownerHub: string;
  /** Hubs contesting ownership when partitioned; empty otherwise. */
  readonly contesting: readonly string[];
}

/** The hub's federation posture as the proposed endpoint reports it. */
export interface FederationPosture {
  readonly hubId: string;
  readonly domain: string;
  readonly peerings: readonly PeeringView[];
  readonly namespaces: readonly NamespaceView[];
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asEpochOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parsePeering(value: unknown): PeeringView {
  const peering = asRecord(value);
  return {
    domain: asString(peering["domain"]),
    state: asString(peering["state"]),
    importedAt: asEpochOrNull(peering["imported_at"]),
    fingerprint: asString(peering["fingerprint"]),
  };
}

function parseNamespace(value: unknown): NamespaceView {
  const namespace = asRecord(value);
  return {
    namespace: asString(namespace["namespace"]),
    outcome: asString(namespace["outcome"]),
    ownerHub: asString(namespace["owner_hub"]),
    contesting: Array.isArray(namespace["contesting"])
      ? namespace["contesting"].filter((item): item is string => typeof item === "string")
      : [],
  };
}

/**
 * Shape an untrusted federation payload into a {@link FederationPosture}.
 * Returns `null` only when the payload is not an object at all.
 */
export function parseFederation(raw: unknown): FederationPosture | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  return {
    hubId: asString(payload["hub_id"]),
    domain: asString(payload["domain"]),
    peerings: Array.isArray(payload["peerings"]) ? payload["peerings"].map(parsePeering) : [],
    namespaces: Array.isArray(payload["namespaces"])
      ? payload["namespaces"].map(parseNamespace)
      : [],
  };
}

/** The contested namespaces — the row's alarm condition. */
export function contestedNamespaces(posture: FederationPosture): NamespaceView[] {
  return posture.namespaces.filter((entry) => entry.outcome === "partitioned");
}

/** The federation feed's state; `absent` means the hub serves no endpoint. */
export type FederationState = FeedState<FederationPosture>;

export interface FederationStoreOptions {
  /** Endpoint to poll; defaults to the proposed `/federation.json`. */
  readonly url?: string;
  /** Poll cadence in milliseconds; posture changes rarely, so poll slowly. */
  readonly pollMs?: number;
  /** Injectable fetch for tests; defaults to the global. */
  readonly fetcher?: typeof fetch;
  /** Injectable clock for tests; defaults to `Date.now`. */
  readonly now?: () => number;
}

const DEFAULT_FEDERATION_URL = "/federation.json";
const DEFAULT_FEDERATION_POLL_MS = 20_000;

/**
 * Poll the hub's federation posture with the shared feed lifecycle: `404`
 * reports `absent` and keeps re-checking, so the row comes alive the moment
 * the server side ships.
 */
export function createFederationStore(
  options: FederationStoreOptions = {},
): EndpointFeed<FederationPosture> {
  return createEndpointFeed({
    url: options.url ?? DEFAULT_FEDERATION_URL,
    pollMs: options.pollMs ?? DEFAULT_FEDERATION_POLL_MS,
    parse: parseFederation,
    ...(options.fetcher !== undefined ? { fetcher: options.fetcher } : {}),
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
}
