// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the approval view of one operation: its intent, both digests, the request, the command

import type { JSX } from "react";
import { useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import {
  approvalView,
  decideCommands,
  type IntentField,
  type OperationChain,
} from "../lib/operations";

function IntentFields({ fields }: { readonly fields: readonly IntentField[] }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <dl className="op-intent" aria-label={t("operations.approval.intent")}>
      {fields.map((item) => (
        <div key={item.field} className="op-intent__field">
          <dt>{item.field}</dt>
          <dd><code>{item.canonical}</code></dd>
        </div>
      ))}
    </dl>
  );
}

function Digests({ chain, equal }: { readonly chain: OperationChain; readonly equal: boolean }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <div className="op-digests">
      <p className="op-digests__row">
        <span>{t("operations.approval.recorded")}</span> <code>{chain.intentSha256}</code>
      </p>
      <p className="op-digests__row">
        <span>{t("operations.approval.recomputed")}</span> <code>{chain.recomputedSha256}</code>
      </p>
      {equal ? (
        <p className="op-digests__verdict">{t("operations.approval.equal")}</p>
      ) : (
        <p className="op-digests__verdict op-approval__refusal" role="alert">{t("operations.approval.refused")}</p>
      )}
    </div>
  );
}

function CommandLine({ command }: { readonly command: string }): JSX.Element {
  const { t } = useCockpitI18n();
  const [copied, setCopied] = useState<boolean | null>(null);
  const copy = async (): Promise<void> => {
    try {
      if (navigator.clipboard === undefined) throw new Error("no clipboard");
      await navigator.clipboard.writeText(command);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <li className="op-command">
      <code className="op-command__text">{command}</code>
      <button type="button" className="op-button" onClick={() => void copy()} aria-label={t("operations.approval.copyLabel", { command })}>
        {t("operations.approval.copy")}
      </button>
      {copied !== null && (
        <span className="op-command__status" role="status">
          {copied ? t("operations.approval.copied") : t("operations.approval.copyFailed")}
        </span>
      )}
    </li>
  );
}

function Decide({ digest, reason }: { readonly digest: string; readonly reason: string | null }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <>
      <figure className="op-words">
        <figcaption>{t("operations.approval.words")}</figcaption>
        <p className="op-words__text">
          {reason === null ? t("operations.approval.noRequest") : reason === "" ? t("operations.approval.noWords") : reason}
        </p>
      </figure>
      <p className="op-note">{t("operations.approval.who")}</p>
      <ul className="op-commands">
        {decideCommands(digest).map((command) => <CommandLine key={command} command={command} />)}
      </ul>
      <p className="op-note">{t("operations.approval.notHere")}</p>
    </>
  );
}

/**
 * For an operation that awaits approval: the intent the declaration kept, field
 * by field in canonical order; the recorded digest beside the one the dashboard
 * recomputed; and, only when they are equal, the requester's own words and the
 * command that decides. The cockpit itself never approves.
 */
export function OperationApproval({ chain }: { readonly chain: OperationChain }): JSX.Element | null {
  const { t } = useCockpitI18n();
  const view = approvalView(chain);
  if (view === null) return null;
  return (
    <section className="op-approval" aria-label={t("operations.approval.title")}>
      <p className="op-approval__head">{t("operations.approval.title")}</p>
      {view.kind === "no intent" ? (
        <p className="op-approval__refusal" role="alert">{t("operations.approval.noIntent")}</p>
      ) : (
        <>
          <IntentFields fields={chain.intentFields} />
          <Digests chain={chain} equal={view.kind === "decidable"} />
          {view.kind === "decidable" && <Decide digest={view.digest} reason={view.reason} />}
        </>
      )}
    </section>
  );
}
