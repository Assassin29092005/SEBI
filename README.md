# DRHP Studio — SME IPO Offer Document Drafter

AI-assisted drafting platform for SME IPO offer documents under SEBI ICDR Chapter IX.
Built for the SEBI hackathon (Problem Statement 4). See [CLAUDE.md](CLAUDE.md) for the
full project brief, architecture, and guiding principles.

**The output is a draft, not a filing.** It becomes submittable only after merchant
banker due diligence and certification — by design.

## Layout

- `backend/` — FastAPI app. The checklist schema (`backend/app/schema/`) is the single
  source of truth for every disclosure requirement.
- `frontend/` — React + TypeScript + Tailwind promoter/banker UI.
- `data/regulation/` — pinned ICDR Chapter IX source text for schema generation.
- `data/reference_drhps/` — public filed SME DRHPs, benchmarking only.
- `data/demo_company/` — the synthetic demo issuer (Sunrise Agrotech Ltd).
- `tests/` — backend tests.

## Run

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e .
cp ../.env.example ../.env                        # fill in an LLM key
uvicorn app.main:app --reload

# Frontend
cd frontend
npm install
npm run dev
```

## Tests

```bash
cd backend && pytest ../tests
```
