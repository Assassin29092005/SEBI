# Reference DRHPs

Four real filed SME DRHPs (public NSE Emerge documents, all Chapter IX
issues, downloaded from nsearchives.nseindia.com):

| Company | Filed | PDF | TOC mapping |
|---|---|---|---|
| Smartdata Enterprises (India) Limited | 2026-03-31 | `smartdata_drhp_2026-03-31.pdf` | `smartdata_drhp_2026-03-31.sections.yaml` |
| Qualiance International Limited | 2026-03-30 | `qualiance_drhp_2026-03-30.pdf` | `qualiance_drhp_2026-03-30.sections.yaml` |
| Harit Industries Limited | 2026-03-19 | `harit_industries_drhp_2026-03-19.pdf` | `harit_industries_drhp_2026-03-19.sections.yaml` |
| Shanti Inorganics Limited | 2025-09-26 | `shanti_inorganics_drhp_2025-09-26.pdf` | `shanti_inorganics_drhp_2025-09-26.sections.yaml` |

Shanti Inorganics was added later than the first three (see git history) to
widen the sample the benchmark rests on — one issuer, one sector, and one
filing quarter can't rule out coincidence; a second, unrelated filing
quarter (Sept 2025 vs. Mar 2026) and sector (industrial chemicals vs. the
first three) is real, if still modest, breadth. Its TOC has no standalone
"Related Party Transactions" chapter (disclosed inside Restated Financial
Information instead, going by its table of contents) — reported honestly
as simply absent from this filing's mapping rather than force-fit onto a
`financials.related_party_transactions` entry it doesn't actually have a
chapter for.

They are used **only** for:

1. **The reference benchmark** (`backend/app/coverage.py::benchmark`,
   served at `GET /api/coverage/benchmark`): each `*.sections.yaml` file is
   the filing's table of contents, hand-mapped chapter by chapter to
   checklist entry ids. Chapters map to `encoded` entries, are marked
   `out_of_scope_auditor` (tax-benefit statements, restated financials,
   capitalisation statements — auditor work by law), or `not_encoded`
   (honestly reported gaps in the schema). Stale entry ids are downgraded to
   `not_encoded` automatically so the score can never overstate coverage.
2. Section-structure reference.

A main-board filing (SRIT India, Reg. 6(1)) was deliberately excluded — only
Chapter IX SME filings are valid comparables; each mapping file records its
`framework_evidence`.

They are **not** templates to copy text from — the boilerplate detector
flags near-duplicates of `*.txt` extracts placed in this directory for
exactly that reason.
