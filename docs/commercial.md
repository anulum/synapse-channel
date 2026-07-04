# Commercial use

SYNAPSE CHANNEL is **dual-licensed**. There is **no feature difference between the
open-source and the commercial build** — the package on PyPI is the full product; a
commercial licence changes the terms, not the code.

## Which licence do I need?

- **The free AGPL-3.0** covers open-source, research, internal, and personal work —
  including use inside a company — as long as you do not expose a **closed-source** or
  **hosted** derivative over a network to third parties.
- **A commercial licence** is for shipping a **closed-source** product or a **SaaS**
  without the AGPL's network-copyleft obligation; it removes AGPL sections 13 and 5(d)
  (network-use source disclosure) within the scope of the purchased tier.

Rule of thumb: if anyone outside your organisation can interact with a modified version
over a network and you do not publish the corresponding source, you need a commercial
licence.

## Plans

The paid layers **add** permission, hosting, support, and convenience on top of the free
core — nothing is ever moved behind a paywall.

| Plan | For | What it adds |
| --- | --- | --- |
| **Community** — free (AGPL-3.0) | open source, research, personal, internal | the whole product, unlimited; copyleft applies |
| **Commercial Licence** | an org whose policy or product cannot use AGPL | the copyleft exemption on its own — same code, different terms, with a signed certificate |
| **Pro** | a solo developer or small team shipping one closed-source project | a commercial licence for one developer, the mobile app with push, and email support |
| **Team** | a company owning a shared fleet | unlimited projects in one legal entity, a managed observability dashboard (your hubs and data stay local), priority security patches, and named support |
| **Business / Enterprise** | regulated or multi-organisation deployments | an SLA with indemnification, a **managed federation gateway**, SSO, audit exports, and compliance support |

An optional **Supporter** (name-your-price) contribution funds the research and lists you
in `BACKERS`; it grants no extra rights, because the free core already holds none back.

[![View plans and buy a commercial licence](https://img.shields.io/badge/View_plans_%26_buy-anulum.li%2Fsynapse-0a7d3c?style=for-the-badge)](https://anulum.li/synapse/pricing.html)

Plans, current prices, and checkout are at
[**anulum.li/synapse/pricing.html**](https://anulum.li/synapse/pricing.html) (handled by
Polar.sh in **USD**; each buyer sees their local currency at checkout, CHF invoicing on
request). For enterprise, OEM, academic, non-profit, managed-hosting, or co-ownership
terms, write to [protoscience@anulum.li](mailto:protoscience@anulum.li).

The full commercial terms are in
[`COMMERCIAL-LICENSE.md`](https://github.com/anulum/synapse-channel/blob/main/COMMERCIAL-LICENSE.md);
the open-source licence is
[AGPL-3.0-or-later](https://github.com/anulum/synapse-channel/blob/main/LICENSE).

## Evaluation path

Use this path before buying or requesting custom terms:

1. Decide whether AGPL-3.0 already covers your use. If your product or service is
   open source under compatible terms, or your use stays private/internal, the
   Community path is normally the right fit.
2. If you will distribute a closed-source product or operate a hosted service
   without publishing corresponding source, choose the published Pro or Team
   plan unless your case needs custom terms.
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
