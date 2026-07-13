// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — bounded editor reconnect policy

/** Pure backoff calculation shared by editor transports. */

/** First retry ceiling after a disconnected socket. */
export const RECONNECT_BASE_DELAY_MS = 500;

/** Longest interval between automatic connection attempts. */
export const RECONNECT_MAX_DELAY_MS = 30_000;

/**
 * Return a bounded exponential delay with equal jitter.
 *
 * `entropy` is supplied by the caller so tests can cover the production
 * calculation without replacing timers or randomness. The returned delay is
 * always between half and all of the attempt's exponential ceiling.
 */
export function reconnectDelayMs(attempt: number, entropy: number): number {
  const boundedAttempt = Number.isFinite(attempt)
    ? Math.max(0, Math.min(30, Math.trunc(attempt)))
    : 0;
  const boundedEntropy = Number.isFinite(entropy)
    ? Math.max(0, Math.min(1, entropy))
    : 0;
  const ceiling = Math.min(
    RECONNECT_MAX_DELAY_MS,
    RECONNECT_BASE_DELAY_MS * 2 ** boundedAttempt,
  );
  return Math.round(ceiling * (0.5 + boundedEntropy * 0.5));
}
