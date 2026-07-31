// Login / register screen — the app's new entry point. Replaces the old
// demo-grade role dropdown: the role now comes from a real account, enforced
// server-side (see backend/app/auth).

import { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import type { LoginRole } from "../api/client";

type Mode = "login" | "register";

function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return String(err);
}

// The API surfaces validation/auth failures as "METHOD path → 4xx" — give
// the promoter something readable instead of raw status codes.
function friendlyError(err: unknown, mode: Mode): string {
  const raw = errorMessage(err);
  if (raw.includes("→ 401")) return "Incorrect email or password.";
  if (raw.includes("→ 409")) return "An account with this email already exists.";
  if (raw.includes("→ 403")) return "That invite code is not valid for this role.";
  if (raw.includes("→ 400") || raw.includes("→ 422")) {
    return mode === "register"
      ? "Please check the form — email, name, and an 8+ character password are required."
      : "Please check your email and password.";
  }
  return raw;
}

export default function Login() {
  const { login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<LoginRole>("promoter");
  const [inviteCode, setInviteCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") {
        await login({ email, password });
      } else {
        await register({
          email,
          name,
          password,
          role,
          invite_code: inviteCode || undefined,
        });
      }
    } catch (err) {
      setError(friendlyError(err, mode));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-semibold text-gray-900">DRHP Studio</h1>
          <p className="text-sm text-gray-500 mt-1">
            {mode === "login" ? "Sign in to continue your draft." : "Create your account."}
          </p>
        </div>

        <div className="rounded border bg-white shadow-sm p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <div>
                <label className="block text-xs font-medium text-gray-600" htmlFor="name">
                  Name
                </label>
                <input
                  id="name"
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                  placeholder="e.g. R. Iyer"
                />
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-gray-600" htmlFor="email">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                placeholder="you@company.com"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-gray-600" htmlFor="password">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={mode === "register" ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                placeholder={mode === "register" ? "At least 8 characters" : "••••••••"}
              />
            </div>

            {mode === "register" && (
              <>
                <div>
                  <label className="block text-xs font-medium text-gray-600" htmlFor="role">
                    Your role
                  </label>
                  <select
                    id="role"
                    value={role}
                    onChange={(e) => setRole(e.target.value as LoginRole)}
                    className="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm bg-white"
                  >
                    <option value="promoter">Promoter (SME issuer)</option>
                    <option value="auditor">Auditor</option>
                    <option value="banker">Merchant banker</option>
                  </select>
                </div>

                {role !== "promoter" && (
                  <div>
                    <label
                      className="block text-xs font-medium text-gray-600"
                      htmlFor="invite-code"
                    >
                      Invite code
                    </label>
                    <input
                      id="invite-code"
                      type="text"
                      required
                      value={inviteCode}
                      onChange={(e) => setInviteCode(e.target.value)}
                      className="mt-1 w-full rounded border border-gray-300 px-2 py-1.5 text-sm"
                      placeholder="Provided by your firm"
                    />
                    <p className="mt-1 text-xs text-gray-500">
                      {role === "banker" ? "Merchant banker" : "Auditor"} accounts require an
                      invite code — this role carries certification / role-tagged upload
                      authority, so it isn't open self-registration.
                    </p>
                  </div>
                )}
              </>
            )}

            {error && (
              <div className="rounded border border-red-300 bg-red-50 p-2 text-sm text-red-800">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={busy}
              className={
                "w-full rounded px-4 py-2 text-sm font-medium text-white " +
                (busy ? "bg-blue-400 cursor-not-allowed" : "bg-blue-700 hover:bg-blue-800")
              }
            >
              {busy
                ? mode === "login"
                  ? "Signing in…"
                  : "Creating account…"
                : mode === "login"
                  ? "Sign in"
                  : "Create account"}
            </button>
          </form>
        </div>

        <p className="text-center text-sm text-gray-600 mt-4">
          {mode === "login" ? (
            <>
              Don&rsquo;t have an account?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("register");
                  setError(null);
                }}
                className="text-blue-700 hover:underline font-medium"
              >
                Create one
              </button>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <button
                type="button"
                onClick={() => {
                  setMode("login");
                  setError(null);
                }}
                className="text-blue-700 hover:underline font-medium"
              >
                Sign in
              </button>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
