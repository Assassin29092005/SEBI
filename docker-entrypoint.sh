#!/bin/sh
# Container start: apply migrations, then serve.
#
# This exists as a script rather than as a long `dockerCommand` string in
# render.yaml because Render runs that string through its own shell. Wrapping
# it in another `sh -c "..."` gave the inner shell the whole `cd … && … && …`
# chain as a single program name to look up, and the deploy died with
#   sh: 1: cd /app/backend && python -m alembic upgrade head && …: not found
# and exit status 127. A single-token command has no such ambiguity: it works
# whether the platform execs it directly or hands it to a shell first.
#
# Keeping it in the image also means `docker run` locally and Render start
# the app identically, rather than the real start command living only in
# deployment config that never gets exercised in development.
set -e

# Alembic reads settings.database_url, and alembic.ini lives in backend/.
cd /app/backend
python -m alembic upgrade head

# Render assigns $PORT; a container that listens on a fixed port instead
# builds fine and then never passes its health check.
cd /app
exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
