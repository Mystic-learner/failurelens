import { NavLink, Route, Routes } from "react-router-dom";
import { useEventsStatus } from "./events";
import DiagnosePage from "./pages/DiagnosePage";
import IncidentDetailPage from "./pages/IncidentDetailPage";
import IncidentsPage from "./pages/IncidentsPage";
import SignaturesPage from "./pages/SignaturesPage";

const LINK = "rounded-md px-3 py-1.5 text-sm";
const nav = ({ isActive }: { isActive: boolean }) =>
  `${LINK} ${isActive ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"}`;

const DOT: Record<string, string> = {
  live: "bg-emerald-400",
  connecting: "bg-amber-400",
  reconnecting: "bg-amber-400",
  failed: "bg-rose-500",
  stopped: "bg-slate-500",
};

export default function App() {
  const status = useEventsStatus();
  return (
    <div className="mx-auto min-h-screen max-w-4xl px-4 pb-16">
      <header className="flex flex-wrap items-center gap-2 py-4">
        <span className="mr-2 text-lg font-semibold text-white">FailureLens</span>
        <nav className="flex flex-wrap gap-1">
          <NavLink to="/" end className={nav}>
            Diagnose
          </NavLink>
          <NavLink to="/incidents" className={nav}>
            Incidents
          </NavLink>
          <NavLink to="/signatures" className={nav}>
            Signatures
          </NavLink>
        </nav>
        <span className="ml-auto flex items-center gap-2 text-xs text-slate-400" title="Live event connection">
          <span className={`h-2 w-2 rounded-full ${DOT[status] ?? "bg-slate-500"}`} />
          {status}
        </span>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<DiagnosePage />} />
          <Route path="/incidents" element={<IncidentsPage />} />
          <Route path="/incidents/:id" element={<IncidentDetailPage />} />
          <Route path="/signatures" element={<SignaturesPage />} />
          <Route path="*" element={<p className="py-8 text-slate-400">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}