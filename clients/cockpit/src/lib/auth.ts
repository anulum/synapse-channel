// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — session-only cockpit bearer and authenticated fetch adapter

/** Browser-session key holding the dashboard bearer. Never use persistent storage. */
export const COCKPIT_BEARER_KEY = "synapse-cockpit-bearer";

/** Access posture currently visible to the React shell. */
export type CockpitAuthPhase = "probing" | "open" | "unlocked" | "locked";

/** Observable credential state. The bearer itself is deliberately absent. */
export interface CockpitAuthState {
  readonly phase: CockpitAuthPhase;
  readonly revision: number;
  readonly reason: string | null;
}

const listeners = new Set<() => void>();
let initialized = false;
let state: CockpitAuthState = { phase: "probing", revision: 0, reason: null };

function readBearer(): string | null {
  try {
    const value = window.sessionStorage.getItem(COCKPIT_BEARER_KEY);
    return value === null || value.trim() === "" ? null : value;
  } catch {
    return null;
  }
}

function publish(next: CockpitAuthState): void {
  state = next;
  for (const listener of listeners) listener();
}

function initialize(): void {
  if (initialized) return;
  initialized = true;
  state = {
    phase: readBearer() === null ? "probing" : "unlocked",
    revision: state.revision,
    reason: null,
  };
}

/** Return the current token-free state for `useSyncExternalStore`. */
export function cockpitAuthSnapshot(): CockpitAuthState {
  initialize();
  return state;
}

/** Subscribe to credential changes; the callback receives no secret-bearing value. */
export function subscribeCockpitAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Retain a non-empty bearer in `sessionStorage` and open a fresh request generation.
 * Returns false when the input is empty or session storage is unavailable.
 */
export function unlockCockpit(rawBearer: string): boolean {
  const bearer = rawBearer.trim();
  if (bearer === "") return false;
  try {
    window.sessionStorage.setItem(COCKPIT_BEARER_KEY, bearer);
  } catch {
    return false;
  }
  initialize();
  publish({ phase: "unlocked", revision: state.revision + 1, reason: null });
  return true;
}

/** Clear the bearer and hide every live surface behind the unlock veil. */
export function lockCockpit(reason: string): void {
  try {
    window.sessionStorage.removeItem(COCKPIT_BEARER_KEY);
  } catch {
    // The presentation must still lock if browser storage became unavailable.
  }
  initialize();
  if (state.phase === "locked" && state.reason === reason) return;
  publish({ phase: "locked", revision: state.revision + 1, reason });
}

/** Reset to an unauthenticated probe, primarily for a new browser-session boundary. */
export function resetCockpitAuth(): void {
  try {
    window.sessionStorage.removeItem(COCKPIT_BEARER_KEY);
  } catch {
    // A probe remains safe: any protected data request returns 401 and locks.
  }
  initialized = false;
  publish({ phase: "probing", revision: state.revision + 1, reason: null });
}

function markOpenReadSurface(): void {
  initialize();
  if (state.phase !== "probing") return;
  publish({ phase: "open", revision: state.revision, reason: null });
}

function mergedHeaders(input: RequestInfo | URL, init: RequestInit | undefined): Headers {
  const headers = new Headers(input instanceof Request ? input.headers : undefined);
  const overrides = new Headers(init?.headers);
  overrides.forEach((value, name) => headers.set(name, value));
  return headers;
}

/**
 * Fetch one cockpit surface with the session bearer and fail closed on 401.
 *
 * A successful unauthenticated response proves the dashboard retained its normal
 * loopback open-read posture. A 401 clears the credential before notifying React,
 * so no stale live presentation survives a rejected or revoked bearer.
 */
export async function fetchWithCockpitAuth(
  input: RequestInfo | URL,
  init: RequestInit | undefined = undefined,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  initialize();
  const bearer = readBearer();
  const headers = mergedHeaders(input, init);
  if (bearer !== null) headers.set("Authorization", `Bearer ${bearer}`);
  const response = await fetcher(input, { ...init, headers });
  if (response.status === 401) {
    // A response from a superseded request generation must not erase the newer
    // bearer an operator has already pasted while that request was in flight.
    if (readBearer() === bearer) {
      lockCockpit("The dashboard refused that bearer. Paste a current token to unlock it.");
    }
  } else if (bearer === null) {
    markOpenReadSurface();
  }
  return response;
}

/** Shared production adapter used by every cockpit read and write request. */
export function authenticatedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  return fetchWithCockpitAuth(input, init);
}
