// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE CHANNEL — guided local-first incident workspace

import type { JSX } from "react";
import { useEffect, useMemo, useState } from "react";

import {
  buildIncidentExport,
  clearIncidentDraft,
  createIncidentDraft,
  evidenceFromSelection,
  incidentEvidenceKey,
  incidentExportFilename,
  INCIDENT_HYPOTHESIS_LIMIT,
  INCIDENT_NOTES_LIMIT,
  INCIDENT_TITLE_LIMIT,
  readIncidentDraft,
  withIncidentEvidence,
  withIncidentText,
  writeIncidentDraft,
  type IncidentDraft,
} from "../lib/incidentWorkspace";
import { selectionLabel } from "../lib/selection";
import type {
  CockpitSelection,
  IncidentStep,
  ReplayState,
} from "../lib/workspace";

const INCIDENT_STEPS: readonly IncidentStep[] = ["scope", "evidence", "notes"];

function incidentId(nowMs: number): string {
  const randomId = globalThis.crypto?.randomUUID?.();
  return randomId === undefined ? `local-${nowMs.toString(36)}` : randomId;
}

function newDraft(nowMs = Date.now()): IncidentDraft {
  return createIncidentDraft(nowMs, incidentId(nowMs));
}

function replayLabel(replay: ReplayState): string {
  if (replay.mode === "history") return `history at sequence ${replay.at}`;
  if (replay.mode === "compare") return `compare ${replay.a} to ${replay.b}`;
  return "live view";
}

