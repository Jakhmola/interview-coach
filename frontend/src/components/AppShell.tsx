import { BookOpenCheck, FileText, History, LogOut, Mic2, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../state/auth";

const navItems = [
  { to: "/setup", label: "Setup", icon: FileText },
  { to: "/interview", label: "Interview", icon: Mic2 },
  { to: "/history", label: "History", icon: History },
];

export function AppShell() {
  const { user, logout } = useAuth();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Sparkles size={20} />
          </span>
          <div>
            <strong>Interview Coach</strong>
            <span>Practice studio</span>
          </div>
        </div>
        <nav className="nav-list">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink key={item.to} to={item.to} className="nav-link">
                <Icon size={18} />
                {item.label}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-card">
          <BookOpenCheck size={18} />
          <span>Warm up with a prepared job before starting a round.</span>
        </div>
      </aside>
      <main className="main-panel">
        <header className="topbar">
          <div>
            <span className="eyebrow">Coaching workspace</span>
            <h1>Prepare, rehearse, review.</h1>
          </div>
          <div className="user-menu">
            <span>{user?.email}</span>
            <button className="icon-button" onClick={logout} title="Log out">
              <LogOut size={18} />
            </button>
          </div>
        </header>
        <Outlet />
      </main>
    </div>
  );
}
