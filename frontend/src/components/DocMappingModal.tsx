import { useEffect, useMemo, useState } from "react";
import { X } from "lucide-react";

import {
  ApiError,
  DocIntakeSuggestion,
  MappingRow,
  MappingSuggestion,
  api,
} from "../api";

type Selection =
  | { kind: "highlight"; experienceIdx: number; highlightIdx: number }
  | { kind: "experience"; experienceIdx: number }
  | { kind: "project" };

function selectionKey(sel: Selection): string {
  if (sel.kind === "highlight") {
    return `H:${sel.experienceIdx}:${sel.highlightIdx}`;
  }
  if (sel.kind === "experience") {
    return `E:${sel.experienceIdx}`;
  }
  return "P";
}

function toMappingRow(sel: Selection): MappingRow {
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

function suggestionToSelection(s: DocIntakeSuggestion): Selection | null {
  if (s.mapping_kind === "highlight" && s.experience_idx != null && s.highlight_idx != null) {
    return { kind: "highlight", experienceIdx: s.experience_idx, highlightIdx: s.highlight_idx };
  }
  if (s.mapping_kind === "experience" && s.experience_idx != null) {
    return { kind: "experience", experienceIdx: s.experience_idx };
  }
  if (s.mapping_kind === "project") {
    return { kind: "project" };
  }
  return null;
}

export function DocMappingModal({
  token,
  documentId,
  onClose,
  onApplied,
}: {
  token: string;
  documentId: string;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [suggestion, setSuggestion] = useState<MappingSuggestion | null>(null);
  const [title, setTitle] = useState("");
  const [selections, setSelections] = useState<Map<string, Selection>>(new Map());
  const [isLoading, setIsLoading] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    api
      .getMappingSuggestion(token, documentId)
      .then((data) => {
        if (cancelled) return;
        setSuggestion(data);
        setTitle(data.title);
        const next = new Map<string, Selection>();
        const top = [...data.suggestions]
          .sort((a, b) => b.confidence - a.confidence)
          .slice(0, 1);
        for (const sug of top) {
          const sel = suggestionToSelection(sug);
          if (sel) {
            next.set(selectionKey(sel), sel);
          }
        }
        setSelections(next);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.detail : "Could not load mapping suggestion.");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, documentId]);

  const topSuggestionKeys = useMemo(() => {
    if (!suggestion) return new Set<string>();
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
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.set(key, sel);
      }
      return next;
    });
  };

  const apply = async () => {
    if (!suggestion || selections.size === 0) {
      setError("Pick at least one mapping target.");
      return;
    }
    setIsSubmitting(true);
    setError(null);
    try {
      await api.postDocumentMapping(token, documentId, {
        title: title.trim() || suggestion.title,
        rows: [...selections.values()].map(toMappingRow),
        extracted: suggestion.extracted,
      });
      onApplied();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not save mapping.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-panel">
        <header className="modal-header">
          <div>
            <span className="eyebrow">Project doc</span>
            <h2>Where does this doc fit?</h2>
          </div>
          <button className="icon-button" onClick={onClose} title="Close" aria-label="Close">
            <X size={18} />
          </button>
        </header>

        {isLoading ? <p>Reading the doc and matching to your profile...</p> : null}
        {error ? <div className="error-banner">{error}</div> : null}

        {suggestion ? (
          <div className="modal-body">
            <label className="form-stack">
              Project title
              <input
                type="text"
                value={title}
                maxLength={160}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>

            <details className="modal-preview">
              <summary>Preview of document</summary>
              <pre>{suggestion.preview}</pre>
            </details>

            {suggestion.extracted.tech_stack.length > 0 ||
            suggestion.extracted.urls.length > 0 ||
            suggestion.extracted.description ? (
              <section className="extracted-block">
                <strong>Extracted</strong>
                {suggestion.extracted.description ? (
                  <p>{suggestion.extracted.description}</p>
                ) : null}
                {suggestion.extracted.tech_stack.length > 0 ? (
                  <div className="chip-row">
                    {suggestion.extracted.tech_stack.map((t) => (
                      <span key={t} className="chip">
                        {t}
                      </span>
                    ))}
                  </div>
                ) : null}
                {suggestion.extracted.urls.length > 0 ? (
                  <ul className="url-list">
                    {suggestion.extracted.urls.map((u) => (
                      <li key={u}>
                        <a href={u} target="_blank" rel="noreferrer">
                          {u}
                        </a>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </section>
            ) : null}

            <section>
              <strong>Map to one or more targets</strong>
              <p className="hint">
                Suggested matches are pre-selected. Multi-select is allowed — a doc may attach to
                highlights across different companies, or also be a standalone project.
              </p>
              <div className="mapping-tree">
                {suggestion.experiences.length === 0 ? (
                  <p className="hint">
                    No experience rows in your profile yet — upload a CV first or use the standalone
                    project option below.
                  </p>
                ) : (
                  suggestion.experiences.map((exp) => {
                    const expSel: Selection = {
                      kind: "experience",
                      experienceIdx: exp.experience_idx,
                    };
                    const expKey = selectionKey(expSel);
                    return (
                      <article key={exp.experience_idx} className="mapping-exp">
                        <header>
                          <strong>
                            {exp.role}
                            {exp.role && exp.company ? " · " : ""}
                            {exp.company}
                          </strong>
                          <label className="checkbox-row">
                            <input
                              type="checkbox"
                              checked={selections.has(expKey)}
                              onChange={() => toggle(expSel)}
                            />
                            <span>
                              Associate with {exp.company || "this company"} (no specific bullet)
                              {topSuggestionKeys.has(expKey) ? (
                                <em className="suggested-tag"> suggested</em>
                              ) : null}
                            </span>
                          </label>
                        </header>
                        <ul>
                          {exp.highlights.length === 0 ? (
                            <li className="hint">(no highlights on this experience)</li>
                          ) : (
                            exp.highlights.map((hl) => {
                              const hlSel: Selection = {
                                kind: "highlight",
                                experienceIdx: exp.experience_idx,
                                highlightIdx: hl.highlight_idx,
                              };
                              const hlKey = selectionKey(hlSel);
                              return (
                                <li key={hl.highlight_idx}>
                                  <label className="checkbox-row">
                                    <input
                                      type="checkbox"
                                      checked={selections.has(hlKey)}
                                      onChange={() => toggle(hlSel)}
                                    />
                                    <span>
                                      {hl.text}
                                      {topSuggestionKeys.has(hlKey) ? (
                                        <em className="suggested-tag"> suggested</em>
                                      ) : null}
                                    </span>
                                  </label>
                                </li>
                              );
                            })
                          )}
                        </ul>
                      </article>
                    );
                  })
                )}
                <div className="mapping-standalone">
                  <label className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={selections.has(selectionKey({ kind: "project" }))}
                      onChange={() => toggle({ kind: "project" })}
                    />
                    <span>
                      Standalone project (personal, OSS, hackathon)
                      {topSuggestionKeys.has(selectionKey({ kind: "project" })) ? (
                        <em className="suggested-tag"> suggested</em>
                      ) : null}
                    </span>
                  </label>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        <footer className="modal-footer">
          <button className="ghost-button" onClick={onClose} disabled={isSubmitting}>
            Cancel
          </button>
          <button
            className="primary-button"
            onClick={apply}
            disabled={isSubmitting || isLoading || selections.size === 0}
          >
            {isSubmitting ? "Saving..." : `Save ${selections.size || ""} mapping${selections.size === 1 ? "" : "s"}`.trim()}
          </button>
        </footer>
      </div>
    </div>
  );
}
