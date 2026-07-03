// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the reliability EVIDENCE panel: the record, never a score

import { memo } from "react";

import { orderOwners, type ReliabilityFinding, type ReliabilityState } from "../lib/reliability";

/** Glyph per finding kind — redundant with colour, never colour alone. */
const KIND_GLYPH: Record<string, string> = {
  stale_claim: "!",
  conflict_pair: "▲",
  declared_failed_check: "✕",
  broken_handoff_candidate: "↯",
};

/** How many findings show before the tail collapses into a count. */
const FINDINGS_SHOWN = 40;

function timeOf(ts: number | null): string {
  if (ts === null) return "—";
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function findingClass(kind: string): string {
  if (kind === "conflict_pair" || kind === "declared_failed_check") {
    return "evidence-row evidence-row--critical";
  }
  return "evidence-row evidence-row--warn";
}

interface FindingRowProps {
  readonly finding: ReliabilityFinding;
}

function FindingRow({ finding }: FindingRowProps): JSX.Element {
  return (
    <li className={findingClass(finding.kind)} title={JSON.stringify(finding.evidence)}>
      <span className="evidence-row__glyph" aria-hidden="true">
        {KIND_GLYPH[finding.kind] ?? "·"}
      </span>
      <span className="evidence-row__body">
        <span className="evidence-row__meta">
          <span className="evidence-row__seq">seq {finding.seq}</span>
          <span className="evidence-row__time">{timeOf(finding.ts)}</span>
          <span className="evidence-row__kind">{finding.kind}</span>
        </span>
        <span className="evidence-row__detail">{finding.detail}</span>
        <span className="evidence-row__who">
          {finding.owner}
          {finding.taskId !== "" && ` · ${finding.taskId}`}
        </span>
      </span>
    </li>
  );
}

interface ReliabilityPanelProps {
  /** The reliability feed's current state, including how it was obtained. */
  readonly state: ReliabilityState;
}

function ReliabilityPanelView({ state }: ReliabilityPanelProps): JSX.Element {
  const report = state.data;
  const owners = report === null ? [] : orderOwners(report.owners);
  const findings = report?.findings ?? [];
  const shown = findings.slice(0, FINDINGS_SHOWN);
  const overflow = findings.length - shown.length;

  return (
    <section className="panel" aria-label="Reliability evidence">
      <div className="panel__head">
        <span>Reliability</span>
        {report !== null && <span className="panel__count">{findings.length}</span>}
        <span className="panel__sub">{report?.note !== "" && report !== null ? report.note : "evidence"}</span>
      </div>
      <div className="panel__body">
        {state.status === "absent" ? (
          <p className="panel__placeholder">
            This hub's dashboard does not serve reliability evidence yet
            (no /reliability.json). The panel activates as soon as it does.
          </p>
        ) : state.status === "error" && report === null ? (
          <p className="panel__placeholder">{`Reliability feed failed: ${state.error ?? "unknown"}`}</p>
        ) : report === null ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : (
          <>
            {owners.length > 0 && (
              <table className="evidence-owners">
                <thead>
                  <tr>
                    <th scope="col">agent</th>
                    <th scope="col" title="stale claims recorded">
                      stale
                    </th>
                    <th scope="col" title="conflict pairs recorded">
                      confl
                    </th>
                    <th scope="col" title="declared failed checks recorded">
                      failed
                    </th>
                    <th scope="col" title="broken handoff candidates recorded">
                      handoff
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {owners.map((owner) => (
                    <tr key={owner.owner}>
                      <td className="evidence-owners__name" title={owner.owner}>
                        {owner.owner}
                      </td>
                      <td>{owner.staleClaims}</td>
                      <td>{owner.conflictPairs}</td>
                      <td>{owner.declaredFailedChecks}</td>
                      <td>{owner.brokenHandoffs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {findings.length === 0 ? (
              <p className="panel__placeholder">No reliability findings recorded.</p>
            ) : (
              <ul className="evidence">
                {shown.map((finding, index) => (
                  // A conflict_pair anchors BOTH sides to one seq, so the key
                  // needs the position to stay unique.
                  <FindingRow key={`${finding.seq}:${finding.kind}:${index}`} finding={finding} />
                ))}
                {overflow > 0 && (
                  <li className="evidence-row evidence-row--more">{`+${overflow} more recorded`}</li>
                )}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const ReliabilityPanel = memo(ReliabilityPanelView);
