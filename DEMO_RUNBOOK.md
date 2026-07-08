# DRHP Studio — Demo Runbook

The demo-day script, planted contradiction reference, honesty answers, and the
real numbers pulled from a green build. Read [CLAUDE.md](CLAUDE.md) for the
project brief and guiding principles first; this file is the on-stage script.

## Pre-flight (5 min before)

```bash
# 1. Backend
pip install -e "backend[dev]"
uvicorn app.main:app --reload --app-dir backend         # 127.0.0.1:8000

# 2. Frontend (separate terminal, from frontend/)
npm install
npm run dev                                              # Vite dev server proxies /api → :8000

# 3. Optional: an LLM key in .env (Gemini or Groq free tier).
#    THE DEMO WORKS OFFLINE. The examiner is thinner without a key; everything
#    else runs the deterministic path.
cp .env.example .env

# 4. Sanity checks (should print all green):
python -m pytest tests/ -q                               # 160 passed, 1 skipped
python -m ruff check backend                             # All checks passed
cd frontend && npm run build                             # clean
```

Reset the demo cleanly between runs: kill the backend, delete
`data/session/session.json` if present, restart. Persistence is on by default.

## Key numbers (from a green build, `schema_version: 0.3.0`)

| Metric | Value |
|---|---|
| Backend tests passing | 160 (1 opt-in live-LLM skip) |
| Checklist entries | 26 (all non-stub, `reviewed_by_human: true`) |
| Regulation pinned | ICDR as amended through `2026-03-21` |
| Reference filings benchmarked | 3 (public NSE Emerge DRHPs) |
| Chapter map vs. Harit Industries Ltd | 80.6% (25/31 in-scope) |
| Chapter map vs. Qualiance International Ltd | 80.6% (25/31 in-scope) |
| Chapter map vs. Smartdata Enterprises (India) Ltd | 80.6% (25/31 in-scope) |

The un-encoded chapters are the same six across all three filings and are all
non-Chapter-IX content (forward-looking-statements boilerplate, articles of
association excerpts, foreign-ownership legends, declaration). Out-of-scope
chapters are auditor-supplied by law and correctly not counted.

## THE ARC (12 minutes, promoter journey first)

### 0. Frame the problem (30 s)
Open on the Eligibility page. "SME promoters spend 6–9 months and 15–30 lakh
rupees on a merchant banker just to draft the offer document. This tool
compresses the drafting stage; the banker still certifies before filing."

### 1. Eligibility gate (90 s)
Fill the form with clean numbers. Submit → PASS banner + Continue link.
"When it fails, the same screen returns a **readiness report** — what to fix,
timeline, clause citation. The tool broadens the SME pipeline instead of just
serving the already-ready."

### 2. Wizard: promoter UX (2 min)
Toggle language to हिंदी at the top. "Every question shows *why we ask this*
with the exact ICDR clause it maps to." Fill one or two questions live. Then
switch to the Upload tab:

```bash
# In another terminal, load the whole synthetic company in one shot:
python backend/scripts/seed_demo.py --with-uploads
```

That pushes 42 wizard facts + 3 uploads through the real API — every
extracted value is a *proposal* until confirmed against the highlighted
source snippet. **An unconfirmed fact never feeds generation.**

### 3. Gap Report (60 s)
Nav → Gap Report. Show the three columns: **you can fix these** (promoter),
**needs your auditor**, **needs your merchant banker**. "This routing is what
lets us honestly claim 'substantially complete' — auditor-only work is out of
scope by law, not silently missing."

### 4. Draft (2 min) — headline metrics
Nav → Draft. Click **Generate draft**. Five metric tiles appear at the top:
sections generated · coverage % · gaps · **contradictions (RED)** · arithmetic
findings.

### 5. THE MOMENT — planted contradiction (90 s)
Click the red Contradictions tile. Banner unfurls:

> `issue_size_paise`: **₹12.50 crore** (wizard) vs. **₹14.00 crore** (bank
> sanction letter). Resolve before certification.

"The wizard said ₹12.5 crore. The bank's stale sanction letter said ₹14
crore. Both are confirmed sources. Our contradiction detector caught it
across sections. **On stage, one hallucinated number destroys trust — so we
also refuse to let the LLM invent numbers at all.**"

### 6. Objects arithmetic (60 s)
Expand Validation → Objects arithmetic. Under the planted contradiction it
raises exactly one **material `unallocated_proceeds`** finding against the
₹14 crore reading, citing *ICDR Sch. VI Part A, para (9)*. Under the wizard's
₹12.5 crore reading the objects (₹12.2 cr) + GCP (₹15 lakh) reconcile within
1.2% — clean. "Real DRHP drafts miss this kind of off-by-a-crore all the
time."

### 7. Side-by-side vs. filed DRHPs (90 s)
Expand **Benchmark vs filed DRHPs**. Tab through the three real NSE Emerge
filings. Each shows chapter-by-chapter mapping: green chips = mapped
checklist entries, gray = auditor content out of scope, amber = not yet
encoded (e.g. Forward Looking Statements). "80.6% in-scope match across three
independent real filings. Not a claim — measured evidence."

