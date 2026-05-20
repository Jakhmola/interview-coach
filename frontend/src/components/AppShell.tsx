import { History, LogOut, Mic2, Settings2, Sparkles } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";
import { ActiveJobChip } from "./ActiveJobChip";
import { ErrorBanner } from "./ui";

const navItems = [
  { to: "/setup", label: "Setup", icon: Settings2 },
  { to: "/interview", label: "Practice", icon: Mic2 },
  { to: "/history", label: "History", icon: History },
];

export function AppShell() {
  const { token, user, logout } = useAuth();
  const { activeJobId } = useActiveJob();
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
      // Phase 25 (B15): completeness is per-active-job. Pre-Phase-25
      // this asked "does the user have *any* ready job?" — once a
      // user had one ready job, switching to a brand-new un-prepped
      // job let them navigate to /interview, which then errored on
      // missing context. Now we gate on the active job's own status,
      // falling back to "any can_start" only when no job is active
      // (first-time-user case before they pick one).
      const jobs = await api.listJobs(token);
      if (activeJobId) {
        const status = await api.prepStatus(token, activeJobId).catch(() => null);
        setIsSetupComplete(Boolean(status?.can_start));
      } else {
        const statuses = await Promise.all(
          jobs.map((job) => api.prepStatus(token, job.id).catch(() => null)),
        );
        setIsSetupComplete(statuses.some((status) => status?.can_start));
      }
    } catch (err) {
      setReadinessError(err instanceof ApiError ? err.detail : "Could not check setup readiness.");
      setIsSetupComplete(false);
    } finally {
      setHasCheckedReadiness(true);
    }
  }, [token, activeJobId]);

  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  useEffect(() => {
    if (hasCheckedReadiness && !isSetupComplete && location.pathname !== "/setup") {
      navigate("/setup", { replace: true });
    }
  }, [hasCheckedReadiness, isSetupComplete, location.pathname, navigate]);

  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Primary navigation">
        <div className="sidebar-brand">
          <span className="sidebar-brand-mark">
            <Sparkles size={16} />
          </span>
          <span className="sidebar-brand-name">Interview Coach</span>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            const locked = item.to !== "/setup" && hasCheckedReadiness && !isSetupComplete;
            if (locked) {
              return (
                <button
                  key={item.to}
                  type="button"
                  className="sidebar-nav-item locked"
                  disabled
                  title="Complete setup first"
                >
                  <Icon size={16} />
                  <span>{item.label}</span>
                </button>
              );
            }
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `sidebar-nav-item${isActive ? " active" : ""}`
                }
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <ActiveJobChip />
          <div className="sidebar-account">
            <span className="sidebar-account-email" title={user?.email}>
              {user?.email}
            </span>
            <button
              type="button"
              className="sidebar-account-logout"
              onClick={logout}
              title="Log out"
              aria-label="Log out"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </aside>

      <main className="canvas">
        <ErrorBanner code={readinessError} />
        <Outlet context={{ refreshReadiness, isSetupComplete }} />
      </main>
    </div>
  );
}
