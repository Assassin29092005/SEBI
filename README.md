# DRHP Studio — SME IPO Offer Document Drafter

AI-assisted drafting platform for SME IPO offer documents under SEBI ICDR
Chapter IX. Built for the SEBI hackathon (Problem Statement 4).

The full brief, guiding principles, and architecture live in [CLAUDE.md](CLAUDE.md).
The on-stage 12-minute demo script lives in [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md).

> **The output is a draft, not a filing.** It becomes submittable only after
> merchant banker due diligence and certification — by design.

## What it does

An SME promoter walks through: **sign in → eligibility → wizard + uploads →
fact confirmation → grounded generation → gap and contradiction validation →
banker certification → exchange-ready package**. The tool:

- Real accounts, not a UI role switch: every promoter/auditor/banker action
  is enforced server-side by a JWT bearer token and role check (see
  `backend/app/auth/`) — a promoter account cannot certify a section by
  editing local state, because the server checks the token's role, not what
  the frontend claims.
- Full audit log: who viewed, edited, or confirmed what, and when — every
  request is recorded (see `backend/app/audit.py`), including denied
  attempts, reviewable via `GET /api/audit` (banker-only).
- Banker-correction feedback loop: a banker may correct any extracted fact
  during due-diligence review, not just their own uploads — every correction
  records who performed it, and `GET /api/extraction-reliability`
  (banker-only) aggregates those corrections into a reliability signal by
  source and confidence, isolating how often a banker's review specifically
  caught someone else's extraction error (see `backend/app/extraction_reliability.py`).
- Encodes SEBI ICDR Chapter IX (Schedule VI Parts A + E) as a versioned,
  clause-cited YAML checklist — the single source of truth.
- Extracts facts from uploads, gates every value on promoter confirmation,
  and refuses to feed unconfirmed data into generation. Confirming a fact
  means seeing the real source, not a bare quoted string: an inline viewer
  renders the actual PDF page with the extracted snippet highlighted (real
  pixel highlighting via PyMuPDF where the page has a native text layer),
  shows an image upload directly, or highlights the exact substring in a
  `.txt` source's raw text.
- The wizard saves and resumes across sessions: every already-saved or
  confirmed answer rehydrates from the fact store on load instead of
  starting blank, and unsaved-but-typed text survives a closed tab via a
  per-account local draft — a real promoter filling this out over days
  should never have to retype what they already answered.
- Writes each disclosure section grounded in the fact store only. Missing
  data renders as `[REQUIRES INPUT: …]`. A digit-level hallucination guard
  discards any LLM output containing a number that isn't in the facts.
- Runs four validators over the draft: gap check, cross-section
  contradiction check, boilerplate detector, adversarial-examiner objections,
  and an Objects-of-the-Issue arithmetic check (objects sum + GCP ≟ issue
  size; GCP cap per Reg. 230(2)).
- The adversarial examiner can loop: revise sections carrying a fixable
  objection (boilerplate, reviewer prose) and re-examine, round after round,
  until nothing new turns up — actually checking that the draft "survives
  review" rather than raising objections once and never coming back. Missing
  facts, contradictions, and arithmetic mismatches are recognised as data
  problems no rewrite can fix, so the loop stops honestly instead of
  spinning (`backend/app/validate/iterative_examiner.py`).
- Locks export behind a per-section certification workflow so the merchant
  banker stays in the loop.
- Assembles the DRHP and the draft abridged prospectus (Sch. VI Part E per
  Reg. 246(3)) as `.docx` and bundles the full audit trail as a `.zip`.

Benchmarked against three real filed NSE Emerge SME DRHPs: 100% in-scope
chapter match on every one (auditor-only chapters explicitly out of scope).

## Repo layout

- `backend/` — FastAPI app. Package root is `backend/app/`; run tests from
  the repo root (a `conftest.py` puts `backend/` on `sys.path`).
