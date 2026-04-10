import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";

interface AppShellProps {
  children: ReactNode;
}

const navigation = [
  { to: "/runs", label: "Runs" },
  { to: "/jobs", label: "Jobs" }
];

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="app-shell">
      <div className="app-shell__backdrop" />
      <header className="app-header">
        <div>
          <p className="eyebrow">Operator Console</p>
          <h1>Workflow Dashboard</h1>
          <p className="lede">
            Track run health, watch discovery/application progress, and review the
            resume attached to each job.
          </p>
        </div>
        <nav className="top-nav" aria-label="Primary">
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              className={({ isActive }) =>
                isActive ? "top-nav__link top-nav__link--active" : "top-nav__link"
              }
              to={item.to}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
