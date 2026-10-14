# Commercial use

SYNAPSE CHANNEL is **dual-licensed**. There is **no feature difference between the
open-source and the commercial build**: both use the public Core codebase. A
commercial licence changes the terms, not the code. Optional dependencies,
platform support and documented runtime limits still apply.

## Which licence do I need?

- **The free AGPL-3.0** permits commercial, research, internal and personal use
  subject to its applicable conditions. Section 13 requires a corresponding-source
  offer for remote users of a modified version; sections 5 and 6 govern conveying
  covered works. Internal use is not an automatic waiver of these conditions.
- **A commercial licence** provides separately agreed permission when your
  intended deployment cannot satisfy applicable AGPL conditions. The commercial
  grant identifies sections 13 and 5(d), which concern network-source offers and
  interactive-interface notices respectively, within the purchased tier's scope.

Hosted use alone does not require a commercial licence or publication of an
entire software stack. Review the actual covered or combined work, corresponding
source and distribution/network interaction against the licence; separate
independent components in an aggregate have a distinct boundary.

## Plans

The paid layers **add** permission, hosting, support, and convenience on top of the free
core — nothing is ever moved behind a paywall.

| Plan | For | What it adds |
| --- | --- | --- |
| **Community** — free (AGPL-3.0) | open source, research, personal, internal | public Core feature set; copyleft and documented runtime limits apply |
| **Organisation Licence — USD 490/yr** | one legal entity that only needs commercial permission | the copyleft exemption on its own; no hosted service, dashboard, SLA, support, or Fleet entitlement |
| **Pro — USD 19/mo or 190/yr** | one developer shipping closed-source products or private hosted services | commercial permission for one developer, the mobile app with push *(planned)*, and email support |
| **Team — USD 39/user/mo or 390/user/yr** | a company owning shared coordination workflows; minimum 3 seats | unlimited projects in one legal entity, hosted dashboard *(planned)* while operational data stays local, security-patch SLA, and onboarding |
| **FLEET Enterprise — quote** | regulated, multi-hub, or multi-organisation deployments | separately entitled private software; federation, SSO, audit exports, managed services, compliance support and SLA scope require deployment-specific agreement and qualification |

Items marked *(planned)* are not available yet; they are not part of what a plan delivers today.

SYNAPSE CHANNEL FLEET is separately entitled private software, not a hidden
commercial build of the public core.

An optional **Supporter** (name-your-price) contribution funds the research and lists you
in `BACKERS`; it grants no extra rights, because the free core already holds none back.

[![View plans and buy a commercial licence](https://img.shields.io/badge/View_plans_%26_contact-commercial-0a7d3c?style=for-the-badge)](https://anulum.github.io/synapse-channel/commercial/)

Plans, current prices, and purchase contact are on
[the public commercial information page](https://anulum.github.io/synapse-channel/commercial/). For enterprise, OEM, academic, non-profit, managed-hosting, or co-ownership
terms, write to [protoscience@anulum.li](mailto:protoscience@anulum.li).

The full commercial terms are in
[`COMMERCIAL-LICENSE.md`](https://github.com/anulum/synapse-channel/blob/main/COMMERCIAL-LICENSE.md);
the open-source licence is
[AGPL-3.0-or-later](https://github.com/anulum/synapse-channel/blob/main/LICENSE).

## Evaluation path

Use this path before buying or requesting custom terms:

1. Check the actual work, distribution, network interaction and source
   availability against the AGPL conditions; a private/internal label alone
   does not establish an exemption.
2. If your deployment cannot satisfy those conditions, evaluate a commercial
   plan whose permission scope covers it, or request custom terms.
3. For enterprise, OEM, academic, non-profit, managed-hosting, procurement, or
   co-ownership discussions, email
   [protoscience@anulum.li](mailto:protoscience@anulum.li).

Include the legal entity, product or service name, deployment shape
(distributed product, internal tool, hosted SaaS, managed service, or embedded
component), expected source availability, expected users or seats, support
expectations, compliance constraints, and the version line you plan to ship.

This page explains project licensing boundaries; it is not legal advice. If your
licensing obligations are material to a release or acquisition, review the AGPL
and the commercial terms with your counsel.

## Claim hygiene

The commercial documentation is checked by:

```bash
.venv/bin/python tools/check_commercial_claim_hygiene.py --check
```

The check keeps the AGPL/commercial boundary visible and fails wording that
implies paid code paths absent from the public package. Commercial licensing
changes usage terms, not the package contents.