### 8. Role switch → Merchant Banker (30 s)
Header dropdown: Promoter → Merchant Banker. Nav filters. "Same tool, banker
view — this is a demo role switch, not real auth (that's a production
concern, documented in CLAUDE.md)."

### 9. Certification lock (90 s)
Nav → Banker Dashboard. Table of checklist entries with state
(draft → reviewed → certified). Click **Download exchange-ready package
(.zip)** — refused: *"Certification lock: N blocker sections uncertified.
The exchange-ready package unlocks the moment every blocker is certified."*
Advance a few blocker rows draft → reviewed → certified. Retry export.
"The lock is a feature, not an apology — the problem statement requires
intermediaries stay in the loop."

### 10. Open the docx (60 s)
Package downloads as `drhp_studio_package.zip`. Extract, open `drhp.docx`:
- Cover page carries both issue-size values + a bold red **CONTRADICTION
  DETECTED** line. The bug becomes an artefact in the exported document.
- Draft notice: "This document is a computer-assisted draft and not legal
  advice. It may be filed only after due diligence and certification by a
  SEBI-registered merchant banker (lead manager)."
- Body: every generated sentence carries a superscript citation marker; each
  section ends with a **Sources** list mapping marker → fact id.
- Zip also contains `gap_report.json`, `contradictions.json`, `coverage.json`,
  `examiner_objections.json`, `arithmetic_findings.json`,
  `facts_with_provenance.json`, `review_state.json`, `manifest.json`. Full
  audit trail in one download.

## Planted contradiction reference (from `data/demo_company/README.md`)

| Source | File | Value on disk | Meaning |
|---|---|---|---|
| Wizard answer | `wizard_answers.json` | `12500000000` | ₹12.50 crore |
| Extracted upload | `uploads/bank_sanction_letter.txt` (`Issue Size: Rs 14.00 crore`) | `14000000000` (after `parse_inr_to_paise`) | ₹14.00 crore |

Everything else in the fixtures is internally consistent — objects sum + GCP
reconciles with the ₹12.5 cr wizard value inside the 5% band.

## Fallbacks — if things go wrong on stage

- **Wi-Fi dies:** every LLM-dependent feature has a deterministic fallback.
  Generation, extraction, contradiction check, boilerplate, arithmetic,
  examiner — all offline-safe. Autouse pytest fixture blanks API keys in the
  test suite; the same fallback path is what production hits when
  `LLMUnavailable` is raised.
- **Backend crashes mid-demo:** `data/session/session.json` is written
  atomically after every mutating endpoint. Restart uvicorn — facts, review
  state, cached sections come back. Persist knob:
  `settings.persist_session = False` disables it; session lives in
  `settings.session_dir` (default `data/session/`, gitignored).
- **Frontend hot-reload trips:** hard-refresh the tab. Backend state
  survives.

## Judge Q&A — honesty answers (from CLAUDE.md Known Limitations)

Quote these faithfully — never soften them:

- **"Are the restated financials generated too?"** No — restated financial
  statements are auditor work by law. The tool ingests and formats them; the
  coverage score marks them explicitly out-of-scope, never silently counted.
- **"Is the litigation search real?"** No — the demo ships a mock behind a
  `LitigationConnector` Protocol. There is no clean free API over Indian
  court records; a real integration is an adapter behind that seam.
- **"Who verified your schema?"** Human-reviewed against the consolidated
  ICDR text pinned in `data/regulation/`; `reviewed_by_human: true` in the
  schema header. Not legally certified — it's a faithful encoding of the
  regulation, not legal advice.
- **"Can the extractor misread a document?"** Yes. Mitigated (not
  eliminated) by mandatory promoter confirmation against the highlighted
  source snippet, and by clickable citations on every generated sentence for
  the banker's review.
- **"Is the regulation current?"** Pinned to ICDR as amended through
  `2026-03-21` (ICDR (Amendment) Regulations, 2026, notified 2026-03-16,
  effective 2026-03-21). Any later amendment requires regenerating the
  schema — never patching code.
- **"What stops the LLM hallucinating?"** Three layers: grounded generation
  writes from the fact store only, missing data renders as
  `[REQUIRES INPUT: …]` (deliberate blanks over confident wrongness); a
  digit-level hallucination guard discards any LLM output containing a
  number not derivable from the facts; monetary values are re-parsed
  deterministically from the matched snippet, so LLM arithmetic is never
  trusted.
- **"Doesn't this replace merchant bankers?"** No, and the design refuses
  to. The certification lock blocks export until every blocker section is
  banker-certified; the exported cover carries a bold merchant-banker
  disclaimer; the problem statement itself requires intermediaries stay in
  the loop.
- **"The output is a filing?"** No — a draft. It becomes submittable only
  after merchant banker due diligence and certification. That is by design
  and matches SEBI's regulatory intent.