- `frontend/` — React 18 + TypeScript + Tailwind. Vite dev server proxies
  `/api` to `127.0.0.1:8000`.
- `data/regulation/` — pinned ICDR text and amendment manifest.
- `data/reference_drhps/` — three public NSE Emerge filings + hand-mapped
  chapter YAMLs for the benchmark.
- `data/demo_company/` — synthetic issuer *Sunrise Agrotech Ltd* with a
  deliberately planted `issue_size_paise` contradiction for the live demo.
- `data/uploads/`, `data/audit/` — encrypted-at-rest archives of original
  source uploads and the access-log audit trail respectively (gitignored;
  see `app.crypto`, `app.intake.vault`, `app.audit`). Facts, review state,
  and user accounts live in Postgres, not on disk — see `app.db`.
- `tests/` — 248 backend test functions (needs a running Postgres — see below).

## Run it

```bash
# Backend
pip install -e "backend[dev]"
docker compose up -d                                   # Postgres (repo-root docker-compose.yml)
cd backend && alembic upgrade head && cd ..             # apply migrations
uvicorn app.main:app --reload --app-dir backend         # 127.0.0.1:8000
```

```bash
# Frontend (separate terminal, from frontend/)
npm install
npm run dev                                            # Vite proxies /api → :8000
```

```bash
# Seed the demo issuer through the real API (backend must be running).
# Registers (or logs into, on a repeat run) a demo promoter account first —
# every endpoint requires a bearer token now (see backend/app/auth).
python backend/scripts/seed_demo.py --with-uploads     # 42 wizard facts + planted contradiction
```

Opening the frontend directly: the app now starts on a sign-in screen.
Register a promoter account there to walk the wizard yourself, or register a
banker account (needs `BANKER_INVITE_CODE` from `.env`) to see the
certification dashboard. Auditor/banker registration is invite-gated — those
roles carry certification and role-tagged-upload authority; promoter
self-registration is always open.

## Tests + lint

```bash
python -m pytest tests/ -q                             # needs Postgres running — see "Run it" above
python -m pytest tests/test_facts.py -q                # one file
python -m pytest tests/test_facts.py::test_confirmation_makes_fact_available
python -m ruff check backend                           # lint (E,F,I,UP,B,ANN)
cd frontend && npx tsc --noEmit -p tsconfig.json       # frontend type-check
cd frontend && npm run build                           # production build
```

Tests default to the deterministic (non-LLM) path. An autouse fixture in
`conftest.py` blanks any keys in `.env` so the suite never depends on live
Gemini/Groq quota. Opt a specific test into real network calls with
`@pytest.mark.live_llm`.

## The whole system runs without any API key

`.env` is **optional**. The demo, the tests, and every user-facing feature
work with zero API keys configured.

### How

Every feature has two paths: an LLM path and a deterministic path. The LLM
path never runs first. It runs alongside, and its output is only trusted
after the deterministic guard passes. When no key is configured (or the
provider fails), the single choke point `app.llm.client.grounded_complete`
raises `LLMUnavailable` and every caller catches it and continues on the
deterministic path.

