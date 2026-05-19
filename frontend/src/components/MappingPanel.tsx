import { useEffect, useMemo, useState } from "react";
import { Check as CheckIcon, SkipForward } from "lucide-react";

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
 * Inline mapping HITL panel — used both at Stage 4 of the wizard
 * (prep_graph emits a ``mapping_suggestion`` SSE event) and from Manage
 * (``POST /documents/{id}/remap`` returns the same payload shape).
 *
 * Visual vocabulary matches the rest of the wizard (dropzone,
 * btn-primary, wizard-body) — there is no separate modal vocabulary.
 */
export function MappingPanel({
  suggestion,
  onDecision,
  disabled,
}: {
  suggestion: MappingSuggestion;
  onDecision: (d: MappingDecision) => void;
  disabled: boolean;
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
    <section className="wizard-mapping-panel">
      <header className="wizard-mapping-header">
        <div>
          <span className="eyebrow">Project doc · {suggestion.remaining} remaining</span>
          <h2>Where does this fit?</h2>
          <p className="wizard-blurb">
            We pulled out what looks like a project. Confirm the target on your CV (or skip if
            it doesn't belong anywhere).
          </p>
        </div>
      </header>

      <label className="wizard-form">
        <span className="wizard-label">Project title</span>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={160}
          disabled={disabled}
        />
      </label>

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
                  onChange={() => toggle({ kind: "experience", experienceIdx: exp.experience_idx })}
                  disabled={disabled}
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
                      disabled={disabled}
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
            disabled={disabled}
          />
          <span>
            Save as a standalone project
            {topSuggestionKeys.has("P") ? <em className="wizard-suggested-tag"> suggested</em> : null}
          </span>
        </label>
      </div>

      <div className="wizard-actions">
        <button
          className="btn-primary"
          type="button"
          onClick={apply}
          disabled={disabled || selections.size === 0}
        >
          <CheckIcon size={14} /> Confirm mapping
        </button>
        <button className="btn-ghost" type="button" onClick={skip} disabled={disabled}>
          <SkipForward size={14} /> Skip this doc
        </button>
      </div>
    </section>
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
