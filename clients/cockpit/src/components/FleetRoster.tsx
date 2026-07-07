// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the fleet roster panel: who is present and what they hold

import { memo } from "react";

import type { RosterEntry, RowStatus } from "../lib/roster";

/** Glyph per status — redundant with colour and row order, never colour alone. */
const STATUS_GLYPH: Record<RowStatus, string> = {
  conflict: "▲",
  stale: "!",
  holding: "●",
  idle: "·",
};

/** Short status word for the row's accessible label and tooltip. */
const STATUS_WORD: Record<RowStatus, string> = {
  conflict: "branch conflict",
  stale: "stale lease",
  holding: "holding claims",
  idle: "idle",
};

/** How many held paths a row shows before collapsing the tail into a count. */
const PATHS_SHOWN = 3;

function lastSegment(name: string): string {
  const slash = name.lastIndexOf("/");
  return slash === -1 ? name : name.slice(slash + 1);
}

function project(name: string): string {
  const slash = name.indexOf("/");
  return slash === -1 ? "" : name.slice(0, slash);
}

interface RosterRowProps {
  readonly entry: RosterEntry;
  /** Opens the agent detail drawer. */
  readonly onInspect?: ((name: string) => void) | undefined;
}

function RosterRow({ entry, onInspect }: RosterRowProps): JSX.Element {
  const held = entry.paths.length;
  const shown = entry.paths.slice(0, PATHS_SHOWN);
  const overflow = held - shown.length;
  const claimCount = entry.activeClaims.length + entry.staleClaims.length;

  return (
    <li
      className={`roster-row roster-row--${entry.status}${entry.online ? "" : " roster-row--offline"}${onInspect !== undefined ? " roster-row--link" : ""}`}
      onClick={onInspect === undefined ? undefined : () => onInspect(entry.agent)}
      title={`${entry.agent} — ${STATUS_WORD[entry.status]}${entry.online ? "" : " (offline)"}`}
    >
      <span className="roster-row__glyph" aria-hidden="true">
        {STATUS_GLYPH[entry.status]}
      </span>
      <span className="roster-row__id">
        <span className="roster-row__name">{lastSegment(entry.agent)}</span>
        {project(entry.agent) !== "" && (
          <span className="roster-row__project">{project(entry.agent)}</span>
        )}
      </span>
      <span className="roster-row__meta">
        {claimCount > 0 ? (
          <span className="roster-row__count">{`${claimCount} claim${claimCount === 1 ? "" : "s"}`}</span>
        ) : (
          <span className="roster-row__count roster-row__count--none">no claims</span>
        )}
        {entry.roles.map((role) => (
          <span
            key={role}
            className="roster-row__tag roster-row__tag--role"
            title={`answers to the role '${role}'`}
          >
            {role}
          </span>
        ))}
        {entry.wakerMissing && (
          <span className="roster-row__tag roster-row__tag--warn">waker missing</span>
        )}
        {!entry.online && <span className="roster-row__tag roster-row__tag--warn">offline</span>}
      </span>
      {held > 0 && (
        <ul className="roster-row__paths">
          {shown.map((path) => (
            <li key={path} className="roster-row__path" title={path}>
              {path}
            </li>
          ))}
          {overflow > 0 && <li className="roster-row__path roster-row__path--more">{`+${overflow} more`}</li>}
        </ul>
      )}
    </li>
  );
}

interface FleetRosterProps {
  readonly roster: readonly RosterEntry[];
  /** Number of `-rx` waiters folded out of the primary rows, if any. */
  readonly waiters: number;
  /** Opens the agent detail drawer. */
  readonly onInspect?: ((name: string) => void) | undefined;
}

function FleetRosterView({ roster, waiters, onInspect }: FleetRosterProps): JSX.Element {
  const live = roster.filter((entry) => entry.online).length;

  return (
    <section className="panel" aria-label="Fleet roster">
      <div className="panel__head">
        <span>Fleet roster</span>
        <span className="panel__count">{live}</span>
        {waiters > 0 && <span className="panel__sub">{`${waiters} waiting`}</span>}
      </div>
      <div className="panel__body" tabIndex={0}>
        {roster.length === 0 ? (
          <p className="panel__placeholder">No agents present. Waiting for the hub.</p>
        ) : (
          <ul className="roster">
            {roster.map((entry) => (
              <RosterRow key={entry.agent} entry={entry} onInspect={onInspect} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Memoised: re-renders only when its own data changes, not on the 1 s clock. */
export const FleetRoster = memo(FleetRosterView);
