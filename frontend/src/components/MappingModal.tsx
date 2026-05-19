import { useEffect, useMemo, useState } from "react";
import { Check as CheckIcon, FileText, SkipForward, X } from "lucide-react";

import {
  DocIntakeExtracted,
  DocIntakeSuggestion,
  MappingRow,
  MappingSuggestion,
} from "../api";

// ─────────────────────────── Selection helpers ──────────────────────────
//
// Identical between the prep-graph HITL pause and the out-of-graph remap
// flow used by Manage. Lives in one component so the two surfaces share
// vocabulary — nothing diverges silently.

export type Selection =
  | { kind: "highlight"; experienceIdx: number; highlightIdx: number }
  | { kind: "experience"; experienceIdx: number }
  | { kind: "project" };

export function selectionKey(sel: Selection): string {
  if (sel.kind === "highlight") return `H:${sel.experienceIdx}:${sel.highlightIdx}`;
  if (sel.kind === "experience") return `E:${sel.experienceIdx}`;
  return "P";
}

export function toMappingRow(sel: Selection): MappingRow {
  if (sel.kind === "highlight") {
    return {
      mapping_kind: "highlight",
      experience_idx: sel.experienceIdx,
      highlight_idx: sel.highlightIdx,
    };
  }
  if (sel.kind === "experience") {
    return { mapping_kind: "experience", experience_idx: sel.experienceIdx };
  }
  return { mapping_kind: "project" };
}

export function suggestionToSelection(s: DocIntakeSuggestion): Selection | null {
  if (s.mapping_kind === "highlight" && s.experience_idx != null && s.highlight_idx != null) {
    return { kind: "highlight", experienceIdx: s.experience_idx, highlightIdx: s.highlight_idx };
  }
  if (s.mapping_kind === "experience" && s.experience_idx != null) {
    return { kind: "experience", experienceIdx: s.experience_idx };
  }
  if (s.mapping_kind === "project") return { kind: "project" };
  return null;
}

export type MappingDecision =
  | { action: "apply"; rows: MappingRow[]; title: string; extracted: DocIntakeExtracted }
  | { action: "skip" };

/**
 * Phase 22: mapping HITL is a *modal*, used identically on Setup (during
 * prep_graph's HITL pause) and Manage (out-of-graph remap via
 * ``POST /documents/{id}/remap``). The two surfaces share one visual
 * vocabulary so the "where does this fit?" choice always feels the same.
 *
 * Wider than inline because real CVs have many experience highlights and
 * the user needs to scan them comfortably. Backdrop + ESC are wired to
 * ``onClose``; the parent decides whether closing equals "skip this doc"
 * (Setup, where escaping IS the skip) or "just close, leave the mapping
 * alone" (Manage's Remap is opt-in).
 */
export function MappingModal({
  open,
  suggestion,
  busy,
  onDecision,
  onClose,
}: {
  open: boolean;
  suggestion: MappingSuggestion | null;
  busy: boolean;
  onDecision: (d: MappingDecision) => void;
  /** Called on backdrop click + ESC. Parent decides what that means. */
  onClose: () => void;
}) {
  // Body scroll-lock while the modal is open — reinforces the "do this
  // first" contract the user already implicitly accepted by clicking
  // through to the mapping step / Remap button.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  // ESC closes (matches OS-level dialog convention).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open || !suggestion) return null;

  return (
    <div
      className="mapping-modal-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="mapping-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mapping-modal-title"
      >
        <MappingModalBody
          suggestion={suggestion}
          busy={busy}
          onDecision={onDecision}
          onClose={onClose}
        />
      </div>
    </div>
  );
}

