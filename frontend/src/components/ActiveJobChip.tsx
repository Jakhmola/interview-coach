import { useEffect, useRef, useState } from "react";

import { api, JobItem } from "../api";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Persistent active-job indicator rendered in the AppShell topbar.
 * Shows `{role} @ {company}` derived from the JD's parsed_json, or
 * "No active job" when the user has no JDs yet.
 *
 * Click opens a dropdown listing other JDs for quick switching. The
 * dropdown also exposes a "Clear" item that wipes the active selection.
 */
export function ActiveJobChip() {
  const { token } = useAuth();
  const { activeJob, activeJobId, setActiveJobId, refresh } = useActiveJob();

  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!wrapRef.current) return;
      if (e.target instanceof Node && !wrapRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  // Fetch JDs lazily — only when the user opens the dropdown.
  useEffect(() => {
    if (!open || !token) return;
    let cancelled = false;
    api
      .listJobs(token)
      .then((j) => {
        if (!cancelled) setJobs(j);
      })
      .catch(() => {
        /* swallow — chip stays usable; just no switcher */
      });
    return () => {
      cancelled = true;
    };
  }, [open, token]);

  const parsed = activeJob?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined;
  const role = parsed?.title;
  const company = parsed?.company_name;

  const muted = !activeJobId;
  const text = muted
    ? "No active job"
    : `${role || "(role TBD)"} @ ${company || "(company TBD)"}`;

  return (
    <div className="active-job-chip-wrap" ref={wrapRef}>
      <button
        type="button"
        className={`active-job-chip${muted ? " muted" : ""}`}
        onClick={() => setOpen((x) => !x)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={muted ? undefined : "Switch active job"}
      >
        <span className="chip-label">Active job</span>
        <span className="chip-value">{text}</span>
      </button>

      {open ? (
        <div className="active-job-menu" role="listbox">
          {jobs.length === 0 ? (
            <div className="active-job-menu-empty">No saved JDs yet.</div>
          ) : (
            jobs.map((j) => {
              // listJobs returns JobListItem, which doesn't carry parsed_json
              // (the role+company we'd love to show). Use source_url or a
              // "Pasted JD" fallback until the user selects it and the full
              // detail loads via getJob in the context.
              const fallback = j.source_url
                ? j.source_url.slice(0, 60)
                : "Pasted JD";
              const date = new Date(j.created_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
              });
              const isCurrent = j.id === activeJobId;
              return (
                <button
                  key={j.id}
                  type="button"
                  role="option"
                  aria-selected={isCurrent}
                  className={`active-job-menu-item${isCurrent ? " current" : ""}`}
                  onClick={() => {
                    setActiveJobId(j.id);
                    setOpen(false);
                    void refresh();
                  }}
                >
                  <span>{fallback}</span>
                  <span className="active-job-menu-date">{date}</span>
                </button>
              );
            })
          )}
          {activeJobId ? (
            <button
              type="button"
              className="active-job-menu-item clear"
              onClick={() => {
                setActiveJobId(null);
                setOpen(false);
              }}
            >
              Clear active job
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
