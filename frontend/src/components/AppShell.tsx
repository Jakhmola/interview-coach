import { LogOut } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";
import { viewTransition } from "../viewTransition";
import { ErrorBanner, Staples, StockToggle } from "./ui";

const navItems = [
  { to: "/setup", label: "Setup" },
  { to: "/interview", label: "Practice" },
  { to: "/history", label: "History" },
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
      // this asked "does the user have *any* ready job?" - once a
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
    <div className="desk">
      <div className="desk-bar">
        <nav className="tabs" aria-label="Primary navigation">
          {navItems.map((item) => {
            const locked = item.to !== "/setup" && hasCheckedReadiness && !isSetupComplete;
            if (locked) {
              return (
                <button
                  key={item.to}
                  type="button"
                  className="tab locked"
                  disabled
                  title="Complete setup first"
                >
                  {item.label}
                </button>
              );
            }
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `tab${isActive ? " active" : ""}`}
                onClick={(event) => {
                  // Plain left click: pull the next sheet with a view
                  // transition. Modified clicks keep the browser's behaviour.
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  if (event.button !== 0 || location.pathname === item.to) return;
                  event.preventDefault();
                  viewTransition(() => navigate(item.to), "route");
                }}
              >
                {item.label}
              </NavLink>
            );
          })}
        </nav>

        <div className="desk-tools">
          <span className="who" title={user?.email}>
            {user?.email}
          </span>
          <StockToggle />
          <button type="button" className="desk-btn" onClick={logout} title="Log out">
            <LogOut />
            <span>Log out</span>
          </button>
        </div>
      </div>

      <main className="sheet">
        <Staples />
        <ErrorBanner code={readinessError} />
        <Outlet context={{ refreshReadiness, isSetupComplete }} />
        <footer className="sheet-foot">Interview Coach · runs on your machine</footer>
      </main>
    </div>
  );
}
