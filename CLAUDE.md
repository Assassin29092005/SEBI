# CLAUDE.md

This file provides guidance to Claude Code when working on this repository.

## Commands

Backend (from repo root; package root is `backend/`, tests live in `tests/` at root — `conftest.py` puts `backend/` on `sys.path`):

```bash
pip install -e "backend[dev]"        # install backend + dev deps (pytest, ruff)
python -m pytest tests/ -q           # full suite
python -m pytest tests/test_facts.py -q            # one file
python -m pytest tests/test_facts.py::test_confirmation_makes_fact_available  # one test
python -m ruff check backend         # lint (line-length 100, rules E,F,I,UP,B,ANN)
uvicorn app.main:app --reload --app-dir backend     # run the API (127.0.0.1:8000)
python backend/scripts/seed_demo.py [--with-uploads]  # push data/demo_company/ fixtures through the
                                                       # real API (wizard answers, optionally uploads —
                                                       # --with-uploads includes the planted contradiction)
```

Tests default to the deterministic (non-LLM) path — an autouse `conftest.py` fixture blanks the API keys so the suite never depends on live Gemini/Groq quota. Opt a specific test into real network calls with `@pytest.mark.live_llm`.

Frontend (from `frontend/`):

```bash
npm install
npm run dev        # Vite dev server; proxies /api → 127.0.0.1:8000 (see vite.config.ts)
npm run build       # tsc -b && vite build
npx tsc --noEmit -p tsconfig.json   # type-check only
```

`pyrightconfig.json` at the repo root adds `backend` to `extraPaths` for editor type-checking.

## Project Overview

**SME IPO Offer Document Drafter** — an AI-assisted drafting platform built for the SEBI hackathon (Problem Statement 4: Simplifying IPO Offer Document Preparation for SMEs). Working name: **DRHP Studio**.

### The Problem

SME listings on the SME Exchange platforms (BSE SME, NSE Emerge) are throttled by the cost and months of effort needed to prepare a compliant IPO offer document. Promoters with lean teams and no capital-markets exposure depend on merchant bankers, lawyers, and compliance professionals from day one. SEBI explicitly wants the early drafting stage simplified — while **preserving the role of authorised intermediaries in review and certification before any regulatory submission**.

### What We're Building

A pipeline where an SME promoter enters their business, financial, and legal particulars (or uploads documents they already have) and receives a substantially complete, disclosure-ready **draft DRHP** under SEBI ICDR Chapter IX, with gaps and inconsistencies flagged and the merchant banker kept in the certification loop. "TurboTax for IPO paperwork."

The full version, in pipeline order:

1. **Eligibility gate** — fail fast against Chapter IX / exchange eligibility norms. A failed gate produces a **readiness report** (what to fix, indicative timeline), not a dead end — this is how the tool *broadens the pipeline* of SMEs rather than only serving the already-ready.
2. **Three intake channels** — a multilingual guided wizard (plain language; every question shows *why it is asked*, with the regulation clause it maps to), document uploads with auto-extraction, and a litigation lookup (mocked behind a connector — see Limitations).
3. **Promoter confirmation of every extracted fact** — auto-extracted values are *proposals* until the promoter confirms them against the highlighted source snippet. Extraction accuracy is a first-class check, distinct from cross-document consistency. An unconfirmed fact never feeds generation.
4. **Fact store with provenance** — one structured store; each fact carries its source (wizard answer / document + page / lookup), extraction confidence, confirmation status, and the **role** that supplied it (promoter / auditor / merchant banker / system).
5. **Machine-readable checklist schema** — SEBI ICDR Chapter IX plus the current amendments, encoded as versioned data (see "The Checklist Schema" below). The schema is the single source of truth: no disclosure requirement lives in prompts or code. It is regenerable from the regulation text when SEBI amends rules.
6. **Grounded generation** — the LLM writes each section using *only* facts from the store. Anything missing renders as `[REQUIRES INPUT: <fact> — <who can provide it>]`. Honest blanks beat confident hallucination; this is the core trust guarantee.
7. **Validation suite** — gap check (schema vs. fact store), contradiction check (numeric/entity consistency across sections — "page 40 disagrees with page 12"), boilerplate detector, and an **adversarial examiner agent** that plays exchange reviewer and raises objections until the draft survives them.
8. **Role-aware gap report** — every gap is routed: *promoter-fixable*, *needs your auditor* (e.g. restated financial statements, which the tool can ingest and format but never generate), or *needs your merchant banker* (e.g. due-diligence certificate). This routing is what makes "substantially complete" an honest, defensible claim.
9. **Banker certification workflow** — a dashboard with per-section review states (`draft → reviewed → certified`), an audit trail of banker edits, and a **certification lock**: the exchange-ready package cannot be exported until every blocker-severity section is certified. This is a feature demanded by the problem statement, not an apology.
10. **Document assembly** — python-docx produces the formatted draft DRHP **and the DRHP-stage draft abridged prospectus**. ✅ RESOLVED (2026-07-03): the ICDR (Amendment) Regulations, 2026 (effective 2026-03-21) omit the "Offer Document Summary" (old Sch. VI Part A para 4) and mandate a **draft abridged prospectus per Schedule VI Part E** filed with the draft offer document (Reg. 246(3)). See `data/regulation/MANIFEST.md`.
11. **Quantitative coverage score** — completeness measured per-section against real filed SME DRHPs, with auditor-only content explicitly marked out-of-scope rather than silently counted. Judges get evidence, not a claim.

