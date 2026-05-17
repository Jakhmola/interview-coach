import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api, JobItem } from "../api";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Active-job indicator, rendered in the sidebar footer.
 * Click opens a switcher dropdown listing other JDs.
 *
 * Job list is loaded eagerly (not just on open) so the chip knows
 * whether opening a dropdown is even useful. With zero jobs, the chip
 * becomes a direct "Go to Setup" affordance instead of opening an
 * empty menu.
 */
export function ActiveJobChip() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { activeJob, activeJobId, setActiveJobId, refresh } = useActiveJob();

  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const wrapRef = useRef<HTMLDivElement | null>(null);

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

  // Eager load — we need the job count to decide chip behavior.
  // Refetch when activeJobId changes so newly saved JDs appear in the menu.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .listJobs(token)
      .then((j) => {
        if (!cancelled) setJobs(j);
      })
      .catch(() => {
        /* swallow — chip stays usable */
      });
    return () => {
      cancelled = true;
    };
  }, [token, activeJobId]);

  const parsed = activeJob?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined;
  const role = parsed?.title;
  const company = parsed?.company_name;

  const muted = !activeJobId;
  const hasOtherJobs = jobs.some((j) => j.id !== activeJobId);
  // Dropdown is only useful if there's something to switch to OR an
  // active job to clear. Otherwise click should route to Setup.
  const dropdownUseful = hasOtherJobs || !!activeJobId;

  const onPillClick = () => {
    if (dropdownUseful) {
      setOpen((x) => !x);
    } else {
      navigate("/setup");
    }
  };

  return (
    <div className="active-job" ref={wrapRef}>
      <button
        type="button"
        className={`active-job-pill${muted ? " muted" : ""}`}
        onClick={onPillClick}
        aria-haspopup={dropdownUseful ? "listbox" : undefined}
        aria-expanded={dropdownUseful ? open : undefined}
        title={
          dropdownUseful
            ? "Switch active job"
            : "Add a job description to start"
        }
      >
        {muted ? (
          <span className="active-job-value muted">
            {jobs.length === 0 ? "Add a job →" : "No job selected"}
          </span>
        ) : (
          <span className="active-job-value">
            <span className="active-job-role">{role || "(role TBD)"}</span>
            <span className="active-job-company">{company || "(company TBD)"}</span>
          </span>
        )}
        {dropdownUseful ? (
          <ChevronDown size={14} className={`active-job-caret${open ? " open" : ""}`} />
        ) : null}
      </button>

      {open && dropdownUseful ? (
        <div className="active-job-menu" role="listbox">
          {jobs.map((j) => {
            const fallback = j.source_url ? j.source_url.slice(0, 56) : "Pasted JD";
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
                <span className="active-job-menu-item-label">{fallback}</span>
                <span className="active-job-menu-item-date">{date}</span>
              </button>
            );
          })}
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
