// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — operator attention queue over explicit fleet evidence

import type { JSX } from "react";
import { useMemo, useState } from "react";

import { mergeStoredAttention, type AttentionAction, type AttentionItem, type AttentionLevel } from "../lib/attention";
import type { AttentionFeedState } from "../lib/attentionFeed";

type AttentionFilter = "all" | AttentionLevel;

interface AttentionQueueProps {
  readonly items: readonly AttentionItem[];
  readonly stored?: AttentionFeedState | undefined;
  readonly connected: boolean;
  readonly onInspectAgent?: ((identity: string) => void) | undefined;
  readonly onInspectTask?: ((taskId: string) => void) | undefined;
  readonly onInspectRoute?: ((source: string, target: string) => void) | undefined;
}

const SHOWN_LIMIT = 50;

function actionLabel(action: AttentionAction): string {
  if (action.kind === "agent") return "inspect agent";
  if (action.kind === "task") return "open task";
  return "inspect route";
}

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function AttentionQueue({
  items,
  stored,
  connected,
  onInspectAgent,
  onInspectTask,
  onInspectRoute,
}: AttentionQueueProps): JSX.Element {
  const [filter, setFilter] = useState<AttentionFilter>("all");
  const combined = useMemo(
    () => mergeStoredAttention(items, stored?.status === "live" || stored?.status === "error" ? stored.data : null),
    [items, stored],
  );
  const counts = useMemo(
    () => ({
      critical: combined.filter((item) => item.level === "critical").length,
      warning: combined.filter((item) => item.level === "warning").length,
      info: combined.filter((item) => item.level === "info").length,
    }),
    [combined],
  );
  const filtered = filter === "all" ? combined : combined.filter((item) => item.level === filter);
  const shown = filtered.slice(0, SHOWN_LIMIT);

  const runAction = (action: AttentionAction): void => {
    if (action.kind === "agent") onInspectAgent?.(action.id);
    else if (action.kind === "task") onInspectTask?.(action.id);
    else onInspectRoute?.(action.source, action.target);
  };

  return (
    <section className="panel attention" aria-label="Fleet attention queue">
      <div className="panel__head attention__head">
        <span>Attention queue</span>
        <span className="panel__sub">explicit evidence · deterministic order</span>
      </div>
      <div className="attention__toolbar" role="group" aria-label="Filter attention queue">
        {(["all", "critical", "warning", "info"] as const).map((candidate) => {
          const count = candidate === "all" ? combined.length : counts[candidate];
          return (
            <button
              key={candidate}
              type="button"
              className={`attention__filter${filter === candidate ? " attention__filter--active" : ""}`}
              aria-pressed={filter === candidate}
              onClick={() => setFilter(candidate)}
            >
              {candidate} <span>{count}</span>
            </button>
          );
        })}
      </div>
      <div className="panel__body attention__body">
        {stored?.status === "absent" && (
          <p className="panel__placeholder">Local attention observer is not configured.</p>
        )}
        {stored?.status === "error" && (
          <p className="panel__placeholder">Local attention observer is unavailable; last known alerts may be stale.</p>
        )}
        {stored?.status === "live" && stored.data?.state === "missing_observer" && (
          <p className="panel__placeholder">Local attention observer is late. No new alerts cannot be treated as silence.</p>
        )}
        {!connected && shown.length === 0 ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : shown.length === 0 ? (
          <p className="panel__placeholder">No current signals in this evidence filter.</p>
        ) : (
          <ol className="attention__list">
            {shown.map((item) => (
              <li key={item.id} className={`attention__row attention__row--${item.level}`}>
                <span className="attention__level">{item.level}</span>
                <span className="attention__evidence">
                  <strong>{item.subject}</strong>
                  <span>
                    {item.kind.replaceAll("_", " ")} · {item.evidence}
                  </span>
                </span>
                {item.observedAt !== null && (
                  <time dateTime={new Date(item.observedAt * 1000).toISOString()}>
                    {timeLabel(item.observedAt)}
                  </time>
                )}
                {item.action !== null && (
                  <button
                    type="button"
                    className="attention__action"
                    onClick={() => {
                      if (item.action !== null) runAction(item.action);
                    }}
                  >
                    {actionLabel(item.action)}
                  </button>
                )}
              </li>
            ))}
          </ol>
        )}
        {filtered.length > shown.length && (
          <p className="attention__overflow">+{filtered.length - shown.length} more signals</p>
        )}
      </div>
    </section>
  );
}
