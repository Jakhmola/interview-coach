import { useEffect, useRef, useState } from "react";
import { ArrowRight, ChevronDown, Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { jobLabel } from "../jobLabel";
import { useActiveJob } from "../state/activeJob";

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
  const navigate = useNavigate();
  // Jobs list + active detail both live on ActiveJobContext. Switching the
  // active job only moves the id - the provider's effect pulls the matching
  // detail and a switch can't change the list - so the chip needs no refetch.
  const { activeJob, activeJobId, jobs, setActiveJobId } = useActiveJob();

  const [open, setOpen] = useState(false);
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

  // Prefer the detail copy (freshest after a re-analyze), fall back to
  // the list payload (already includes parsed_json post-Phase 22) so
  // the pill never has to show "(role TBD)" once the analyzer has run.
  const listMatch = jobs.find((j) => j.id === activeJobId) ?? null;
  const parsed =
    (activeJob?.parsed_json as { title?: string; company_name?: string } | null | undefined) ??
    (listMatch?.parsed_json as { title?: string; company_name?: string } | null | undefined);
  const role = parsed?.title;
  const company = parsed?.company_name;

  const muted = !activeJobId;
  // The slip lists every saved job plus "New job description", so it is
  // worth opening as soon as one job exists. With none, the chip itself is
  // the way into Setup.
  const dropdownUseful = jobs.length > 0;

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
            {jobs.length === 0 ? (
              <>
                Add a job <ArrowRight size={12} aria-hidden="true" />
              </>
            ) : (
              "No job selected"
            )}
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
            const label = jobLabel(j);
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
                }}
              >
                <span className="active-job-menu-item-label">{label}</span>
                <span className="active-job-menu-item-date">{date}</span>
              </button>
            );
          })}
          <button
            type="button"
            className="active-job-menu-item add"
            onClick={() => {
              setOpen(false);
              navigate("/setup?new_job=1");
            }}
          >
            <span className="active-job-menu-item-label">
              <Plus size={12} aria-hidden="true" /> New job description
            </span>
          </button>
        </div>
      ) : null}
    </div>
  );
}
