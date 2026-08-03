import { useEffect, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router-dom";
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
  // Nav collapses to a hamburger below sm — a promoter/banker on a phone
  // (the realistic device for daily use, not just a demo laptop) otherwise
  // gets 5 nav links + a schema chip + name/role + sign-out all fighting for
  // one row, which either overflows or wraps into an unreadable mess.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

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

  // Close the mobile menu on every route change so a nav tap doesn't leave
  // the panel open over the newly-loaded page.
  const location = useLocation();
  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  if (!user) return null; // App() only mounts AuthedApp once user is set

  const visibleNav = nav.filter((item) => item.roles.includes(user.role));

  return (
    <div className="min-h-screen bg-gray-50">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-gray-900 focus:shadow-lg"
      >
        Skip to main content
      </a>
      <header className="bg-white border-b px-4 sm:px-6 py-3">
        <div className="flex items-center gap-4">
          <span className="font-semibold text-lg">DRHP Studio</span>

          {/* Desktop nav — hidden below sm, where the hamburger takes over. */}
          <nav aria-label="Main navigation" className="hidden sm:flex gap-4 text-sm">
            {visibleNav.map((item) => (
              <Link key={item.to} to={item.to} className="text-gray-600 hover:text-gray-900">
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto hidden sm:flex items-center gap-3">
            {schemaHeader && (
              <span
                title={schemaHeader.regulation}
                className="hidden md:inline-block text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-2.5 py-1 whitespace-nowrap cursor-help"
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
              className="text-sm text-gray-500 hover:text-gray-800 border rounded px-3 py-1.5"
            >
              Sign out
            </button>
          </div>

          {/* Mobile: hamburger toggle, visible only below sm. */}
          <button
            type="button"
            onClick={() => setMobileNavOpen((prev) => !prev)}
            aria-expanded={mobileNavOpen}
            aria-controls="mobile-nav-panel"
            aria-label={mobileNavOpen ? "Close menu" : "Open menu"}
            className="ml-auto sm:hidden inline-flex h-11 w-11 items-center justify-center rounded border border-gray-300 text-gray-700"
          >
            <span className="sr-only">{mobileNavOpen ? "Close menu" : "Open menu"}</span>
            {mobileNavOpen ? (
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            )}
          </button>
        </div>

        {mobileNavOpen && (
          <div id="mobile-nav-panel" className="sm:hidden mt-3 pb-1 border-t pt-3">
            <nav aria-label="Main navigation" className="flex flex-col gap-1">
              {visibleNav.map((item) => (
                <Link
                  key={item.to}
                  to={item.to}
                  className="rounded px-3 py-2.5 text-sm text-gray-700 hover:bg-gray-50"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="mt-3 border-t pt-3 flex items-center justify-between gap-3">
              <span className="text-sm text-gray-700">
                {user.name} <span className="text-gray-400">·</span>{" "}
                <span className="font-medium">{ROLE_LABEL[user.role]}</span>
              </span>
              <button
                type="button"
                onClick={logout}
                className="text-sm text-gray-500 hover:text-gray-800 border rounded px-3 py-1.5"
              >
                Sign out
              </button>
            </div>
          </div>
        )}
      </header>
      <main id="main-content" className="p-4 sm:p-6 max-w-4xl mx-auto">
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
