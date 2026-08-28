import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Check as CheckIcon, GitBranch, SkipForward, Star } from "lucide-react";

import { RepoListing } from "../api";

/**
 * Phase 32: the GitHub repo-selection HITL. The prep_graph pauses after
 * profile_builder, emits ``repos_available``, and this modal floats above the
 * wizard so the user can pick which public repos to ingest into grounding +
 * their profile. Mirrors MappingModal's modal shell + backdrop/ESC contract;
 * closing equals "select none" (the user's escape hatch), which deselects
 * everything previously ingested.
 *
 * The picker is deliberately coarse - name, description, language, stars,
 * last-pushed, archived flag, CV-mention pre-check, and a search box. No
 * JD/tech ranking here: the rich signal doesn't exist pre-ingestion (focus
 * weighting handles relevance downstream).
 */
export function RepoSelectModal({
  open,
  repos,
  busy,
  onSubmit,
  onClose,
  closeLabel = "Skip - no repos",
}: {
  open: boolean;
  repos: RepoListing[] | null;
  busy: boolean;
  onSubmit: (selectedUrls: string[]) => void;
  /** Backdrop click + ESC + the footer's left button. In setup this means
   * "select none"; on Manage it means "cancel" (no change). */
  onClose: () => void;
  /** Footer left-button text. Default suits the setup HITL; Manage passes
   * "Cancel" since closing there discards rather than deselects. */
  closeLabel?: string;
}) {
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open || !repos) return null;

  return (
    <div
      className="mapping-modal-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="mapping-modal wide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="repo-modal-title"
      >
        <RepoSelectBody
          repos={repos}
          busy={busy}
          onSubmit={onSubmit}
          onClose={onClose}
          closeLabel={closeLabel}
        />
      </div>
    </div>
  );
}

/** "Jan 2023" - a repo's last push; the day would be noise next to years-old repos. */
function monthYear(value: string) {
  return new Date(value).toLocaleDateString(undefined, { month: "short", year: "numeric" });
}

function RepoSelectBody({
  repos,
  busy,
  onSubmit,
  onClose,
  closeLabel,
}: {
  repos: RepoListing[];
  busy: boolean;
  onSubmit: (selectedUrls: string[]) => void;
  onClose: () => void;
  closeLabel: string;
}) {
  // Pre-check CV-mentioned repos (setup), already-ingested ones (Manage / the
  // user's prior selection), and any that failed to ingest - so a retry
  // resubmits the same set; unchecking a failed repo skips it instead.
  const [selected, setSelected] = useState<Set<string>>(
    () =>
      new Set(
        repos
          .filter((r) => r.cv_mentioned || r.already_ingested || r.ingest_error)
          .map((r) => r.html_url),
      ),
  );
  const [query, setQuery] = useState("");

  // Follow-up 3: when prep re-opens the picker after an ingest failure, switch
  // the framing from "pick repos" to "fix the broken ones".
  const hasErrors = repos.some((r) => r.ingest_error);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter(
      (r) =>
        r.full_name.toLowerCase().includes(q) ||
        (r.description ?? "").toLowerCase().includes(q) ||
        (r.language ?? "").toLowerCase().includes(q),
    );
  }, [repos, query]);

  const toggle = (url: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  return (
    <>
      <header className="mapping-modal-header">
        <div>
          <span className="eyebrow">
            {hasErrors ? (
              <>
                <AlertTriangle size={13} /> Some repos couldn&apos;t be ingested
              </>
            ) : (
              <>
                <GitBranch size={13} /> {repos.length} public repo{repos.length === 1 ? "" : "s"}
              </>
            )}
          </span>
          <h2 id="repo-modal-title">{hasErrors ? "Retry or skip the failed repos" : "Pick repos to include"}</h2>
          <p className="wizard-blurb">
            {hasErrors
              ? "We can't finish setup until these are resolved. Keep a repo checked to retry it, or uncheck it to skip it and continue without it."
              : "We'll read the README, dependency manifests and directory layout of each repo you select, and add it to your profile as a project. Already-included repos start checked; unchecking one removes it."}
          </p>
        </div>
      </header>

      <div className="mapping-modal-body repo-body">
        <label className="wizard-form">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by name, description, or language…"
            disabled={busy}
          />
        </label>

        <div className="repo-list">
          {filtered.length === 0 ? (
            <p className="wizard-blurb">No repos match your filter.</p>
          ) : (
            filtered.map((r) => (
              <label
                key={r.html_url}
                className={selected.has(r.html_url) ? "repo-row checked" : "repo-row"}
              >
                <input
                  type="checkbox"
                  checked={selected.has(r.html_url)}
                  onChange={() => toggle(r.html_url)}
                  disabled={busy}
                />
                <span className="repo-main">
                  <strong>{r.full_name}</strong>
                  {r.cv_mentioned ? <em className="wizard-suggested-tag"> on your CV</em> : null}
                  {r.archived ? <em className="wizard-suggested-tag"> archived</em> : null}
                  {r.description ? <span className="repo-desc clamp">{r.description}</span> : null}
                  {r.ingest_error ? (
                    <span className="repo-desc repo-err">
                      <AlertTriangle size={11} /> {r.ingest_error.step} step failed -{" "}
                      {r.ingest_error.reason}
                    </span>
                  ) : null}
                </span>
                <span className="repo-side">
                  <span className="wizard-chip-row">
                    {r.language ? <span className="wizard-chip">{r.language}</span> : null}
                    {r.stars > 0 ? (
                      <span className="wizard-chip">
                        <Star size={11} /> {r.stars}
                      </span>
                    ) : null}
                  </span>
                  {r.pushed_at ? <span className="repo-pushed">Pushed {monthYear(r.pushed_at)}</span> : null}
                </span>
              </label>
            ))
          )}
        </div>
      </div>

      <footer className="mapping-modal-footer">
        <button className="btn-ghost" type="button" onClick={onClose} disabled={busy}>
          <SkipForward size={14} /> {closeLabel}
        </button>
        <button
          className="btn-primary"
          type="button"
          onClick={() => onSubmit([...selected])}
          disabled={busy}
        >
          <CheckIcon size={14} />{" "}
          {selected.size === 0
            ? "Continue without repos"
            : hasErrors
              ? `Retry ${selected.size} repo${selected.size === 1 ? "" : "s"}`
              : `Include ${selected.size} repo${selected.size === 1 ? "" : "s"}`}
        </button>
      </footer>
    </>
  );
}
