"""Real authentication + server-side RBAC.

Replaces the earlier demo-grade UI role dropdown: promoter / auditor / banker
separation is now enforced in the FastAPI dependency graph (see
:mod:`app.auth.dependencies`), not just in what the frontend chooses to
display. Passwords are hashed (PBKDF2-HMAC-SHA256, per-user salt); sessions
are stateless JWT bearer tokens (see :mod:`app.auth.security`); users persist
to disk the same way session state does (see :mod:`app.auth.store`).
"""
