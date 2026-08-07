# Deploying to Render

`render.yaml` at the repo root is a Render Blueprint: one Docker web service
(the existing `Dockerfile` — FastAPI, the built SPA, and Tesseract in one
image) plus one managed Postgres.

Verified by running the blueprint's exact start command against an empty
database locally: migrations applied all five tables from scratch, the app
bound `$PORT`, `/api/health` reported `db_connected: true`, the SPA and its
deep links served, an unknown `/api/*` path still 404'd as JSON, and
invite-gated banker registration accepted the right code and rejected a wrong
one with 403.

## Before you start

Generate the one secret Render cannot generate for you:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`ENCRYPTION_KEY` must be a **Fernet key** (32 url-safe base64 bytes), so
Render's `generateValue` cannot produce a usable one. `JWT_SECRET_KEY` is an
arbitrary random string and *is* generated for you.

## Deploy

1. Push this repo to GitHub (already done — `main`).
2. Render dashboard → **New** → **Blueprint** → pick the repo.
3. Render reads `render.yaml` and prompts for the four `sync: false` values:

   | Variable | What to enter |
   |---|---|
   | `ENCRYPTION_KEY` | the Fernet key from above |
   | `BANKER_INVITE_CODE` | any string you choose |
   | `AUDITOR_INVITE_CODE` | any string you choose |
   | `GEMINI_API_KEY` | your key, or leave blank |
   | `ALLOWED_ORIGINS` | leave blank |

4. Apply. First build takes a while — the image installs Tesseract and builds
   the frontend.

Blank invite codes **disable auditor and banker registration entirely**, and
with them the certification workflow and the export. That is the single most
common way to end up with a deployment where half the app is unreachable.

Leaving `GEMINI_API_KEY` blank is fine: every LLM-touching feature falls back
to a deterministic path. The one exception is vernacular draft review, which
has no offline implementation and honestly reports `translated: false`.

`ALLOWED_ORIGINS` can stay blank because the API serves the SPA from its own
origin — cross-origin requests are not part of normal use. Set it to
`https://<your-service>.onrender.com` only if you ever host the frontend
separately.

## Where the data actually lives

Three separate places, and the difference matters:

| What | Where it lives in production | Survives a deploy? |
|---|---|---|
| Facts, users, review state, audit log | **Render Managed Postgres** — a separate service Render runs, not part of your container | Yes |
| Archived original uploads | The **container's own filesystem**, `/app/data/uploads` | **No** on free (no disk) |
| Generated draft sections | Process memory (`app.runtime_cache`) | No — regenerate with `POST /api/generate` |

The database is **not inside your Docker container**, and it is **not** the
Postgres from `docker-compose.yml` — that file is local development only and
Render never reads it. Render provisions its own PostgreSQL instance in the
region you picked, on storage it manages; you never see it as a filesystem.
The web service reaches it over Render's private network using the Internal
URL that `fromDatabase` wires in.

The practical consequence: **deploying new code never touches your data.**
The container is replaced on every deploy; Postgres is not. That is also why
the uploads vault is the one thing at risk on the free tier — it rides on the
container, not the database.

## Browsing the production database from pgAdmin

Render's database has two URLs. The **Internal** one only resolves inside
Render and is what the app uses. For pgAdmin you need the **External** one:
dashboard → your database → **Connect** → External.

| pgAdmin field | Value |
|---|---|
| Host name/address | the host from the External URL |
| Port | `5432` |
| Maintenance database | `drhp_studio` |
| Username | `drhp` |
| Password | from the External URL |
| SSL tab → SSL mode | **`require`** |

TLS 1.2+ is mandatory on the external endpoint, so `sslmode=require` is not
optional — a plain connection is refused.

Before you do this, restrict the database's **IP allow list** (dashboard →
your database → Access Control) to your own IP. The allow list applies to
external access only, so the app's internal connection is unaffected. That
endpoint is on the public internet and the database holds your users'
password hashes, not just synthetic issuer data.

## After the first deploy

Migrations run automatically at container start, so the database is ready.
To load the demo issuer, from your laptop against the deployed URL:

```bash
python backend/scripts/seed_demo.py --base-url https://<your-service>.onrender.com --with-uploads
```

The seeder honours the API's own rate limiter, so it will wait rather than
fail if it runs into the budget.

## Free-tier limits that will actually bite you

**Postgres is deleted 30 days after creation.** Not paused — deleted. Take a
backup before then:

```bash
python backend/scripts/backup.py --db-url "<the External Database URL from Render>"
```

**No persistent disk.** Free instances have an ephemeral filesystem, so
archived original uploads (`app.intake.vault`) do not survive a restart or
deploy. Facts, review state, user accounts, and the audit log all live in
Postgres and are unaffected — so the promoter journey, generation, validation,
certification, and export all work. What breaks is opening the *original
document* behind an already-confirmed fact after a restart.

Uncomment the `disk:` block in `render.yaml` on a paid instance to fix it. Note
the mount path is `data/uploads`, not `data/` — the image bakes the pinned ICDR
regulation text and the reference DRHPs into `data/`, and a disk mounted over
that directory hides them, breaking clause-text retrieval and the coverage
benchmark.

**Free web services sleep after inactivity** and take a while to wake. Worth
hitting the URL a few minutes before a demo.

## Things that do not change by deploying

These are the same limitations documented in CLAUDE.md, none of which Render
alters:

- Rate-limit windows are per-process, so several instances mean several
  budgets. Fine here — free and disk-backed services both run one instance.
- No WAL archiving or point-in-time restore, and nothing verifies that a
  restore works.
- Single-tenant: one deployment serves one issuer's promoter/auditor/banker
  team. There is no `tenant_id` anywhere in the schema.
- No account-admin UI and no password reset. Roles are gated by shared invite
  codes.

## If a deploy fails

**Builds, then never passes the health check** — almost always the port. The
service must listen on `$PORT`; `render.yaml`'s `dockerCommand` already does.

**`No module named 'psycopg2'`** — something is passing a `postgresql://` URL
past `Settings._require_async_driver`. The app needs the asyncpg driver named
in the scheme; the validator rewrites Render's URL automatically, so this
means the URL reached SQLAlchemy by another route.

**`/api/health` returns `"status": "degraded"`** — the process is up but
Postgres is unreachable. Check that the database and the web service are in
the same region and that `DATABASE_URL` is wired via `fromDatabase`.
