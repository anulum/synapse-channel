// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the claims board: who holds which file scope, and how safely

import { formatCountdown, type BranchConflictView, type ClaimView } from "../lib/claims";

/** Glyph per urgency — redundant with colour and order, never colour alone. */
const URGENCY_GLYPH: Record<ClaimView["urgency"], string> = {
  conflict: "▲",
  stale: "!",
  held: "●",
};

interface ConflictBannerProps {
  readonly conflicts: readonly BranchConflictView[];
}

function ConflictBanner({ conflicts }: ConflictBannerProps): JSX.Element {
  return (
    <div className="conflict-banner" role="alert">
      <span className="conflict-banner__glyph" aria-hidden="true">
        ▲
      </span>
      <div className="conflict-banner__body">
        <span className="conflict-banner__head">
          {conflicts.length === 1
            ? "1 branch conflict"
            : `${conflicts.length} branch conflicts`}
        </span>
        <ul className="conflict-banner__list">
          {conflicts.map((conflict, index) => (
            <li key={`${conflict.ownerA}:${conflict.ownerB}:${index}`}>
              <span className="conflict-banner__pair">
                {conflict.ownerA} ({conflict.branchA}) vs {conflict.ownerB} ({conflict.branchB})
              </span>
              <span className="conflict-banner__paths">{conflict.paths.join(", ")}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

interface ClaimRowProps {
  readonly view: ClaimView;
}

function ClaimRow({ view }: ClaimRowProps): JSX.Element {
  const { claim } = view;
  const countdown = formatCountdown(view.secondsToExpiry);
  const overdue = view.secondsToExpiry !== null && view.secondsToExpiry < 0;

  return (
    <li className={`claim-row claim-row--${view.urgency}`}>
      <span className="claim-row__glyph" aria-hidden="true">
        {URGENCY_GLYPH[view.urgency]}
      </span>
      <span className="claim-row__id">
        <span className="claim-row__task">{claim.task_id}</span>
        <span className="claim-row__owner">{claim.owner}</span>
      </span>
      <span className="claim-row__meta">
        {claim.git !== null && (
          <span className="claim-row__branch" title={`branch ${claim.git.branch} on ${claim.git.base}`}>
            {claim.git.branch} → {claim.git.base}
          </span>
        )}
        {claim.stale && <span className="claim-row__tag claim-row__tag--stale">stale</span>}
        <span className={`claim-row__lease${overdue ? " claim-row__lease--overdue" : ""}`}>
          {countdown}
        </span>
      </span>
      {claim.paths.length > 0 && (
        <ul className="claim-row__paths">
          {claim.paths.map((path) => (
            <li key={path} className="claim-row__path" title={path}>
              {path}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

interface ClaimsBoardProps {
  /** Claim rows, already ranked worst-first by the claims lib. */
  readonly claims: readonly ClaimView[];
  /** The hub's advisory branch conflicts; any entry renders the loud banner. */
  readonly conflicts: readonly BranchConflictView[];
  /** Whether a snapshot has arrived at all (drives the honest empty state). */
  readonly connected: boolean;
  /** The active focus lens ("" = off), stated in the head. */
  readonly lens?: string;
}

export function ClaimsBoard({ claims, conflicts, connected, lens = "" }: ClaimsBoardProps): JSX.Element {
  const staleCount = claims.filter((view) => view.claim.stale).length;

  return (
    <section className="panel" aria-label="File-scope claims">
      <div className="panel__head">
        <span>Claims</span>
        <span className="panel__count">{claims.length}</span>
        {lens !== "" && <span className="panel__sub panel__sub--warn">{`lens: ${lens}`}</span>}
        {staleCount > 0 && <span className="panel__sub panel__sub--warn">{staleCount} stale</span>}
      </div>
      <div className="panel__body" tabIndex={0}>
        {conflicts.length > 0 && <ConflictBanner conflicts={conflicts} />}
        {!connected ? (
          <p className="panel__placeholder">Waiting for the hub.</p>
        ) : claims.length === 0 ? (
          <p className="panel__placeholder">No file scopes are held right now.</p>
        ) : (
          <ul className="claims">
            {claims.map((view) => (
              <ClaimRow key={`${view.claim.owner}:${view.claim.task_id}`} view={view} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
