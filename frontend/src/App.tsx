import { Link, Route, Routes } from "react-router-dom";
import Eligibility from "./pages/Eligibility";
import Wizard from "./pages/Wizard";
import GapReport from "./pages/GapReport";
import DraftViewer from "./pages/DraftViewer";
import BankerDashboard from "./pages/BankerDashboard";

// The promoter journey, in pipeline order. The demo opens here — promoter
// UX first, pipeline second.
const nav = [
  { to: "/", label: "Eligibility" },
  { to: "/wizard", label: "Wizard" },
  { to: "/gaps", label: "Gap Report" },
  { to: "/draft", label: "Draft" },
  { to: "/banker", label: "Banker Dashboard" },
];

export default function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white border-b px-6 py-3 flex items-center gap-6">
        <span className="font-semibold text-lg">DRHP Studio</span>
        <nav className="flex gap-4 text-sm">
          {nav.map((item) => (
            <Link key={item.to} to={item.to} className="text-gray-600 hover:text-gray-900">
              {item.label}
            </Link>
          ))}
        </nav>
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
