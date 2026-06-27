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

| Plan | For | Grant |
| --- | --- | --- |
| **Community** — free (AGPL-3.0) | open source, research, personal | the full feature set; copyleft applies |
| **Indie** — pay-what-you-want, from CHF 9.99 | a solo developer or one closed-source project | copyleft exemption for **one** product, perpetual for the purchased version line |
| **Team** | a company shipping closed-source or SaaS | exemption for **unlimited** projects in one legal entity, with email support |
| **Managed / Enterprise** | hosted multi-tenant coordination, SLAs, compliance | bespoke terms |

[![View plans and buy a commercial licence](https://img.shields.io/badge/View_plans_%26_buy-anulum.li%2Fsynapse-0a7d3c?style=for-the-badge)](https://anulum.li/synapse/pricing.html)

Plans and checkout are at
[**anulum.li/synapse/pricing.html**](https://anulum.li/synapse/pricing.html) (handled by
Polar.sh, CHF). For enterprise, OEM, academic, or non-profit terms, write to
[protoscience@anulum.li](mailto:protoscience@anulum.li).

The full commercial terms are in
[`COMMERCIAL-LICENSE.md`](https://github.com/anulum/synapse-channel/blob/main/COMMERCIAL-LICENSE.md);
the open-source licence is
[AGPL-3.0-or-later](https://github.com/anulum/synapse-channel/blob/main/LICENSE).

## Claim hygiene

The commercial documentation is checked by:

```bash
.venv/bin/python tools/check_commercial_claim_hygiene.py --check
```

The check keeps the AGPL/commercial boundary visible and fails wording that
implies paid code paths absent from the public package. Commercial licensing
changes usage terms, not the package contents.
