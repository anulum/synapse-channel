// SPDX-License-Identifier: AGPL-3.0-or-later
// Commercial license available
// © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
// © Code 2020–2026 Miroslav Šotek. All rights reserved.
// ORCID: 0009-0009-3560-0851
// Contact: www.anulum.li | protoscience@anulum.li
// SYNAPSE_CHANNEL — local, contextual cockpit guide model

import type { MessageKey } from "./i18n";
import type { InspectorTab } from "./workspace";

export interface GuideTopic {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
  readonly body: string;
}

type Translator = (
  key: MessageKey,
  values?: Readonly<Record<string, string | number>>,
) => string;

const GENERAL_TOPIC_KEYS = [
  "orientation",
  "limits",
  "actions",
  "shortcuts",
  "troubleshooting",
] as const;

function generalTopic(t: Translator, id: (typeof GENERAL_TOPIC_KEYS)[number]): GuideTopic {
  return {
    id,
    title: t(`guide.topic.${id}.title`),
    summary: t(`guide.topic.${id}.summary`),
    body: t(`guide.topic.${id}.body`),
  };
}

function panelTopic(t: Translator, panel: InspectorTab): GuideTopic {
  const body = t(`guide.panel.${panel}.body`);
  const title = t(`guide.panel.${panel}.title`);
  return {
    id: `panel-${panel}`,
    title,
    summary: t("guide.contextFor", { panel: title }),
    body,
  };
}

/** Context comes first; the stable general reference follows. */
export function guideTopics(t: Translator, activePanel: InspectorTab): readonly GuideTopic[] {
  return [panelTopic(t, activePanel), ...GENERAL_TOPIC_KEYS.map((id) => generalTopic(t, id))];
}

/** Search remains inside the already-loaded catalogue; no query is transmitted. */
export function filterGuideTopics(
  topics: readonly GuideTopic[],
  query: string,
  locale: string,
): readonly GuideTopic[] {
  const needle = query.trim().toLocaleLowerCase(locale);
  if (needle === "") return topics;
  return topics.filter((topic) =>
    `${topic.title} ${topic.summary} ${topic.body}`.toLocaleLowerCase(locale).includes(needle),
  );
}
