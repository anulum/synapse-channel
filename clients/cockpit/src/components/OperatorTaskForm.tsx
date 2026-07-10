// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — governed task declaration and update form

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  declareOperatorTask,
  parseDependencyIds,
  updateOperatorTask,
  type OperatorTaskResult,
} from "../lib/operatorActions";

export type OperatorTaskMode = "declare" | "update";

interface OperatorTaskFormProps {
  readonly mode: OperatorTaskMode;
  readonly taskIds: readonly string[];
  readonly onBack: () => void;
}

function resultLine(result: OperatorTaskResult): string {
  if (result.kind === "accepted") {
    return result.detail === "" ? `accepted — ${result.status}` : `accepted — ${result.detail}`;
  }
  if (result.kind === "denied") return `denied: ${result.detail}`;
  if (result.kind === "rejected") return `rejected: ${result.detail}`;
  if (result.kind === "unreachable") return `unreachable: ${result.detail}`;
  if (result.kind === "not-armed") return "operator write-path not armed (--operator)";
  if (result.kind === "unauthorised") return "dashboard bearer refused; unlock again";
  if (result.kind === "rate-limited") return `rate limited: ${result.detail}`;
  if (result.kind === "invalid") return result.message;
  return `failed: ${result.message}`;
}

/** Focused form for one hub-governed task declaration or update. */
export function OperatorTaskForm({
  mode,
  taskIds,
  onBack,
}: OperatorTaskFormProps): JSX.Element {
  const [taskId, setTaskId] = useState("");
  const [title, setTitle] = useState("");
  const [dependencies, setDependencies] = useState("");
  const [status, setStatus] = useState("");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const idRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => idRef.current?.focus(), []);

  const submit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    setPending(true);
    const request =
      mode === "declare"
        ? declareOperatorTask({
            id: taskId,
            title,
            dependsOn: parseDependencyIds(dependencies),
          })
        : updateOperatorTask({
            id: taskId,
            ...(status.trim() === "" ? {} : { status }),
            ...(note.trim() === "" ? {} : { note }),
          });
    void request.then((result) => {
      setOutcome(resultLine(result));
      setPending(false);
    });
  };

  const heading = mode === "declare" ? "declare task" : "update task";
  return (
    <form className="palette__compose" aria-label={`Operator ${heading}`} onSubmit={submit}>
      <span className="palette__compose-head">
        operator {heading} — the hub validates, authorises, and records the action
      </span>
      <label htmlFor="operator-task-id">Task id</label>
      <input
        ref={idRef}
        id="operator-task-id"
        className="palette__input"
        value={taskId}
        list={mode === "update" ? "operator-task-ids" : undefined}
        autoComplete="off"
        onChange={(event) => {
          setTaskId(event.target.value);
          setOutcome(null);
        }}
      />
      {mode === "update" && (
        <datalist id="operator-task-ids">
          {[...new Set(taskIds)].map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
      )}
      {mode === "declare" ? (
        <>
          <label htmlFor="operator-task-title">Task title</label>
          <input
            id="operator-task-title"
            className="palette__input"
            value={title}
            onChange={(event) => {
              setTitle(event.target.value);
              setOutcome(null);
            }}
          />
          <label htmlFor="operator-task-dependencies">Dependencies (comma separated)</label>
          <input
            id="operator-task-dependencies"
            className="palette__input"
            value={dependencies}
            onChange={(event) => {
              setDependencies(event.target.value);
              setOutcome(null);
            }}
          />
        </>
      ) : (
        <>
          <label htmlFor="operator-task-status">Task status (optional)</label>
          <input
            id="operator-task-status"
            className="palette__input"
            value={status}
            list="operator-task-statuses"
            onChange={(event) => {
              setStatus(event.target.value);
              setOutcome(null);
            }}
          />
          <datalist id="operator-task-statuses">
            {["open", "in_progress", "blocked", "done", "cancelled"].map((value) => (
              <option key={value} value={value} />
            ))}
          </datalist>
          <label htmlFor="operator-task-note">Progress note (optional)</label>
          <input
            id="operator-task-note"
            className="palette__input"
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
              setOutcome(null);
            }}
          />
        </>
      )}
      <div className="palette__compose-row">
        <button type="submit" className="log-controls__toggle" disabled={pending}>
          {pending ? "submitting…" : heading}
        </button>
        <button type="button" className="panel__clear" onClick={onBack}>
          back
        </button>
        {outcome !== null && (
          <span className="palette__outcome" role="status">
            {outcome}
          </span>
        )}
      </div>
    </form>
  );
}
