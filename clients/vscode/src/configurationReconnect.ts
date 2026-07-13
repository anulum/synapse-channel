// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — latest-configuration gate for asynchronous credential reads

/** Prevent an older SecretStorage read from replacing a newer hub configuration. */

/** Result of one asynchronous read tied to a configuration generation. */
export type ConfigurationReadResult<T> =
  | { kind: "current"; value: T }
  | { kind: "stale" }
  | { kind: "error" };

/** Issue monotonically increasing attempts and reject out-of-order completions. */
export class ConfigurationReconnectGate {
  private generation = 0;

  /** Invalidate every earlier read and return the new generation. */
  begin(): number {
    this.generation += 1;
    return this.generation;
  }

  /** Resolve a credential read only if its configuration is still current. */
  async read<T>(generation: number, reader: () => Promise<T>): Promise<ConfigurationReadResult<T>> {
    try {
      const value = await reader();
      return generation === this.generation
        ? { kind: "current", value }
        : { kind: "stale" };
    } catch {
      return generation === this.generation ? { kind: "error" } : { kind: "stale" };
    }
  }
}
