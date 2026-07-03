// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — fetch and shape recorded causality traces for the inspector

// The inspector renders `synapse causality causes|effects --json` verbatim: the
// focus event, its directly related events (with the recorded relation), and the
// transitive closure. Federated traces identify events globally as
// `(hub_id, seq)`; when nodes carry a hub attribution the view clusters them per
// hub, mirroring the CLI's --dot semantics. Nothing is inferred client-side —
// the hub's recorded relations are the only edges drawn.

/** One recorded event in a causality trace. */
export interface CausalityNode {
  readonly seq: number;
  readonly kind: string;
  readonly owner: string;
  readonly taskId: string;
  /** Epoch seconds, or null when the record omitted it. */
  readonly ts: number | null;
  readonly status: string;
  readonly text: string;
  readonly worktree: string;
  /** Owning hub for federated traces; empty on single-hub traces. */
  readonly hubId: string;
  readonly paths: readonly string[];
  readonly dependsOn: readonly string[];
}

/** One recorded relation between two events. */
export interface CauseEdge {
  readonly src: number;
  readonly dst: number;
  /** The hub's relation label, e.g. `lifecycle`, `dependency`, `federation`. */
  readonly relation: string;
  /** The hub's one-line description of the recorded relation. */
  readonly detail: string;
  readonly node: CausalityNode;
}

/** A causes/effects trace as the hub's causality engine reports it. */
export interface CausalityTrace {
  readonly direction: string;
  readonly seq: number;
  /** Whether the event is in the coordination causal graph (not: the log). */
  readonly present: boolean;
  readonly node: CausalityNode | null;
  readonly direct: readonly CauseEdge[];
  readonly transitive: readonly CausalityNode[];
  /**
   * The server's own explanation for a `present: false` answer — recorded but
   * outside the causal graph (chatter) versus no event at that sequence.
   */
  readonly note: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function asInt(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : 0;
}

function asEpochOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function parseNode(value: unknown): CausalityNode {
  const node = asRecord(value);
  return {
    seq: asInt(node["seq"]),
    kind: asString(node["kind"]),
    owner: asString(node["owner"]),
    taskId: asString(node["task_id"]),
    ts: asEpochOrNull(node["ts"]),
    status: asString(node["status"]),
    text: asString(node["text"]),
    worktree: asString(node["worktree"]),
    hubId: asString(node["hub_id"]),
    paths: asStringArray(node["paths"]),
    dependsOn: asStringArray(node["depends_on"]),
  };
}

function parseEdge(value: unknown): CauseEdge {
  const edge = asRecord(value);
  return {
    src: asInt(edge["src"]),
    dst: asInt(edge["dst"]),
    relation: asString(edge["relation"]),
    detail: asString(edge["detail"]),
    node: parseNode(edge["node"]),
  };
}

/**
 * Shape an untrusted causality payload into a {@link CausalityTrace}. Returns
 * `null` only when the payload is not an object at all; a `present: false`
 * trace is a valid answer (the event does not exist) and parses normally.
 */
export function parseTrace(raw: unknown): CausalityTrace | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const payload = asRecord(raw);
  const nodeRaw = payload["node"];
  return {
    direction: asString(payload["direction"]),
    seq: asInt(payload["seq"]),
    present: payload["present"] === true,
    node: typeof nodeRaw === "object" && nodeRaw !== null ? parseNode(nodeRaw) : null,
    direct: Array.isArray(payload["direct"]) ? payload["direct"].map(parseEdge) : [],
    transitive: Array.isArray(payload["transitive"])
      ? payload["transitive"].map(parseNode)
      : [],
    note: asString(payload["note"]),
  };
}

/**
 * Group a trace's transitive nodes by owning hub, mirroring the CLI's
 * cluster-per-hub --dot layout. Single-hub traces (no `hub_id` anywhere)
 * yield one unnamed cluster.
 */
export function clusterByHub(
  nodes: readonly CausalityNode[],
): { hubId: string; nodes: CausalityNode[] }[] {
  const clusters = new Map<string, CausalityNode[]>();
  for (const node of nodes) {
    const bucket = clusters.get(node.hubId);
    if (bucket === undefined) clusters.set(node.hubId, [node]);
    else bucket.push(node);
  }
  return [...clusters.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([hubId, grouped]) => ({ hubId, nodes: grouped }));
}

/** The inspector's query outcome. `absent` = the hub serves no causality endpoint. */
export type TraceResult =
  | { readonly kind: "loaded"; readonly trace: CausalityTrace }
  | { readonly kind: "absent" }
  | { readonly kind: "error"; readonly message: string };

/** What the operator asked for: a hub event seq, or a task id to resolve. */
export interface TraceQuery {
  /** Digits query a seq directly; anything else asks the server to resolve a task id. */
  readonly subject: string;
  readonly direction: "causes" | "effects";
}

const CAUSALITY_URL = "/causality.json";

/** Build the causality endpoint URL for a query (exported for the contract test). */
export function traceUrl(query: TraceQuery, base: string = CAUSALITY_URL): string {
  const params = new URLSearchParams();
  const subject = query.subject.trim();
  if (/^\d+$/.test(subject)) params.set("seq", subject);
  else params.set("task", subject);
  params.set("direction", query.direction);
  return `${base}?${params.toString()}`;
}

/**
 * Fetch one causality trace on demand. A `404` means the dashboard serving
 * this cockpit does not expose the causality endpoint; other failures carry
 * their reason. The caller owns retry policy — this is a single query, not
 * a poll.
 */
export async function fetchTrace(
  query: TraceQuery,
  fetcher: typeof fetch = fetch,
): Promise<TraceResult> {
  try {
    const response = await fetcher(traceUrl(query));
    if (response.status === 404) return { kind: "absent" };
    if (!response.ok) return { kind: "error", message: `hub returned ${response.status}` };
    const trace = parseTrace(await response.json());
    if (trace === null) return { kind: "error", message: "causality payload was not an object" };
    return { kind: "loaded", trace };
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    return { kind: "error", message };
  }
}