function MappingModalBody({
  suggestion,
  busy,
  onDecision,
  onClose,
}: {
  suggestion: MappingSuggestion;
  busy: boolean;
  onDecision: (d: MappingDecision) => void;
  onClose: () => void;
}) {
  const [title, setTitle] = useState(suggestion.title);
  const [selections, setSelections] = useState<Map<string, Selection>>(() =>
    seedSelections(suggestion),
  );

  // Re-seed when the suggestion changes (prep_graph loops to the next
  // unmapped doc, or Manage opens remap for a different doc).
  useEffect(() => {
    setTitle(suggestion.title);
    setSelections(seedSelections(suggestion));
  }, [suggestion.document_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const topSuggestionKeys = useMemo(() => {
    const set = new Set<string>();
    for (const s of suggestion.suggestions) {
      const sel = suggestionToSelection(s);
      if (sel) set.add(selectionKey(sel));
    }
    return set;
  }, [suggestion]);

  const toggle = (sel: Selection) => {
    setSelections((prev) => {
      const next = new Map(prev);
      const key = selectionKey(sel);
      if (next.has(key)) next.delete(key);
      else next.set(key, sel);
      return next;
    });
  };

  const apply = () => {
    if (selections.size === 0) return;
    onDecision({
      action: "apply",
      rows: [...selections.values()].map(toMappingRow),
      title: title.trim() || suggestion.title,
      extracted: suggestion.extracted,
    });
  };

  const skip = () => onDecision({ action: "skip" });

  return (
    <>
      <header className="mapping-modal-header">
        <div>
          <span className="eyebrow">Project doc · {suggestion.remaining} remaining</span>
          <h2 id="mapping-modal-title">Where does this fit?</h2>
          <p className="wizard-blurb">
            We pulled out what looks like a project. Confirm the target on your CV (or skip if
            it doesn't belong anywhere).
          </p>
        </div>
        <button
          type="button"
          className="mapping-modal-close"
          aria-label="Close"
          onClick={onClose}
          disabled={busy}
        >
          <X size={16} />
        </button>
      </header>

      <div className="mapping-modal-body">
        <label className="wizard-form">
          <span className="wizard-label">Project title</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={160}
            disabled={busy}
          />
        </label>

        {suggestion.preview ? (
          <details className="mapping-modal-preview">
            <summary>
              <FileText size={14} />
              Preview source document
            </summary>
            <div className="mapping-modal-preview-body">
              {suggestion.preview}
              {suggestion.preview.length >= 1500 ? (
                <div className="mapping-modal-preview-truncated">
                  …showing the first {suggestion.preview.length.toLocaleString()} characters.
                </div>
              ) : null}
            </div>
          </details>
        ) : null}

        <div className="wizard-mapping-extracted">
          <strong>Extracted</strong>
          {suggestion.extracted.tech_stack.length > 0 ? (
            <div className="wizard-chip-row">
              {suggestion.extracted.tech_stack.map((t) => (
                <span key={t} className="wizard-chip">
                  {t}
                </span>
              ))}
            </div>
          ) : null}
          {suggestion.extracted.description ? (
            <p className="wizard-mapping-desc">{suggestion.extracted.description}</p>
          ) : null}
          {suggestion.extracted.urls.length > 0 ? (
            <ul className="wizard-url-list">
              {suggestion.extracted.urls.map((u) => (
                <li key={u}>
                  <a href={u} target="_blank" rel="noreferrer">
                    {u}
                  </a>
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        <div className="wizard-mapping-targets">
          <strong>Maps to</strong>
          {suggestion.experiences.length === 0 ? (
            <p className="wizard-blurb">
              Your CV had no experiences extracted. Save as a standalone project?
            </p>
          ) : (
            suggestion.experiences.map((exp) => (
              <article key={exp.experience_idx} className="wizard-mapping-exp">
                <header>
                  <strong>{exp.role || "Role"}</strong> @ {exp.company || "Company"}
                </header>
                <label className="wizard-checkbox-row">
                  <input
                    type="checkbox"
                    checked={selections.has(
                      selectionKey({ kind: "experience", experienceIdx: exp.experience_idx }),
                    )}
                    onChange={() =>
                      toggle({ kind: "experience", experienceIdx: exp.experience_idx })
                    }
                    disabled={busy}
                  />
                  <span>
                    All highlights at this role
                    {topSuggestionKeys.has(
                      selectionKey({ kind: "experience", experienceIdx: exp.experience_idx }),
                    ) ? (
                      <em className="wizard-suggested-tag"> suggested</em>
                    ) : null}
                  </span>
                </label>
                {exp.highlights.map((hl) => {
                  const sel: Selection = {
                    kind: "highlight",
                    experienceIdx: exp.experience_idx,
                    highlightIdx: hl.highlight_idx,
                  };
                  const key = selectionKey(sel);
                  return (
                    <label
                      key={hl.highlight_idx}
                      className="wizard-checkbox-row wizard-checkbox-row--indent"
                    >
                      <input
                        type="checkbox"
                        checked={selections.has(key)}
                        onChange={() => toggle(sel)}
                        disabled={busy}
                      />
                      <span>
                        {hl.text}
                        {topSuggestionKeys.has(key) ? (
                          <em className="wizard-suggested-tag"> suggested</em>
                        ) : null}
                      </span>
                    </label>
                  );
                })}
              </article>
            ))
          )}
          <label className="wizard-checkbox-row wizard-checkbox-row--standalone">
            <input
              type="checkbox"
              checked={selections.has("P")}
              onChange={() => toggle({ kind: "project" })}
              disabled={busy}
            />
            <span>
              Save as a standalone project
              {topSuggestionKeys.has("P") ? (
                <em className="wizard-suggested-tag"> suggested</em>
              ) : null}
            </span>
          </label>
        </div>
      </div>

      <footer className="mapping-modal-footer">
        <button className="btn-ghost" type="button" onClick={skip} disabled={busy}>
          <SkipForward size={14} /> Skip this doc
        </button>
        <button
          className="btn-primary"
          type="button"
          onClick={apply}
          disabled={busy || selections.size === 0}
        >
          <CheckIcon size={14} /> Confirm mapping
        </button>
      </footer>
    </>
  );
}

function seedSelections(suggestion: MappingSuggestion): Map<string, Selection> {
  const seed = new Map<string, Selection>();
  const top = [...suggestion.suggestions].sort((a, b) => b.confidence - a.confidence).slice(0, 1);
  for (const s of top) {
    const sel = suggestionToSelection(s);
    if (sel) seed.set(selectionKey(sel), sel);
  }
  return seed;
}
