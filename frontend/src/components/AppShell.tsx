import { FileText, History, Lock, LogOut, Mic2, Sparkles } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { useAuth } from "../state/auth";
import { ActiveJobChip } from "./ActiveJobChip";
import { ErrorBanner } from "./ui";

const navItems = [
  { to: "/setup", label: "Setup", icon: FileText },
  { to: "/interview", label: "Interview", icon: Mic2 },
  { to: "/history", label: "History", icon: History },
];

export function AppShell() {
  const { token, user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [isSetupComplete, setIsSetupComplete] = useState(false);
  const [hasCheckedReadiness, setHasCheckedReadiness] = useState(false);
  const [readinessError, setReadinessError] = useState<string | null>(null);

  const refreshReadiness = useCallback(async () => {
    if (!token) {
      setIsSetupComplete(false);
      setHasCheckedReadiness(true);
      return;
    }
    setReadinessError(null);
    try {
      const jobs = await api.listJobs(token);
      const statuses = await Promise.all(
        jobs.map((job) => api.prepStatus(token, job.id).catch(() => null)),
      );
      setIsSetupComplete(statuses.some((status) => status?.can_start));
    } catch (err) {
      setReadinessError(err instanceof ApiError ? err.detail : "Could not check setup readiness.");
      setIsSetupComplete(false);
    } finally {
      setHasCheckedReadiness(true);
    }
  }, [token]);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  useEffect(() => {
    if (hasCheckedReadiness && !isSetupComplete && location.pathname !== "/setup") {
      navigate("/setup", { replace: true });
    }
  }, [hasCheckedReadiness, isSetupComplete, location.pathname, navigate]);

  return (
    <div className="app-shell">
      <header className="workspace-header">
        <div className="brand">
          <span className="brand-mark">
            <Sparkles size={20} />
          </span>
          <div>
            <strong>Interview Coach</strong>
            <span>Practice studio</span>
          </div>
        </div>
        <ActiveJobChip />
        <div className="user-menu">
          <span>{user?.email}</span>
          <button className="icon-button" onClick={logout} title="Log out">
            <LogOut size={18} />
          </button>
        </div>
      </header>
      <main className="main-panel">
        <section className="tab-stage" aria-label="Workspace sections">
          <nav className="big-tabs">
          {navItems.map((item) => {
            const Icon = item.icon;
            const disabled = item.to !== "/setup" && hasCheckedReadiness && !isSetupComplete;
            if (disabled) {
              return (
                <button
                  key={item.to}
                  className="big-tab disabled"
                  type="button"
                  disabled
                  title="Complete setup before opening this section"
                >
                  <Icon size={24} />
                  <span>{item.label}</span>
                  <Lock size={16} />
                </button>
              );
            }
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `big-tab ${isActive ? "active animated-gradient-border" : ""}`}
              >
                <Icon size={24} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
          </nav>
          <ErrorBanner code={readinessError} />
          {/* readinessError holds a raw code/detail string from the catch in
              refreshReadiness; ErrorBanner translates it (or renders null). */}
        </section>
        <header className="topbar">
          <div>
            <span className="eyebrow">Coaching workspace</span>
            <h1>{isSetupComplete ? "Ready to rehearse." : "Set up your interview kit."}</h1>
          </div>
        </header>
        <Outlet context={{ refreshReadiness, isSetupComplete }} />
      </main>
    </div>
  );
}
