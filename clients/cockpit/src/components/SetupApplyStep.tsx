// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — the setup assistant's apply step: preview, confirm, apply once, read the receipt back

import type { JSX } from "react";
import { useState } from "react";

import { useCockpitI18n } from "../context/CockpitI18n";
import {
  readBackVerdict,
  restartedProcesses,
  SETUP_APPLY_SOURCE,
  type IssuedSetupPlan,
  type SetupAnswer,
  type SetupApplySource,
  type SetupReadBack,
  type SetupReceipt,
} from "../lib/setupApply";

const KNOWN_EFFECTS = [
  "install_synapse_package",
  "select_supported_python",
  "select_supported_platform",
  "expose_synapse_entrypoint",
  "configure_coordination_identity",
  "establish_local_loopback_hub",
  "establish_identity_waiter",
] as const;
const KNOWN_DISRUPTIONS = ["configuration_change", "environment_change", "service_start", "host_migration"] as const;

type KnownEffect = (typeof KNOWN_EFFECTS)[number];
type KnownDisruption = (typeof KNOWN_DISRUPTIONS)[number];

interface Applied {
  readonly answer: SetupAnswer<SetupReceipt>;
  readonly readBack: SetupReceipt | null;
  readonly verdict: SetupReadBack;
}

function isKnownEffect(id: string): id is KnownEffect {
  return (KNOWN_EFFECTS as readonly string[]).includes(id);
}

function isKnownDisruption(id: string): id is KnownDisruption {
  return (KNOWN_DISRUPTIONS as readonly string[]).includes(id);
}

/** An effect's name in the person's language; an effect this cockpit does not know keeps its id. */
function EffectName({ id }: { readonly id: string }): JSX.Element {
  const { t } = useCockpitI18n();
  return <strong>{isKnownEffect(id) ? t(`setup.apply.effect.${id}`) : id}</strong>;
}

function PlanView({ plan }: { readonly plan: IssuedSetupPlan }): JSX.Element {
  const { t, locale } = useCockpitI18n();
  const until = new Date(plan.expiresAt * 1000).toLocaleTimeString(locale);
  const blocked = plan.effects.some((effect) => effect.disposition === "blocked");
  return (
    <section className="setup-apply-plan" aria-labelledby="setup-apply-plan-title">
      <h4 id="setup-apply-plan-title">{t("setup.apply.planTitle")}</h4>
      <p>{t("setup.apply.planMeta", { profile: plan.profile, digest: plan.planDigest.slice(0, 12), time: until })}</p>
      {plan.effects.length === 0 ? (
        <p>{t("setup.apply.nothing")}</p>
      ) : (
        <ul className="setup-apply-list">
          {plan.effects.map((effect) => (
            <li key={effect.id} className="setup-apply-row">
              <EffectName id={effect.id} />
              <span>
                {isKnownDisruption(effect.disruption) ? t(`setup.apply.disruption.${effect.disruption}`) : effect.disruption}
                {" · "}
                {effect.reversible ? t("setup.apply.reversible") : t("setup.apply.irreversible")}
              </span>
              {effect.disposition === "blocked" && <span className="setup-error">{t("setup.apply.blockedEffect")}</span>}
            </li>
          ))}
        </ul>
      )}
      {restartedProcesses(plan).map((pid) => (
        <p key={pid} className="setup-boundary">{t("setup.apply.restart", { pid })}</p>
      ))}
      {blocked && <p className="setup-error" role="alert">{t("setup.apply.blocked")}</p>}
    </section>
  );
}