| Feature | Deterministic path (runs always) | LLM path (optional) |
|---|---|---|
| **Upload extraction** (`app.intake.uploads`) | Label-scan: line-by-line `Label: value` match against the checklist's fact ontology (`label_for_key` strips `_paise`/`[]`, Title-Cases). Monetary values parsed via `parse_inr_to_paise` (handles `₹14.00 crore`, `Rs. 12,50,00,000`, `₹85 lakh`) — deterministic integer arithmetic, LLM never trusted for money. | Prompts Gemini/Groq to return `(key, value, page, snippet, confidence)` JSON over prose passages. Snippets must be verifiable substrings of the source text; unknown keys dropped. On duplicate `(key, page)`, LLM proposal wins the merge. |
| **OCR for scanned/photographed pages** (`app.intake.ocr`) | If Tesseract is installed (a system binary — see `.env.example`), any PDF page whose native text layer is too thin to be real content, or a standalone image upload (`.png`/`.jpg`/`.tiff`/...), is rendered/opened and OCR'd. Facts sourced this way carry a lower confidence (`_OCR_CONFIDENCE = 0.6`, under the examiner's 0.7 low-confidence threshold — automatically flagged for review). | None. Pure image processing (PyMuPDF + Tesseract), no LLM involved. |
| **Section generation** (`app.generate.sections`) | Renders one deterministic sentence per confirmed fact: `"<Human key>: <formatted value> (source: <provenance detail>)."` Every rendered sentence gets an exact-offset `Citation`. Missing facts render as `[REQUIRES INPUT: <key> — <role> can provide this]`. | LLM writes disclosure prose with `[F:<fact_id>]` markers extracted into citations. **Hallucination guard:** every digit sequence in the LLM output must be a substring or displayable form of some provided fact value; on any violation (or zero citations) the LLM text is discarded and the deterministic renderer wins. |
| **Contradiction check** (`app.validate.contradictions`) | For each citation, resolve the fact and emit a `Claim` (subject = fact key, value = str). Regex-scan uncited monetary expressions in the section text. `cross_check` normalises Indian monetary surface forms to paise (`₹12.5 crore` → `125000000000`) and groups by subject; multi-value groups are `Contradiction`s. | Optional refinement pass; skipped silently on `LLMUnavailable`. |
| **Boilerplate detector** (`app.validate.boilerplate`) | 15-phrase generic-filler list (`"world-class"`, `"best-in-class"`, `"cutting-edge"`, …) + 8-gram overlap against every `*.txt` in `data/reference_drhps/`. Marker-aware (skips `[REQUIRES INPUT]` spans). | None. Pure text analysis. |
| **Adversarial examiner** (`app.validate.examiner`) | Emits an objection per unresolved `[REQUIRES INPUT]` marker; an "uncited quantitative claim" objection when a section has digits but no citations; contradiction-driven objections; arithmetic-finding objections; boilerplate-flag objections quoting the flagged span; low-confidence extraction objections (document-sourced facts with confidence < 0.7). | Optional LLM pass raises SME-exchange-reviewer objections. Any returned `clause_ref` that doesn't exactly match the checklist is sanitised to `None` — the examiner never invents citations. Skipped silently on `LLMUnavailable`. |
| **Objects arithmetic** (`app.validate.arithmetic`) | Integer paise arithmetic. `allocated = sum(objects_of_issue[].amount_paise) + gcp_amount_paise`. Blocker on overallocation; material on unallocated residual > 5%; blocker on GCP > `min(15% of issue, ₹10 cr)` per Reg. 230(2); no-crash on contradicted issue size (evaluates against each confirmed value). | None. Pure arithmetic. |
| **Gap check + role routing** (`app.validate.gaps`) | For each non-stub checklist entry, every `required_fact` key with zero confirmed facts becomes a `Gap` routed to the entry's `responsible_role` (promoter / auditor / banker / system). | None. Pure schema traversal. |
| **Coverage score + benchmark** (`app.coverage`) | Auditor entries only count toward `out_of_scope`. Non-stub, non-auditor entries with a gap-free `GeneratedSection` count as `covered`. The benchmark reads each `data/reference_drhps/*.sections.yaml` and matches its chapter TOC against checklist entry ids; stale ids downgrade to `not_encoded` — the score can never overstate. | None. Pure counting. |
| **Eligibility** (`app.eligibility`) | Explicit Reg. 228–230 rules; each failed criterion produces a `ReadinessItem` (current state, fix, indicative timeline, clause reference). | None. Pure rules. |
| **Document assembly** (`app.assemble.docx_builder`) | `python-docx` layout. Cover page shows both issue-size values with a red `CONTRADICTION DETECTED` line when confirmed sources disagree. Merchant-banker disclaimer + `DRAFT — NOT FOR FILING` notice. `[REQUIRES INPUT]` runs bold red. Superscript citation markers + per-entry `Sources` list. | None. Pure templating. |
| **Bundle export** (`app.assemble.bundle`) | `zipfile.ZIP_DEFLATED` package: both `.docx` + JSON dumps of every validator + full fact-provenance ledger + review audit trail + manifest with `regulation`, `amended_through`, `schema_version`, `reviewed_by_human`. Gated by the certification lock. | None. Pure packaging. |
| **Litigation lookup** (`app.intake.litigation`) | `MockLitigationConnector` loads `data/demo_company/litigation_records.json` for entities containing `"sunrise agrotech"`. Missing file / bad JSON returns `[]` with a warning log — never crashes. | `IndianKanoonConnector` — a real, working integration against api.indiankanoon.org (published Supreme Court/High Court/tribunal judgments; verified against the API's own reference client and against the live server). `FallbackLitigationConnector` tries it when `LITIGATION_PROVIDER=indiankanoon` + a token are configured, and falls back to the mock on any failure — same pattern as the LLM provider. It indexes decided matters only; there is no free public API over India's live pending-case docket (eCourts/NJDG has none). |
| **Persistence** (`app.db`, `app.facts_repo`, `app.review.repo`, `app.auth.store`) | Facts, review state, and user accounts are written directly to Postgres on every mutating endpoint — durability comes from Postgres's own write-ahead log, not application code. A backend restart or crash loses nothing; only the process-local generated-sections cache (`app.runtime_cache`) needs a fresh `POST /api/generate`. The audit log (`app.audit`) is the one exception, still a flat encrypted file — see Known Limitations in CLAUDE.md. | None. SQLModel + `asyncpg`, no LLM involvement. |
| **Wizard question copy** (`app.intake.wizard`) | Reads `question_copy.yaml`; per key returns `{prompt, help, input_hint}` in EN or Hindi. Fallback humanises the raw key when the copy map has no entry — never raises. | None. Pure YAML. |
| **Schema, review workflow, fact store** | Pydantic + integer paise math. No external calls. | None. |

### Why this design

The LLM is a **rendering engine over the fact store**, not the source of
truth. Every number and citation is deterministic. This is what makes:

- **Zero-key demo** genuinely demo the product, not a degraded shell.
- **Tests** deterministic and quota-free (all 160 pass without a network).
- **Trust** defensible: "one hallucinated number on stage destroys the pitch;
  the guard ensures no number reaches generation that isn't in a confirmed
  fact."

The provider is behind a `LLMProvider` `Protocol` (see
`backend/app/llm/client.py`) — swap `GeminiProvider` / `GroqProvider` for a
local Llama via `OllamaProvider` in about an hour if you want zero external
dependency.

### Optional: turn the LLM on

Copy `.env.example` to `.env` and fill either `GEMINI_API_KEY` (free tier,
default provider) or `GROQ_API_KEY` (free tier). No verification, no billing
gate for free-tier usage. The adversarial examiner becomes chattier, upload
extraction can lift facts from prose passages the label-scan misses, and the
generator can render slightly more natural section prose (still gated by the
hallucination guard).

## Known limitations

Stated openly, per CLAUDE.md:

- **Restated financial statements are auditor work by law.** The tool ingests
  and formats them; the coverage score marks them out-of-scope, never
  silently counted.
- **Litigation lookup is mocked.** No clean free API over Indian court
  records exists — a real integration is an adapter behind the existing
  `LitigationConnector` Protocol.
- **Schema is human-reviewed but not legally certified.** A faithful
  encoding of the regulation, not legal advice.
- **Extraction can misread documents.** Mitigated (not eliminated) by
  mandatory promoter confirmation against the highlighted source and by
  clickable citations for the banker's review.
- **Regulation is pinned as of 2026-03-21.** Later amendments require
  regenerating the schema — see `data/regulation/MANIFEST.md`.
- **The output is a draft, not a filing.** Submittable only after merchant
  banker due diligence and certification. By design.
