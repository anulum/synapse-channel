// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the causality inspector: recorded causes and effects on demand

import type { JSX } from "react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  clusterByHub,
  fetchTrace,
  type CausalityNode,
  type CausalityTrace,
  type TraceResult,
} from "../lib/causality";

/** A subject pushed in from another panel; the nonce re-fires equal subjects. */
export interface CausalityPrefill {
  readonly subject: string;
  readonly nonce: number;
}

function timeOf(ts: number | null): string {
  if (ts === null) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

interface NodeLineProps {
  readonly node: CausalityNode;
}

function NodeLine({ node }: NodeLineProps): JSX.Element {
  return (
    <span className="causality-node">
      <span className="causality-node__seq">seq {node.seq}</span>
      <span className="causality-node__time">{timeOf(node.ts)}</span>
      <span className="causality-node__kind">{node.kind}</span>
      <span className="causality-node__who" title={`${node.owner} ${node.taskId}`}>
        {node.owner === "" ? "—" : node.owner}
        {node.taskId !== "" && ` · ${node.taskId}`}
      </span>
    </span>
  );
}

type InspectorStatus = "idle" | "loading" | "loaded" | "absent" | "error";

interface CausalityViewProps {
  /** A subject handed over from the signal log (master-detail hop), if any. */
  readonly prefill?: CausalityPrefill | null | undefined;
}

export function CausalityView({ prefill = null }: CausalityViewProps): JSX.Element {
  const [subject, setSubject] = useState("");
  const [direction, setDirection] = useState<"causes" | "effects">("causes");
  const [status, setStatus] = useState<InspectorStatus>("idle");
  const [trace, setTrace] = useState<CausalityTrace | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runTrace = useCallback(
    async (querySubject: string, queryDirection: "causes" | "effects"): Promise<void> => {
      if (querySubject.trim() === "") return;
      setStatus("loading");
      const result: TraceResult = await fetchTrace({
        subject: querySubject,
        direction: queryDirection,
      });
      if (result.kind === "loaded") {
        setTrace(result.trace);
        setStatus("loaded");
        setError(null);
      } else if (result.kind === "absent") {
        setStatus("absent");
      } else {
        setStatus("error");
        setError(result.message);
      }
    },
    [],
  );

  const run = useCallback(
    async (event: FormEvent): Promise<void> => {
      event.preventDefault();
      await runTrace(subject, direction);
    },
    [runTrace, subject, direction],
  );

  // A log-row hop lands here: adopt the subject and trace it immediately —
  // it is a read-only query, and the operator asked by clicking the row.
  useEffect(() => {
    if (prefill === null || prefill === undefined) return;
    setSubject(prefill.subject);
    void runTrace(prefill.subject, "causes");
  }, [prefill, runTrace]);

  // The transitive closure of a busy hub runs to hundreds of events; show the
  // most recent slice and say how much of the record lies beyond it.
  const TRANSITIVE_SHOWN = 60;
  const transitive = trace === null ? [] : trace.transitive.slice(-TRANSITIVE_SHOWN);
  const transitiveOverflow = trace === null ? 0 : trace.transitive.length - transitive.length;
  const clusters = clusterByHub(transitive);
  const federated = clusters.some((cluster) => cluster.hubId !== "");

  return (
    <section className="panel" aria-label="Causality inspector">
      <div className="panel__head">
        <span>Causality</span>
        <span className="panel__sub">recorded relations only</span>
      </div>
      <div className="panel__body">
        <form className="causality-form" onSubmit={run}>
          <input
            className="causality-form__subject"
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            placeholder="hub event seq or task id"
            aria-label="Hub event seq or task id"
          />
          <select
            className="causality-form__direction"
            value={direction}
            onChange={(event) => setDirection(event.target.value === "effects" ? "effects" : "causes")}
            aria-label="Trace direction"
          >
            <option value="causes">causes</option>
            <option value="effects">effects</option>
          </select>
          <button className="causality-form__go" type="submit" disabled={status === "loading"}>
            {status === "loading" ? "tracing…" : "trace"}
          </button>
        </form>

        {status === "idle" && (
          <p className="panel__placeholder">
            Enter a hub event seq (reliability findings carry them) or a task id.
          </p>
        )}
        {status === "absent" && (
          <p className="panel__placeholder">
            This hub's dashboard does not serve causality traces yet
            (no /causality.json). The inspector activates as soon as it does.
          </p>
        )}
        {status === "error" && (
          <p className="panel__placeholder">{`Trace failed: ${error ?? "unknown"}`}</p>
        )}
        {status === "loaded" && trace !== null && (
          <div className="causality-trace">
            {!trace.present ? (
              <p className="panel__placeholder">
                {trace.note !== ""
                  ? `Event ${trace.seq}: ${trace.note}.`
                  : `Event ${trace.seq} is not in the coordination causal graph.`}
              </p>
            ) : (
              <>
                {trace.node !== null && (
                  <div className="causality-focus">
                    <span className="causality-focus__label">{trace.direction} of</span>
                    <NodeLine node={trace.node} />
                  </div>
                )}
                {trace.direct.length === 0 ? (
                  <p className="panel__placeholder">No recorded relations.</p>
                ) : (
                  <ul className="causality-edges">
                    {trace.direct.map((edge) => (
                      <li key={`${edge.src}:${edge.dst}`} className="causality-edge">
                        <span className="causality-edge__relation">{edge.relation}</span>
                        <NodeLine node={edge.node} />
                        {edge.detail !== "" && (
                          <span className="causality-edge__detail">{edge.detail}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {trace.transitive.length > 0 && (
                  <div className="causality-transitive">
                    <span className="causality-transitive__head">
                      {transitiveOverflow > 0
                        ? `transitive (last ${transitive.length} of ${trace.transitive.length})`
                        : `transitive (${trace.transitive.length})`}
                    </span>
                    {clusters.map((cluster) => (
                      <div key={cluster.hubId} className="causality-cluster">
                        {federated && (
                          <span className="causality-cluster__hub">
                            {cluster.hubId === "" ? "local hub" : cluster.hubId}
                          </span>
                        )}
                        <ul className="causality-cluster__nodes">
                          {cluster.nodes.map((node) => (
                            <li key={`${node.hubId}:${node.seq}`}>
                              <NodeLine node={node} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
