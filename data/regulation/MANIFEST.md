# Regulation Version Manifest

Source texts used to generate `backend/app/schema/checklists/`. The schema
header must always match this manifest.

**Pinned regulation state: ICDR as amended through 2026-03-21** (ICDR
(Amendment) Regulations, 2026 — notified 2026-03-16, in force 2026-03-21).

| Document | Version | File | Source |
|----------|---------|------|--------|
| SEBI ICDR Regulations, 2018 (consolidated) | last amended 2025-03-08 | `ICDR_2018_consolidated_amended_2025-03-08.pdf` | [sebi.gov.in](https://www.sebi.gov.in/legal/regulations/mar-2025/securities-and-exchange-board-of-india-issue-of-capital-and-disclosure-requirements-regulations-2018-last-amended-on-march-8-2025-_93559.html) |
| ICDR (Amendment) Regulations, 2026 | notified 2026-03-16, effective 2026-03-21 | `ICDR_Amendment_Regulations_2026_notified_2026-03-16.pdf` | [sebi.gov.in](https://www.sebi.gov.in/legal/regulations/mar-2026/securities-and-exchange-board-of-india-issue-of-capital-and-disclosure-requirements-amendment-regulations-2026_100495.html) |

Extracted text (for schema verification and LLM-assisted entry generation):

- `chapter_ix_sme_ipo.txt` — Chapter IX (Reg. 228–280), consolidated PDF pages 168–203
- `schedule_vi_disclosures.txt` — Schedule VI Parts A–E, consolidated PDF pages 252–367
- `icdr_amendment_2026.txt` — full 2026 amendment notification text

## Resolved: abridged prospectus vs. Offer Document Summary

The 2026 amendment settles it **in favour of the abridged prospectus**:

- Schedule VI **Part A clause (4) — "Offer Document Summary" — is omitted**
  (avoids duplication with the abridged prospectus).
- Reg. **246(3)** now requires a **draft abridged prospectus per Schedule VI
  Part E** to accompany the draft offer document filed with the SME exchange.
- Part A clause (6) (Introduction) gains "(C) Summary of Contingent
  Liabilities" and "(D) Summary of Related Party Transactions".

So `output_targets: [abridged]` in the schema means the **Part E draft
abridged prospectus**, filed alongside the DRHP.

## Chapter IX quick reference (verified from consolidated text)

| Reg. | Subject |
|------|---------|
| 228 | Entities not eligible (debarment, wilful defaulter/fraudulent borrower, fugitive offender, outstanding convertibles) |
| 229 | Eligibility: post-issue capital ≤ ₹10 cr (≤ ₹25 cr permitted); EBITDA ≥ ₹1 cr in 2 of 3 FYs (229(6)); promoter-change 1-year bar (229(5)) |
| 230 | General conditions: demat, fully paid-up, 75% firm financing, OFS ≤ 20% of issue / 50% of seller's holding |
| 236 | Minimum promoters' contribution |
| 246 | Filing of offer document (+ draft abridged prospectus per 2026 amendment); due-diligence certificate Forms A/G of Sch. V |
| 268–269 | Allotment procedure, refunds |

Schema entries may be LLM-assisted from these texts, but **every entry is
human-reviewed before it ships**.
