# DRHP Studio — Demo Runbook

The demo-day script, planted contradiction reference, honesty answers, and the
real numbers pulled from a green build. Read [CLAUDE.md](CLAUDE.md) for the
project brief and guiding principles first; this file is the on-stage script.

## Pre-flight (5 min before)

```bash
# 1. Backend
pip install -e "backend[dev]"
docker compose up -d                                     # Postgres (repo-root docker-compose.yml)
cd backend && alembic upgrade head && cd ..               # apply migrations to the dev DB
uvicorn app.main:app --reload --app-dir backend           # 127.0.0.1:8000

# 2. Frontend (separate terminal, from frontend/)
npm install
npm run dev                                              # Vite dev server proxies /api → :8000

# 3. Optional: an LLM key in .env (Gemini or Groq free tier).
#    THE DEMO WORKS OFFLINE. Every LLM-dependent feature has a deterministic
#    fallback (see README.md § "The whole system runs without any API key").
#    Zero keys → the examiner is thinner and generation prose is more
#    templated, but the arc, guarantees, and the planted contradiction all
#    fire identically.
#    Also set BANKER_INVITE_CODE (any string) so you can register the demo
#    banker account in step 5 — see backend/app/auth.
cp .env.example .env

# 4. Sanity checks (should print all green — pytest needs docker compose up
#    and a one-time `createdb drhp_studio_test`, see CLAUDE.md § Commands):
python -m pytest tests/ -q
python -m ruff check backend                             # All checks passed
cd frontend && npm run build                             # clean

# 5. Register the two demo accounts you'll use on stage (frontend running,
#    or via curl — see backend/app/auth). Promoter self-registers; banker
#    needs the BANKER_INVITE_CODE from .env. Do this before the audience
#    is watching — it's a one-time setup step, not part of the pitch.
```

Reset the demo cleanly between runs: facts, review state, and accounts live
in Postgres now, so a backend restart alone changes nothing (that's the
point — see `app.db`). For a genuinely clean slate, drop and re-migrate the
dev database (`docker compose exec postgres dropdb -U drhp drhp_studio &&
docker compose exec postgres createdb -U drhp drhp_studio && (cd backend &&
alembic upgrade head)`) before re-registering demo accounts.

## Key numbers (from a green build, `schema_version: 0.4.0`)

| Metric | Value |
|---|---|
| Backend tests passing | not re-verified in this merge (no local Postgres in this environment — see CLAUDE.md § Commands); 248 test functions present as of this merge, run `pytest tests/ -q` locally to confirm |
| Checklist entries | 32 (all non-stub; six v0.4.0 additions pending the line-by-line human review pass — see the schema header) |
| Regulation pinned | ICDR as amended through `2026-03-21` |
| Reference filings benchmarked | 3 (public NSE Emerge DRHPs) |
| Chapter map vs. Harit Industries Ltd | 100% (31/31 in-scope) |
| Chapter map vs. Qualiance International Ltd | 100% (31/31 in-scope) |
| Chapter map vs. Smartdata Enterprises (India) Ltd | 100% (31/31 in-scope) |

v0.4.0 closed the last six un-encoded chapters (conventions/presentation,
forward-looking statements, key industry regulations, foreign-ownership
restrictions, articles-of-association provisions, declaration). Out-of-scope
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

On a proposal card, click **View source document**. "This isn't a quoted
string we're asking you to trust — it's the actual page, rendered inline,
with the exact snippet highlighted." (For a `.pdf` upload this is a real
page render with a genuine highlight overlay wherever the page has
embedded text; for the bank sanction letter specifically it's a `.txt`
upload, so the highlight is an exact substring match in the raw text —
either way, the real source, not a paraphrase.)

Optional aside if asked "what happens if a promoter gets interrupted?":
refresh the Wizard tab after saving a couple of answers — the page reloads
already showing them saved/confirmed, not blank, because it now rehydrates
from the fact store instead of starting fresh every time. "A demo run is
one sitting; a real SME promoter fills this out over days between other
work. Losing progress to a closed tab was the actual gap, not a nice-to-have."

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

### 6a. Iterative examiner (optional, 45 s)
Expand Validation → **Run examiner until it survives review**. "The
single-shot examiner raises objections once. This loops: it revises whatever
it can — vague boilerplate, generic reviewer prose — and re-checks, round
after round, until nothing new turns up." Point at the round-by-round list
and the stop reason. Honest if asked: a missing fact or the planted
contradiction is a *data* problem, not a wording problem — the loop
recognises that (`no_revisable_objections`) and stops rather than pretending
a rewrite fixed it; only a corrected or new fact resolves those.

