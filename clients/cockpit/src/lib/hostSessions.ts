// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — host observation wire parser
/** Host observations are separate from cost/turn telemetry and executable targets. */
export type MetadataStatus = "not_requested" | "observed" | "unavailable" | "denied" |
  "conflicting" | "partial" | "unsupported";
export interface HostSession {
  readonly reference: string;
  readonly pid: number;
  readonly parent_pid: number;
  readonly start_ticks: number;
  readonly command_name: string;
  readonly state: string;
  readonly provider: string | null;
  readonly identity: string | null;
  readonly project: string | null;
  readonly session: string | null;
  readonly pane: string | null;
  readonly attached: boolean | null;
  readonly identity_source: string;
  readonly duplicate_identity: boolean;
  readonly cwd: string | null;
  readonly context_id: string | null;
  readonly cwd_status: MetadataStatus;
  readonly context_status: MetadataStatus;
  readonly paths_requested: boolean;
  readonly context_requested: boolean;
  readonly presence: boolean | null;
  readonly waiters: readonly string[];
  readonly claims: readonly string[];
  /** Kernel start time in Unix seconds (boot time + start ticks, about ±1 s), or null. */
  readonly started_at: number | null;
  readonly started_at_status: "observed" | "unavailable";
}
export interface HostObservation {
  readonly version: 1;
  readonly observation_id: string;
  readonly observer_instance_id: string;
  readonly host_ref: string;
  readonly observed_at: number;
  readonly valid_for_seconds: number;
  readonly process_status: string;
  readonly tmux_status: string;
  readonly coordination_status: string;
  readonly coordination_observed_at: number | null;
  readonly rows: readonly HostSession[];
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function bounded(value: unknown): value is string {
  return typeof value === "string" && value.length <= 4096;
}
function integer(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}
/** Reject unknown versions, malformed rows, duplicate references and oversized documents. */
export function parseHostObservation(value: unknown): HostObservation | null {
  if (!record(value) || value["version"] !== 1) return null;
  for (const key of ["observation_id", "observer_instance_id", "host_ref"]) {
    if (!bounded(value[key]) || value[key] === "") return null;
  }
  for (const key of ["observed_at", "valid_for_seconds"]) {
    if (typeof value[key] !== "number" || !Number.isFinite(value[key]) || value[key] <= 0) return null;
  }
  if ((value["valid_for_seconds"] as number) > 60) return null;
  for (const key of ["process_status", "tmux_status", "coordination_status"]) {
    if (typeof value[key] !== "string" ||
        !["complete", "partial", "unavailable"].includes(value[key])) return null;
  }
  const coordinationTime = value["coordination_observed_at"];
  if (value["coordination_status"] === "unavailable") {
    if (coordinationTime !== null) return null;
  } else if (typeof coordinationTime !== "number" || !Number.isFinite(coordinationTime) ||
             coordinationTime <= 0 || coordinationTime > (value["observed_at"] as number)) return null;
  const rows = value["rows"];
  if (!Array.isArray(rows) || rows.length > 256) return null;
  const references = new Set<string>();
  for (const row of rows) {
    if (!record(row)) return null;
    for (const key of ["reference", "command_name", "state", "identity_source"]) {
      if (!bounded(row[key]) || row[key] === "") return null;
    }
    if (!integer(row["pid"]) || row["pid"] === 0 ||
        !integer(row["parent_pid"]) || !integer(row["start_ticks"])) return null;
    for (const key of ["provider", "identity", "project", "session", "pane", "cwd", "context_id"]) {
      if (row[key] !== null && !bounded(row[key])) return null;
    }
    if (row["attached"] !== null && typeof row["attached"] !== "boolean") return null;
    if (row["presence"] !== null && typeof row["presence"] !== "boolean") return null;
    for (const key of ["waiters", "claims"]) {
      if (!Array.isArray(row[key]) || row[key].length > 1024 || !row[key].every(bounded)) return null;
    }
    for (const key of ["duplicate_identity", "paths_requested", "context_requested"]) {
      if (typeof row[key] !== "boolean") return null;
    }
    for (const [valueKey, statusKey, grantKey] of [
      ["cwd", "cwd_status", "paths_requested"],
      ["context_id", "context_status", "context_requested"],
    ] as const) {
      const status = row[statusKey];
      if (typeof status !== "string" || !["not_requested", "observed", "unavailable",
        "denied", "conflicting", "partial", "unsupported"].includes(status)) return null;
      if ((status === "observed") !== (row[valueKey] !== null)) return null;
      if ((status === "not_requested") !== !row[grantKey]) return null;
      if (row[valueKey] === "") return null;
    }
    const startStatus = row["started_at_status"];
    if (startStatus !== "observed" && startStatus !== "unavailable") return null;
    const startedAt = row["started_at"];
    if ((startStatus === "observed") !== (startedAt !== null)) return null;
    if (startedAt !== null && (typeof startedAt !== "number" || !Number.isFinite(startedAt) ||
        startedAt <= 0 || startedAt > (value["observed_at"] as number))) return null;
    const reference = row["reference"] as string;
    if (references.has(reference)) return null;
    references.add(reference);
  }
  return value as unknown as HostObservation;
}
