import { useEffect, useState, type ReactElement } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { getSchema, type ChecklistHeader, type LoginRole } from "./api/client";
import { useAuth } from "./auth/AuthContext";
import Login from "./pages/Login";
import Eligibility from "./pages/Eligibility";
import Wizard from "./pages/Wizard";
import GapReport from "./pages/GapReport";
import DraftViewer from "./pages/DraftViewer";
import BankerDashboard from "./pages/BankerDashboard";

// One list drives both the nav and the router. It used to be two: a
// role-filtered `nav` array plus a separate <Routes> block that knew nothing
// about roles — so the nav correctly hid Eligibility from an auditor or
// banker while `path="/"` still rendered it to them. Signing in as a banker
// landed on a promoter-only form whose Check button answered
// `POST /api/eligibility -> 403`, with no link having been clicked.
//
// This is nav *filtering*, not enforcement — the server is the real boundary
// (see backend/app/auth). Its job is to make sure the UI never offers, or
// lands on, a page the signed-in role cannot use. Auditor has no dedicated
// workspace yet (documented limitation) but can read the draft, same as a
// banker.
const ROUTES: {
  to: string;
  label: string;
  roles: LoginRole[];
  element: ReactElement;
}[] = [
  { to: "/eligibility", label: "Eligibility", roles: ["promoter"], element: <Eligibility /> },
  { to: "/wizard", label: "Wizard", roles: ["promoter"], element: <Wizard /> },
  { to: "/gaps", label: "Gap Report", roles: ["promoter"], element: <GapReport /> },
  {
    to: "/draft",
    label: "Draft",
    roles: ["promoter", "auditor", "banker"],
    element: <DraftViewer />,
  },
  { to: "/banker", label: "Banker Dashboard", roles: ["banker"], element: <BankerDashboard /> },
];

/** Where each role starts — its own workspace, not merely the first page it
 * happens to be allowed to open.
 *
 * Stated per role rather than derived from ROUTES order: "first accessible
 * entry" put a banker on the read-only Draft page, because Draft is listed
 * before Banker Dashboard. Not broken, but the wrong home for the one role
 * whose whole job is certification. The fallback still derives, so a role
 * added without a home here lands somewhere it can actually use. */
const LANDING: Record<LoginRole, string> = {
  promoter: "/eligibility", // the start of the promoter journey
  auditor: "/draft", // no dedicated workspace yet (documented limitation)
  banker: "/banker", // certification dashboard
};

function landingFor(role: LoginRole): string {
  return LANDING[role] ?? ROUTES.find((r) => r.roles.includes(role))?.to ?? "/draft";
}

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

  const visibleNav = ROUTES.filter((item) => item.roles.includes(user.role));

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
          {/* Root is a redirect, never a page — otherwise every role lands on
              whatever happens to be mounted at "/". */}
          <Route path="/" element={<Navigate to={landingFor(user.role)} replace />} />
          {ROUTES.map((r) => (
            <Route
              key={r.to}
              path={r.to}
              // A typed or bookmarked URL bypasses the nav entirely, so the
              // guard lives on the route, not on the link.
              element={
                r.roles.includes(user.role) ? r.element : <Navigate to={landingFor(user.role)} replace />
              }
            />
          ))}
          <Route path="*" element={<Navigate to={landingFor(user.role)} replace />} />
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