### 7. Side-by-side vs. filed DRHPs (90 s)
Expand **Benchmark vs filed DRHPs**. Tab through the three real NSE Emerge
filings. Each shows chapter-by-chapter mapping: green chips = mapped
checklist entries, gray = auditor content out of scope (amber would mark any
chapter not yet encoded — as of v0.4.0 there are none). "100% in-scope match
across three independent real filings. Not a claim — measured evidence."

### 8. Sign out → sign in as Merchant Banker (30 s)
Sign out, sign in with the banker account registered during pre-flight. Nav
changes to the banker's view. "This is a real account with a real role check
on the server — a promoter token literally cannot call the certify endpoint,
you saw the 403 if you try it. Not a UI switch."

### 9. Certification lock (90 s)
Nav → Banker Dashboard. Table of checklist entries with state
(draft → reviewed → certified). Click **Download exchange-ready package
(.zip)** — refused: *"Certification lock: N blocker sections uncertified.
The exchange-ready package unlocks the moment every blocker is certified."*
Advance a few blocker rows draft → reviewed → certified. Retry export.
"The lock is a feature, not an apology — the problem statement requires
intermediaries stay in the loop."

### 9a. Extraction reliability panel (optional, 30 s)
Scroll down on the Banker Dashboard past the due-diligence upload card.
"Extraction reliability" shows correction rates bucketed by source
(document / lookup / role upload) and confidence band, plus a specific
count of corrections a **banker's** due-diligence review caught versus a
self-correction by whoever originally supplied the fact. "This is the
feedback loop — every correction already gets recorded with who did it;
this is where that history turns into a QA signal on the tool's own
extraction reliability, not just draft content." Honest if asked: on a
single fresh demo run the numbers are sparse — it's designed to become
meaningful with continuous real usage across drafting cycles, not a
one-shot demo metric.

### 9b. Regulatory staleness watcher (optional, 30 s)
Scroll further to "Regulatory staleness." Click **Check for updates now** —
this is a real live HTTP call to sebi.gov.in, not a canned response. "The
schema pins ICDR as amended through a specific date. A demo checks that
once and moves on; SEBI amendments happen continuously in production, so
this genuinely re-checks SEBI's own public site for anything ICDR-tagged
newer than our pin — and it's also on a weekly GitHub Actions cron, so it
runs even when nobody's looking." If it comes back clean: "that's a real
result, not a hardcoded 'all good' — right now nothing SEBI's published is
newer than our pin." It never auto-updates the schema either way — every
schema change is human-reviewed, this only flags "go check this."

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
- **Backend crashes mid-demo:** facts, review state, and accounts are
  durable in Postgres (`app.db`) as of every mutating call, not a
  snapshot file — restart uvicorn and everything is back, with no persist
  knob to check. Only cached generated sections need a fresh
  `POST /api/generate` (they were never persisted — see `app.runtime_cache`).
  Make sure `docker compose ps` shows Postgres healthy before restarting.
- **Frontend hot-reload trips:** hard-refresh the tab. Backend state
  survives.

## Judge Q&A — honesty answers (from CLAUDE.md Known Limitations)

Quote these faithfully — never soften them:

- **"Are the restated financials generated too?"** No — restated financial
  statements are auditor work by law. The tool ingests and formats them; the
  coverage score marks them explicitly out-of-scope, never silently counted.
- **"Is the litigation search real?"** Yes, when configured — `Indian
  KanoonConnector` calls the real api.indiankanoon.org API (verified
  against their own reference client and against the live server: an
  invalid token gets a real `401 Invalid token.` back, not a connection
  failure). The honest caveat: IndianKanoon indexes *published judgments*,
  not a live pending-case docket — no free public API exists over Indian
  courts' live case status (eCourts/NJDG has none), so a clean result means
  "no published judgment names this entity," not "no litigation exists."
  Falls back to the offline demo mock automatically if unconfigured or
  unreachable.
- **"Who verified your schema?"** Human-reviewed against the consolidated
  ICDR text pinned in `data/regulation/`; `reviewed_by_human: true` in the
  schema header. The six v0.4.0 entries had their clause refs verified
  against the pinned Schedule VI text but still await the same line-by-line
  human pass (flagged in the schema header). Not legally certified — it's a
  faithful encoding of the regulation, not legal advice.
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
- **"Is the role separation real, or just a UI switch?"** Real — every
  endpoint requires a JWT bearer token, and certification/role-tagged-upload
  actions additionally require the token's role to match (see
  `backend/app/auth/`). A promoter account gets a 403 from the certify
  endpoint, not just a hidden button. What's not built: an account-admin UI
  (auditor/banker registration uses a shared invite code, not per-user
  invites) and password reset — appropriate for one issuer's small team, not
  a multi-firm SaaS yet.
