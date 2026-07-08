import { useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { getSchema, type ChecklistHeader } from "./api/client";
import Eligibility from "./pages/Eligibility";
import Wizard from "./pages/Wizard";
import GapReport from "./pages/GapReport";
import DraftViewer from "./pages/DraftViewer";
import BankerDashboard from "./pages/BankerDashboard";

// Demo-grade role switch — deliberately NOT auth (per project rules: no
// auth/RBAC plumbing beyond what the demo needs; real deployments use
// authenticated roles). It only filters the nav list below — every route
// stays mounted, so deep links keep working whatever the selected role.
type UiRole = "promoter" | "banker";

const ROLE_STORAGE_KEY = "drhp_role";

function loadStoredRole(): UiRole {
  try {
    return localStorage.getItem(ROLE_STORAGE_KEY) === "banker" ? "banker" : "promoter";
  } catch {
    return "promoter"; // storage blocked — fall back; the role just won't persist
  }
}

// The promoter journey, in pipeline order. The demo opens here — promoter
// UX first, pipeline second. `roles` drives nav filtering only, never routing.
const nav: { to: string; label: string; roles: UiRole[] }[] = [
  { to: "/", label: "Eligibility", roles: ["promoter"] },
  { to: "/wizard", label: "Wizard", roles: ["promoter"] },
  { to: "/gaps", label: "Gap Report", roles: ["promoter"] },
  { to: "/draft", label: "Draft", roles: ["promoter", "banker"] },
  { to: "/banker", label: "Banker Dashboard", roles: ["banker"] },
];

export default function App() {
  const [role, setRole] = useState<UiRole>(loadStoredRole);
  const [schemaHeader, setSchemaHeader] = useState<ChecklistHeader | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(ROLE_STORAGE_KEY, role);
    } catch {
      // Storage blocked — non-fatal, the switch still works for this session.
    }
  }, [role]);

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

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-3 flex items-center gap-6">
        <span className="font-semibold text-lg">DRHP Studio</span>
        <nav className="flex gap-4 text-sm">
          {nav
            .filter((item) => item.roles.includes(role))
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
          <select
            value={role}
            onChange={(e) => setRole(e.target.value === "banker" ? "banker" : "promoter")}
            aria-label="Role (demo switch)"
            title="Demo role switch — real deployments use authenticated roles."
            className="text-sm text-gray-700 bg-white border rounded px-2 py-1"
          >
            <option value="promoter">Promoter</option>
            <option value="banker">Merchant Banker</option>
          </select>
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