function downloadIncident(
  draft: IncidentDraft,
  replay: ReplayState,
  hubVersion: string,
  configEpoch: string,
): void {
  const nowMs = Date.now();
  const document_ = buildIncidentExport(draft, replay, hubVersion, configEpoch, nowMs);
  const blob = new Blob([JSON.stringify(document_, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = incidentExportFilename(draft, nowMs);
  anchor.click();
  URL.revokeObjectURL(url);
}

interface IncidentWorkspaceProps {
  readonly step: IncidentStep;
  readonly onStepChange: (step: IncidentStep) => void;
  readonly selection: CockpitSelection | null;
  readonly replay: ReplayState;
  readonly storageKey: string;
  readonly hubVersion: string;
  readonly configEpoch: string;
  readonly onOpenEvidence: (selection: CockpitSelection) => void;
}

/** Guide an operator from scope to explicit evidence and a bounded local export. */
export function IncidentWorkspace({
  step,
  onStepChange,
  selection,
  replay,
  storageKey,
  hubVersion,
  configEpoch,
  onOpenEvidence,
}: IncidentWorkspaceProps): JSX.Element {
  const [draft, setDraft] = useState<IncidentDraft>(
    () => readIncidentDraft(localStorage, storageKey) ?? newDraft(),
  );
  const [saveState, setSaveState] = useState<"saved" | "unavailable">("saved");
  const [confirmReset, setConfirmReset] = useState(false);

  useEffect(() => {
    setSaveState(writeIncidentDraft(localStorage, storageKey, draft) ? "saved" : "unavailable");
  }, [draft, storageKey]);

  const selectedKey = selection === null ? null : incidentEvidenceKey(selection);
  const selectionAlreadyAdded = selectedKey !== null && draft.evidence.some(
    (item) => item.key === selectedKey,
  );
  const scopeReady = draft.title.trim() !== "";
  const evidenceReady = draft.evidence.length > 0;
  const stageNumber = INCIDENT_STEPS.indexOf(step) + 1;
  const selectedLabel = useMemo(
    () => selection === null ? "No current selection" : selectionLabel(selection),
    [selection],
  );

  const updateText = (field: "title" | "hypothesis" | "notes", value: string): void => {
    setDraft((current) => withIncidentText(current, field, value, Date.now()));
  };

  const addSelection = (selected: CockpitSelection): void => {
    const nowMs = Date.now();
    setDraft((current) => withIncidentEvidence(
      current,
      evidenceFromSelection(selected, replay, nowMs),
      null,
      nowMs,
    ));
  };

  const resetDraft = (): void => {
    clearIncidentDraft(localStorage, storageKey);
    setDraft(newDraft());
    setConfirmReset(false);
    onStepChange("scope");
  };

  return (
    <section className="incident" aria-label="Guided incident workspace">
      <header className="incident__head">
        <div>
          <span className="incident__eyebrow">local investigation</span>
          <h2>{draft.title.trim() === "" ? "Untitled incident" : draft.title}</h2>
        </div>
        <div className="incident__status" aria-live="polite">
          <span>{`step ${stageNumber} of ${INCIDENT_STEPS.length}`}</span>
          <span className={`incident__save incident__save--${saveState}`}>
            {saveState === "saved" ? "saved in this browser" : "browser storage unavailable"}
          </span>
        </div>
      </header>

      <p className="incident__boundary">
        This is a principal-scoped local draft. It does not write to the hub, acknowledge a peer,
        authorise remediation, or create a signed audit record.
      </p>

      <nav className="incident__steps" aria-label="Incident workflow">
        {INCIDENT_STEPS.map((candidate, index) => {
          const ready = candidate === "scope" ? scopeReady : candidate === "evidence" ? evidenceReady : false;
          return (
            <button
              key={candidate}
              type="button"
              className={`incident-step${candidate === step ? " incident-step--active" : ""}`}
              aria-label={candidate}
              aria-current={candidate === step ? "step" : undefined}
              onClick={() => onStepChange(candidate)}
            >
              <span>{index + 1}</span>
              <strong>{candidate}</strong>
              <i>{ready ? "ready" : candidate === "notes" ? "optional" : "needed"}</i>
            </button>
          );
        })}
      </nav>

      {step === "scope" ? (
        <div className="incident__pane incident__pane--scope">
          <div className="incident__field">
            <label htmlFor="incident-title">Incident title</label>
            <input
              id="incident-title"
              value={draft.title}
              maxLength={INCIDENT_TITLE_LIMIT}
              onChange={(event) => updateText("title", event.target.value)}
              aria-describedby="incident-title-help"
            />
            <small id="incident-title-help">Name the observable problem, not an unverified cause.</small>
          </div>
          <div className="incident__field">
            <label htmlFor="incident-hypothesis">Working hypothesis</label>
            <textarea
              id="incident-hypothesis"
              value={draft.hypothesis}
              maxLength={INCIDENT_HYPOTHESIS_LIMIT}
              onChange={(event) => updateText("hypothesis", event.target.value)}
              aria-describedby="incident-hypothesis-help"
            />
            <small id="incident-hypothesis-help">
              Keep this explicitly provisional; evidence references remain separate.
            </small>
          </div>
          <div className="incident__next">
            <span>{scopeReady ? "Scope named" : "A title is required before review"}</span>
            <button type="button" onClick={() => onStepChange("evidence")} disabled={!scopeReady}>
              continue to evidence
            </button>
          </div>
        </div>
      ) : step === "evidence" ? (
        <div className="incident__pane">
          <div className="incident-context">
            <div>
              <span>current selection</span>
              <strong>{selectedLabel}</strong>
              <small>{replayLabel(replay)}</small>
            </div>
            <button
              type="button"
              onClick={selection === null ? undefined : () => addSelection(selection)}
              disabled={selection === null || selectionAlreadyAdded}
            >
              {selectionAlreadyAdded ? "already in cart" : "add current selection"}
            </button>
          </div>
          {selection === null && (
            <p className="incident__empty">
              Select an event, task, route, agent, or project elsewhere in the cockpit, then return here.
            </p>
          )}
          <div className="incident-cart">
            <div className="incident-cart__head">
              <h3>Evidence cart</h3>
              <span>{`${draft.evidence.length} explicit reference${draft.evidence.length === 1 ? "" : "s"}`}</span>
            </div>
            {draft.evidence.length === 0 ? (
              <p className="incident__empty">No evidence selected. The workspace never adds related rows automatically.</p>
            ) : (
              <ol className="incident-cart__list">
                {draft.evidence.map((item) => (
                  <li key={item.key} className="incident-evidence">
                    <div>
                      <span>{item.selection.kind}</span>
                      <strong>{item.label}</strong>
                      <small>{`${replayLabel(item.replay)} · added ${new Date(item.addedAt).toLocaleString()}`}</small>
                    </div>
                    <div className="incident-evidence__actions">
                      <button type="button" onClick={() => onOpenEvidence(item.selection)}>open</button>
                      <button
                        type="button"
                        onClick={() => setDraft((current) => withIncidentEvidence(
                          current,
                          null,
                          item.key,
                          Date.now(),
                        ))}
                        aria-label={`Remove ${item.selection.kind} ${item.label} from evidence`}
                      >
                        remove
                      </button>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <div className="incident__next">
            <button type="button" className="incident__back" onClick={() => onStepChange("scope")}>back to scope</button>
            <button type="button" onClick={() => onStepChange("notes")} disabled={!evidenceReady}>
              continue to notes and export
            </button>
          </div>
        </div>
      ) : (
        <div className="incident__pane incident__pane--notes">
          <div className="incident__field">
            <label htmlFor="incident-notes">Operator notes</label>
            <textarea
              id="incident-notes"
              value={draft.notes}
              maxLength={INCIDENT_NOTES_LIMIT}
              onChange={(event) => updateText("notes", event.target.value)}
              aria-describedby="incident-notes-help"
            />
            <small id="incident-notes-help">
              Notes are local commentary. Put verifiable facts in the evidence cart as exact references.
            </small>
          </div>
          <dl className="incident-review">
            <div><dt>scope</dt><dd>{scopeReady ? draft.title : "missing title"}</dd></div>
            <div>
              <dt>evidence</dt>
              <dd>{`${draft.evidence.length} explicit reference${draft.evidence.length === 1 ? "" : "s"}`}</dd>
            </div>
            <div><dt>context</dt><dd>{replayLabel(replay)}</dd></div>
            <div><dt>authority</dt><dd>local draft only</dd></div>
          </dl>
          <div className="incident__export">
            <p>
              JSON export includes the draft, exact typed references, replay positions, hub version,
              configuration epoch, and the non-authoritative evidence boundary.
            </p>
            <button
              type="button"
              onClick={() => downloadIncident(draft, replay, hubVersion, configEpoch)}
              disabled={!scopeReady || !evidenceReady}
            >
              export incident JSON
            </button>
          </div>
          <div className="incident__next">
            <button type="button" className="incident__back" onClick={() => onStepChange("evidence")}>back to evidence</button>
            {confirmReset ? (
              <div className="incident__confirm" role="group" aria-label="Confirm new incident">
                <span>Replace this local draft?</span>
                <button type="button" onClick={resetDraft}>confirm new incident</button>
                <button type="button" onClick={() => setConfirmReset(false)}>cancel</button>
              </div>
            ) : (
              <button type="button" className="incident__reset" onClick={() => setConfirmReset(true)}>
                start new incident
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