## Domain Glossary

| Term | Meaning |
|------|---------|
| SEBI | Securities and Exchange Board of India — the capital-markets regulator |
| ICDR | SEBI (Issue of Capital and Disclosure Requirements) Regulations, 2018 — governs public issues |
| Chapter IX | The ICDR chapter governing SME IPOs (eligibility, disclosures, listing on SME exchanges) |
| DRHP / RHP | Draft Red Herring Prospectus (filed for review/public comment) / Red Herring Prospectus (updated, pre-issue) |
| Abridged prospectus | Standardized summary of the offer document (Sch. VI Part E); the 2026 ICDR amendment mandates a **draft** abridged prospectus with the DRHP (Reg. 246(3)) |
| Offer Document Summary | Former Sch. VI Part A para (4) — **omitted by the 2026 ICDR amendment** in favour of the abridged prospectus |
| SME Exchange | BSE SME and NSE Emerge — dedicated listing platforms for SME IPOs |
| Merchant banker / LM | SEBI-registered intermediary (Lead Manager) who conducts due diligence and certifies the offer document |
| Restated financials | Financial statements restated per ICDR and audited by a peer-reviewed auditor — mandatory DRHP content that only an auditor can produce |
| OFS | Offer for Sale — existing shareholders selling in the IPO (capped at 20% for SME IPOs since 2025) |
| GCP | General Corporate Purposes — a use-of-proceeds bucket (capped at lower of 15% or ₹10 cr for SME IPOs) |
| Objects of the Issue | The disclosed purposes for which IPO proceeds will be used |
| Promoter / promoter group | Controlling persons/entities; extensive disclosure and lock-in obligations attach to them |
| KMP | Key Managerial Personnel |
| RPT | Related Party Transactions — a high-scrutiny disclosure area |
| Material litigation | Outstanding legal proceedings above materiality thresholds — mandatory disclosure |

## Guiding Principles

- **Schema first.** The checklist schema is the single source of truth and the first thing built; everything downstream inherits its quality. If the schema is wrong, the generator, validator, gap report, and coverage score are all wrong together.
- **The LLM never invents a fact.** Generation is grounded in the fact store only. Missing data → `[REQUIRES INPUT]`. No exceptions, ever — one hallucinated number on stage destroys the entire trust pitch.
- **Every sentence is traceable.** Facts carry provenance from intake; every generated sentence carries a clickable citation back to its evidence. This is the differentiator over "anyone can make an LLM write a document."
- **Intermediaries stay in the loop by design.** The problem statement *requires* preserving the merchant banker's review/certification role. The certification lock is a selling point — never frame the tool as replacing intermediaries, only as compressing the early drafting stage.
- **Role-based truth.** Some content can only lawfully enter via the auditor or banker role (restated financials, due-diligence certificate). The tool ingests and formats such content; it never generates it. Define "substantially complete" as coverage of the narrative/disclosure sections — say so openly.
- **Promoter-first UX.** "Simple enough for a first-time issuer" is a graded criterion, not a nice-to-have. Plain language, vernacular support, clause-linked "why we ask this" explanations, visible progress. The demo opens with the promoter journey, not the pipeline.
- **Regulatory currency.** Pin the exact regulation version (ICDR as amended through March 2026) in the schema header. Cite the clause for every requirement. When SEBI amends, regenerate the schema — don't patch code.
- **Demo-ready over production-ready.** Hackathon rules: a compelling end-to-end demo (eligibility → intake → confirm → generate → validate → banker certify → export) with one clearly synthetic demo company. Note production concerns in comments/docs rather than building them.

## Architecture

