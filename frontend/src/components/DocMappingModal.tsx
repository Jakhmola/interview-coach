import { useEffect, useMemo, useState } from "react";
import { RefreshCw, X } from "lucide-react";

import {
  ApiError,
  DocIntakeSuggestion,
  MappingRow,
  MappingSuggestion,
  api,
} from "../api";
import { codeFrom } from "../errors";
import { ErrorBanner } from "./ui";

// Backend returns these as the 409 detail when profile_builder hasn't run.
const PROFILE_NOT_READY_DETAILS = new Set([
  "upload your CV and build a profile before adding project docs",
  "apply_mapping needs a profile; upload your CV first",
  "No profile yet",
]);

function isProfileNotReady(err: unknown): boolean {
  if (err instanceof ApiError && err.status === 409) {
    return PROFILE_NOT_READY_DETAILS.has(err.detail);
  }
  return false;
}

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
  // F1 fix: when the backend returns 409 ProfileMissing, we show a
  // recoverable panel instead of a dead-end error. Caller (Setup) also
  // polls prepare/status and shouldn't even open us pre-profile, but if
  // the user navigated in via a stale state we still degrade gracefully.
  const [profileNotReady, setProfileNotReady] = useState(false);
  const [loadKey, setLoadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setError(null);
    setProfileNotReady(false);
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
        if (isProfileNotReady(err)) {
          setProfileNotReady(true);
        } else {
          setError(codeFrom(err));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, documentId, loadKey]);

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
      if (isProfileNotReady(err)) {
        setProfileNotReady(true);
        setSuggestion(null);
      } else {
        setError(codeFrom(err));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const retry = () => {
    setLoadKey((k) => k + 1);
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
        <ErrorBanner code={error} />

        {profileNotReady ? (
          <div className="deferred-mapping-card" style={{ marginTop: 12 }}>
            <RefreshCw size={16} className="spin" />
            <div>
              <strong>Profile is still building.</strong>
              <span>
                {" "}
                Mapping needs your profile to be ready first — usually 15–30s after CV upload.
              </span>
            </div>
          </div>
        ) : null}

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
          {profileNotReady ? (
            <button className="primary-button" onClick={retry} disabled={isLoading}>
              <RefreshCw size={16} />
              Retry
            </button>
          ) : (
            <button
              className="primary-button"
              onClick={apply}
              disabled={isSubmitting || isLoading || selections.size === 0}
            >
              {isSubmitting ? "Saving..." : `Save ${selections.size || ""} mapping${selections.size === 1 ? "" : "s"}`.trim()}
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
