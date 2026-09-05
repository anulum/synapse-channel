// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — host wire contract tests from real process observations
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { parseHostObservation } from "../../src/lib/hostSessions";

const localPython = "../../.venv/bin/python";
const python = process.env["SYNAPSE_COCKPIT_E2E_PYTHON"] ??
  (existsSync(localPython) ? localPython : "python");

function observed(): Record<string, unknown> {
  return JSON.parse(execFileSync(python, [
    "-m", "synapse_channel.cli", "pid-monitor", "--pid", String(process.pid),
    "--tmux-socket", "/nonexistent/host-parser-test.sock", "--json",
  ], { encoding: "utf8", timeout: 10000 })) as Record<string, unknown>;
}
const source = observed();
function document(): Record<string, unknown> { return structuredClone(source); }
describe("host observation wire contract", () => {
  it("accepts real procfs JSON without inventing provider presence", () => {
    const parsed = parseHostObservation(source);
    expect(parsed?.rows[0]?.pid).toBe(process.pid);
    expect(parsed?.rows[0]?.presence).toBeNull();
  });
  it.each([null, [], "", {}, { ...source, version: 2 }, { ...source, version: true }])(
    "refuses incompatible envelopes", (value) => expect(parseHostObservation(value)).toBeNull(),
  );
  it.each(["host_ref", "observation_id", "observer_instance_id"])("requires %s", (key) => {
    for (const value of [null, "", 3, "x".repeat(4097)]) {
      expect(parseHostObservation({ ...source, [key]: value })).toBeNull();
    }
  });
  it.each(["observed_at", "valid_for_seconds"])("requires a finite positive %s", (key) => {
    for (const value of [null, -1, 0, Infinity, NaN, "1"]) {
      expect(parseHostObservation({ ...source, [key]: value })).toBeNull();
    }
  });
  it("refuses unreasonable freshness and invalid adapter states", () => {
    expect(parseHostObservation({ ...source, valid_for_seconds: 61 })).toBeNull();
    for (const key of ["process_status", "tmux_status", "coordination_status"]) {
      expect(parseHostObservation({ ...source, [key]: "invented" })).toBeNull();
    }
  });
  it("refuses oversized rows and duplicate process references", () => {
    expect(parseHostObservation({ ...source, rows: null })).toBeNull();
    expect(parseHostObservation({ ...source, rows: Array(257).fill(null) })).toBeNull();
    expect(parseHostObservation({ ...source, rows: [null] })).toBeNull();
    const rows = source["rows"] as unknown[];
    expect(parseHostObservation({ ...source, rows: [rows[0], rows[0]] })).toBeNull();
  });
  it("accepts a real kernel start time that never postdates the observation", () => {
    const parsed = parseHostObservation(source);
    const row = parsed?.rows[0];
    expect(row?.started_at_status).toBe("observed");
    expect(typeof row?.started_at).toBe("number");
    expect(row?.started_at as number).toBeLessThanOrEqual(parsed?.observed_at as number);
  });
  it("enforces process start evidence consistency", () => {
    const value = document();
    const row = (value["rows"] as Record<string, unknown>[])[0]!;
    row["started_at_status"] = "unavailable";
    row["started_at"] = null;
    expect(parseHostObservation(value)).not.toBeNull();
    row["started_at"] = 1;
    expect(parseHostObservation(value)).toBeNull();
    row["started_at_status"] = "observed";
    row["started_at"] = null;
    expect(parseHostObservation(value)).toBeNull();
    row["started_at"] = value["observed_at"];
    expect(parseHostObservation(value)).not.toBeNull();
    for (const bad of [(value["observed_at"] as number) + 1, 0, -1, NaN, Infinity, "1", true]) {
      row["started_at"] = bad;
      expect(parseHostObservation(value)).toBeNull();
    }
    row["started_at"] = 1;
    row["started_at_status"] = "invented";
    expect(parseHostObservation(value)).toBeNull();
    row["started_at_status"] = undefined;
    expect(parseHostObservation(value)).toBeNull();
  });
  it.each(["reference", "command_name", "state", "identity_source",
    "provider", "identity", "project", "session", "pane", "cwd", "context_id",
    "pid", "parent_pid", "start_ticks", "attached", "presence",
    "duplicate_identity", "paths_requested", "context_requested", "cwd_status", "context_status", "waiters", "claims",
    "started_at", "started_at_status"])(
    "rejects malformed %s rather than rendering misleading evidence", (key) => {
      const value = document();
      const rows = value["rows"] as Record<string, unknown>[];
      rows[0]![key] = {};
      expect(parseHostObservation(value)).toBeNull();
    },
  );
  it("rejects zero PID and out-of-range numeric identities", () => {
    for (const key of ["pid", "parent_pid", "start_ticks"]) {
      for (const input of [-1, 1.2, Number.MAX_SAFE_INTEGER + 1]) {
        const value = document();
        (value["rows"] as Record<string, unknown>[])[0]![key] = input;
        expect(parseHostObservation(value)).toBeNull();
      }
    }
    const value = document();
    (value["rows"] as Record<string, unknown>[])[0]!["pid"] = 0;
    expect(parseHostObservation(value)).toBeNull();
  });
  it("requires an observation time for available coordination evidence", () => {
    for (const status of ["complete", "partial"]) {
      const available = { ...source, coordination_status: status };
      for (const time of [null, undefined, "1", 0, -1, NaN, Infinity,
        (source["observed_at"] as number) + 1]) {
        expect(parseHostObservation({ ...available, coordination_observed_at: time })).toBeNull();
      }
      expect(parseHostObservation({ ...available,
        coordination_observed_at: source["observed_at"] })).not.toBeNull();
    }
    expect(parseHostObservation({ ...source, coordination_observed_at: 1 })).toBeNull();
    expect(parseHostObservation({ ...source, coordination_observed_at: undefined })).toBeNull();
  });
  it("refuses coercible statuses, empty evidence and oversized coordination lists", () => {
    for (const key of ["process_status", "tmux_status", "coordination_status"]) {
      for (const status of [["complete"], { toString: () => "complete" }, null]) {
        expect(parseHostObservation({ ...source, [key]: status })).toBeNull();
      }
    }
    for (const key of ["reference", "command_name", "state", "identity_source"]) {
      const value = document();
      (value["rows"] as Record<string, unknown>[])[0]![key] = "";
      expect(parseHostObservation(value)).toBeNull();
    }
    for (const key of ["waiters", "claims"]) {
      for (const list of [Array(1025).fill("task"), ["x".repeat(4097)], [1]]) {
        const value = document();
        (value["rows"] as Record<string, unknown>[])[0]![key] = list;
        expect(parseHostObservation(value)).toBeNull();
      }
    }
  });
  it.each([
    ["cwd", "cwd_status", "paths_requested"],
    ["context_id", "context_status", "context_requested"],
  ])("enforces %s evidence and disclosure consistency", (valueKey, statusKey, grantKey) => {
    const value = document();
    const row = (value["rows"] as Record<string, unknown>[])[0]!;
    row[grantKey!] = true;
    for (const status of ["unavailable", "denied", "conflicting", "partial", "unsupported"]) {
      row[statusKey!] = status;
      row[valueKey!] = null;
      expect(parseHostObservation(value)).not.toBeNull();
      row[valueKey!] = "cannot display unproven metadata";
      expect(parseHostObservation(value)).toBeNull();
    }
    row[statusKey!] = "observed";
    row[valueKey!] = "observed metadata";
    expect(parseHostObservation(value)).not.toBeNull();
    row[valueKey!] = "";
    expect(parseHostObservation(value)).toBeNull();
    row[valueKey!] = null;
    expect(parseHostObservation(value)).toBeNull();
    row[statusKey!] = "not_requested";
    expect(parseHostObservation(value)).toBeNull();
    row[grantKey!] = false;
    expect(parseHostObservation(value)).not.toBeNull();
    row[statusKey!] = "unavailable";
    expect(parseHostObservation(value)).toBeNull();
    row[statusKey!] = "invented";
    expect(parseHostObservation(value)).toBeNull();
  });
});