```
┌──────────────┐   ┌───────────────┐   ┌──────────────┐   ┌──────────────┐
│  Intake       │──▶│  Fact Store    │──▶│  Generator    │──▶│  Validation   │
│  wizard ·     │   │  (provenance,  │   │  (LLM, schema-│   │  gap · contra-│
│  uploads ·    │   │  confirmation, │   │  driven,      │   │  diction ·    │
│  litigation   │   │  roles)        │   │  grounded)    │   │  adversarial  │
└──────────────┘   └───────────────┘   └──────────────┘   └──────┬───────┘
       ▲                    ▲                                     │
┌──────┴───────┐   ┌────────┴──────┐                     ┌────────▼───────┐
│  Eligibility  │   │  Checklist     │                     │  Assembly      │
│  gate +       │   │  Schema        │                     │  (python-docx) │
│  readiness    │   │  (ICDR Ch. IX, │                     │  DRHP + abridged│
│  report       │   │  versioned)    │                     └────────┬───────┘
└──────────────┘   └───────────────┘                              │
                                              ┌────────────────────┴────────┐
                                              │  Promoter draft + gap report │
                                              │  Banker dashboard + cert lock│
                                              │  Exchange-ready package      │
                                              └─────────────────────────────┘
```

Repo layout (built):

- `backend/app/schema/` — checklist YAML + Pydantic loader/validator + `applicability.py` (evaluates conditional `has_<fact>` requirements against the fact store; unrecognised conditions default to applicable — over-disclosing is safe, silently dropping a requirement isn't).
- `backend/app/eligibility.py` — eligibility gate rules + readiness-report generator.
- `backend/app/intake/` — wizard question flow + `question_copy.yaml` (schema-derived, multilingual), upload handling/extraction (LLM + deterministic label-scan fallback), litigation connector (mock behind a `Protocol`, like a real integration seam).
- `backend/app/facts.py` — fact store: immutable facts with provenance, confidence, confirmation status, supplying role.
- `backend/app/llm/client.py` — the single provider-agnostic LLM client (Gemini / Groq); raises `LLMUnavailable` when no key is configured or the call fails, which is what every LLM-dependent feature falls back to a deterministic path on.
- `backend/app/generate/` — per-section generation: schema requirement → retrieval of relevant facts → grounded prompt → section text with citations and `[REQUIRES INPUT]` markers. Recomputes `*_paise` values deterministically from the matched snippet rather than trusting LLM arithmetic.
- `backend/app/validate/` — gap checker, contradiction detector (extract all numeric/entity claims, cross-check), boilerplate detector, adversarial examiner agent, and `arithmetic.py` (Objects-of-Issue arithmetic: objects sum + GCP ≟ issue size, GCP cap per Reg. 230(2); handles a contradicted issue size without crashing).
- `backend/app/review/` — banker workflow: section states, edit audit trail, certification lock.
- `backend/app/assemble/` — python-docx DRHP + abridged prospectus assembly (cover callout when confirmed issue-size facts disagree; merchant-banker disclaimer baked in) + `bundle.py` (exchange-ready ZIP: both docx + gap/contradiction/coverage/examiner/arithmetic JSON + full fact-provenance ledger + review audit trail + manifest, gated by the certification lock).
- `backend/app/persistence.py` — atomic JSON snapshot of the mutable app state (facts, review, cached sections) into `data/session/session.json` so a backend restart preserves the demo run. Corrupt-file-safe at boot; toggle with `settings.persist_session`.
- `backend/app/coverage.py` — coverage score vs. reference filed DRHPs (real benchmark against `data/reference_drhps/`, not schema-only self-reference); auditor-only content marked out-of-scope, never silently counted.
- `backend/app/main.py` — FastAPI app tying it together.
- `backend/scripts/seed_demo.py` — pushes `data/demo_company/` fixtures through the real API (exercises confirmation the same way the wizard does) for fast manual testing.
- `DEMO_RUNBOOK.md` — demo-day script, planted-contradiction reference, and judge Q&A honesty answers.
- `frontend/src/pages/` — `Eligibility` → `Wizard` (with fact-confirmation screens) → `GapReport` → `DraftViewer` (citations clickable) and `BankerDashboard`.
- `data/regulation/` — ICDR Chapter IX text (source for schema generation), amendment notes, pinned version manifest.
- `data/reference_drhps/` — 3 real filed SME DRHPs (public NSE Emerge filings) with hand-built `.sections.yaml` structure references, used only for the coverage benchmark and section-structure reference.
- `data/demo_company/` — the synthetic demo SME: filled wizard answers, fake uploaded documents, and **one deliberately planted contradiction** (`issue_size_paise`, wizard vs. `bank_sanction_letter.txt`) for the live demo — see its README for the exact values.

## The Checklist Schema (build this first)

Machine-readable YAML, one entry per disclosure requirement:

```yaml
- id: capital_structure.share_capital_history
  clause_ref: "ICDR Sch. VI Part A (as applied by Ch. IX), para ..."
  section: "Capital Structure"
  title: "History of equity share capital"
  description: >
    Build-up of share capital since incorporation: date, number of shares,
    face value, issue price, nature of consideration, nature of allotment.
  applicability: always            # or a condition, e.g. "has_convertibles"
  required_facts:
    - share_allotments[]           # keys into the fact-store ontology
  responsible_role: promoter       # promoter | auditor | banker | system
  severity: blocker                # blocker | material | minor
  output_targets: [drhp, abridged] # which assembled documents include it
```

Rules:
- Every entry cites its clause. No orphan requirements.
- `responsible_role` drives the gap-report routing and the certification lock.
- The wizard's question flow and the generator's section plan are both *derived* from this schema — never hand-maintained in parallel.
- The schema file header pins the regulation version and amendment date it encodes.
- Initial generation of entries can be LLM-assisted from the regulation text in `data/regulation/`, but **every entry is human-reviewed before it ships** — the schema is legal-adjacent content.

## Implementation Order (with cut lines)

**Status: all rows built, including all four cut-line features.** Table kept as the original plan / cut-order reference — if scope must shrink again, cut from the bottom.

| Days | Deliverable | Notes |
|------|------------|-------|
| 0–2 | Checklist schema (top ~10 sections deep, rest stubbed) + fact store | Go/no-go checkpoint: if the schema isn't solid by day 2–3, cut from the bottom of this table, never from the top |
| 3–4 | Wizard (schema-derived) + upload extraction + fact confirmation UI | English only at this stage |
| 5–6 | Grounded generator + gap/contradiction checks | The planted-error demo depends on the contradiction check |
| 7 | python-docx assembly (DRHP; abridged prospectus if reg question settled) | |
| 8–9 | Banker dashboard + certification lock + coverage score | |
| 10–11 | **Cut-line features:** adversarial examiner agent, multilingual wizard, litigation lookup, readiness report | In this priority order |
| 12 | Demo rehearsal: promoter journey first, then the 100-page draft beside a real filing, then the planted contradiction caught live | Rehearse the honesty answers (see Limitations) |

## Conventions

- Python: type hints everywhere, Pydantic models for all API payloads and the schema loader, `ruff` for lint/format.
- Frontend: functional components, TypeScript, Tailwind.
- All monetary values in INR as integers (paise) or `Decimal` — never floats. Lakh/crore formatting at the display layer only.
- Dates: ISO 8601 at API boundaries, timezone-aware internally (IST context).
- Facts are immutable once confirmed; corrections create a new fact version with provenance to the old one.
- LLM access behind a single provider-agnostic client (free tiers: Gemini / Groq); temperature 0 for generation and validation; every LLM call logs its prompt-context fact IDs so citations are reconstructible.
- The demo company is clearly synthetic (fake CIN/PAN-format identifiers, a name like "Sunrise Agrotech Ltd") — never real business data.

## Known Limitations (state them if asked — never hide them)

- **Restated financial statements are auditor work by law.** The tool ingests and formats them; it cannot and does not generate them. The coverage score marks them out-of-scope explicitly.
- **The litigation lookup is mocked** — there is no clean free API over Indian court records; a real integration means an adapter behind the existing connector protocol.
- **The checklist schema is human-reviewed but not legally certified.** It's a faithful encoding of the regulation, not legal advice.
- **Extraction can misread documents.** Mitigated (not eliminated) by mandatory promoter confirmation against the highlighted source and by clickable citations for the banker's review.
- **Regulation version is pinned as of build date** (ICDR as amended through 2026-03-21; see `data/regulation/MANIFEST.md`). Any later amendment requires regenerating the schema.
- **The output is a draft, not a filing.** It becomes submittable only after merchant banker due diligence and certification — this is by design and matches the problem statement.

## What NOT to Do

- **Never let the LLM invent facts, numbers, names, or clause citations.** If it's not in the fact store, it's `[REQUIRES INPUT]`.
- **Never position the tool as replacing merchant bankers** or as producing a filing-ready document without certification — that contradicts the problem statement and SEBI's regulatory intent.
- Don't put real company data, real financials, or real personal identifiers in the repo — synthetic demo issuer only. Reference DRHPs in `data/reference_drhps/` are public filings used for benchmarking, not templates to copy text from (the boilerplate detector exists for a reason).
- Don't hardcode disclosure requirements in prompts or code — everything flows from the schema.
- Don't expand scope to main-board IPOs, rights issues, or non-Chapter-IX frameworks.
- Don't build auth/RBAC/e-sign plumbing beyond what the demo needs — mention it in docs instead.
- Don't chase generation eloquence at the cost of traceability — a plainly worded, fully cited section beats beautiful uncited prose.
