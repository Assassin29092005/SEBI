import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { getSchema, type ChecklistHeader, type LoginRole } from "./api/client";
import { useAuth } from "./auth/AuthContext";
import Login from "./pages/Login";
import Eligibility from "./pages/Eligibility";
import Wizard from "./pages/Wizard";
import GapReport from "./pages/GapReport";
import DraftViewer from "./pages/DraftViewer";
import BankerDashboard from "./pages/BankerDashboard";

// Nav filtering only — the server enforces the actual role boundaries (see
// backend/app/auth); this just keeps the nav from listing links a given
// account cannot use. Auditor has no dedicated workspace yet (documented
// limitation) but can still read the draft, same as a banker.
const nav: { to: string; label: string; roles: LoginRole[] }[] = [
  { to: "/", label: "Eligibility", roles: ["promoter"] },
  { to: "/wizard", label: "Wizard", roles: ["promoter"] },
  { to: "/gaps", label: "Gap Report", roles: ["promoter"] },
  { to: "/draft", label: "Draft", roles: ["promoter", "auditor", "banker"] },
  { to: "/banker", label: "Banker Dashboard", roles: ["banker"] },
];

const ROLE_LABEL: Record<LoginRole, string> = {
  promoter: "Promoter",
  auditor: "Auditor",
  banker: "Merchant Banker",
};

function AuthedApp() {
  const { user, logout } = useAuth();
  const [schemaHeader, setSchemaHeader] = useState<ChecklistHeader | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSchema()
      .then((schema) => {
        if (!cancelled) setSchemaHeader(schema.header);
      })
      .catch(() => {
        // The chip is decorative: while loading or on error, render nothing.
        // The app shell must never break because /api/schema is unreachable.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!user) return null; // App() only mounts AuthedApp once user is set

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-3 flex items-center gap-6">
        <span className="font-semibold text-lg">DRHP Studio</span>
        <nav className="flex gap-4 text-sm">
          {nav
            .filter((item) => item.roles.includes(user.role))
            .map((item) => (
              <Link key={item.to} to={item.to} className="text-gray-600 hover:text-gray-900">
                {item.label}
              </Link>
            ))}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          {schemaHeader && (
            <span
              title={schemaHeader.regulation}
              className="hidden md:inline-block text-xs text-gray-400 bg-gray-50 border border-gray-200 rounded-full px-2.5 py-1 whitespace-nowrap cursor-help"
            >
              ICDR as amended through {schemaHeader.amended_through} · schema v
              {schemaHeader.schema_version}
            </span>
          )}
          <span className="text-sm text-gray-700">
            {user.name} <span className="text-gray-400">·</span>{" "}
            <span className="font-medium">{ROLE_LABEL[user.role]}</span>
          </span>
          <button
            type="button"
            onClick={logout}
            className="text-sm text-gray-500 hover:text-gray-800 border rounded px-2 py-1"
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="p-6 max-w-4xl mx-auto">
        <Routes>
          <Route path="/" element={<Eligibility />} />
          <Route path="/wizard" element={<Wizard />} />
          <Route path="/gaps" element={<GapReport />} />
          <Route path="/draft" element={<DraftViewer />} />
          <Route path="/banker" element={<BankerDashboard />} />
        </Routes>
      </main>
    </div>
  );
}

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <p className="text-gray-500 text-sm">Loading…</p>
      </div>
    );
  }

  if (!user) return <Login />;

  return <AuthedApp />;
}