function ReceiptView({ receipt }: { readonly receipt: SetupReceipt }): JSX.Element {
  const { t } = useCockpitI18n();
  return (
    <div className="setup-apply-receipt">
      <p>
        {t("setup.apply.receipt", { sequence: receipt.sequence })}
        {" · "}
        {t("setup.apply.outcomeLabel")} <code>{receipt.outcome}</code>
        {" · "}
        {t("setup.apply.reasonLabel")} <code>{receipt.reason}</code>
      </p>
      {receipt.effects.length > 0 && (
        <ul className="setup-apply-list">
          {receipt.effects.map((effect) => (
            <li key={effect.kind} className="setup-apply-row">
              <EffectName id={effect.kind} />
              <span>
                <code>{effect.before}</code> → <code>{effect.after}</code>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function AnswerView({ applied }: { readonly applied: Applied }): JSX.Element {
  const { t } = useCockpitI18n();
  const { answer, readBack, verdict } = applied;
  return (
    <section className="setup-apply-answer" aria-labelledby="setup-apply-answer-title">
      <h4 id="setup-apply-answer-title">{t("setup.apply.answerTitle")}</h4>
      {answer.kind === "ok" && <ReceiptView receipt={answer.value} />}
      {answer.kind === "refused" && <p>{t("setup.apply.refused", { detail: answer.detail })}</p>}
      {answer.kind === "failed" && <p>{t("setup.apply.failed", { detail: answer.detail })}</p>}
      <p className={`setup-status setup-status--${verdict}`} role="status">
        {t(`setup.apply.readBack.${verdict}`)}
      </p>
      {answer.kind !== "ok" && readBack !== null && <ReceiptView receipt={readBack} />}
    </section>
  );
}

/**
 * Plan the served profile, show the plan, and apply it once after the person
 * confirms it. Leaving the step forgets the plan; the dashboard lets it expire.
 */
export function SetupApplyStep({ source = SETUP_APPLY_SOURCE }: { readonly source?: SetupApplySource }): JSX.Element {
  const { t } = useCockpitI18n();
  const [plan, setPlan] = useState<IssuedSetupPlan | null>(null);
  const [planAnswer, setPlanAnswer] = useState<SetupAnswer<IssuedSetupPlan> | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [applied, setApplied] = useState<Applied | null>(null);

  const preview = async (): Promise<void> => {
    setBusy(true);
    setPlan(null);
    setApplied(null);
    setConfirmed(false);
    const answer = await source.plan();
    setPlanAnswer(answer);
    setPlan(answer.kind === "ok" ? answer.value : null);
    setBusy(false);
  };

  const apply = async (): Promise<void> => {
    if (plan === null || !confirmed) return;
    setBusy(true);
    setConfirmed(false);
    const answer = await source.apply(plan);
    const readBack = await source.readBack(plan, answer.kind === "ok" ? answer.value : null);
    setApplied({ answer, readBack, verdict: readBackVerdict(readBack) });
    setBusy(false);
  };

  const blocked = plan?.effects.some((effect) => effect.disposition === "blocked") ?? false;
  return (
    <section aria-labelledby="setup-apply-title">
      <h3 id="setup-apply-title">{t("setup.apply.title")}</h3>
      <p>{t("setup.apply.body")}</p>
      <div className="setup-apply-actions">
        <button type="button" onClick={() => void preview()} disabled={busy}>
          {plan === null ? t("setup.apply.preview") : t("setup.apply.previewAgain")}
        </button>
      </div>
      {busy && <p role="status">{t("setup.apply.busy")}</p>}
      {planAnswer?.kind === "refused" && (
        <p className="setup-error" role="alert">{t("setup.apply.refused", { detail: planAnswer.detail })}</p>
      )}
      {planAnswer?.kind === "failed" && (
        <p className="setup-error" role="alert">{t("setup.apply.failed", { detail: planAnswer.detail })}</p>
      )}
      {plan !== null && <PlanView plan={plan} />}
      {plan !== null && !blocked && (
        <>
          <div className="setup-options setup-apply-confirm">
            <label>
              <input
                type="checkbox"
                aria-label={t("setup.apply.confirm")}
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span><strong>{t("setup.apply.confirm")}</strong>{t("setup.apply.once")}</span>
            </label>
          </div>
          <div className="setup-apply-actions">
            <button type="button" className="setup-apply-go" onClick={() => void apply()} disabled={!confirmed || busy}>
              {t("setup.apply.apply")}
            </button>
          </div>
        </>
      )}
      {applied !== null && <AnswerView applied={applied} />}
    </section>
  );
}
