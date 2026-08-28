import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ExternalLink,
  FileUp,
  LinkIcon,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import {
  ApiError,
  DocumentItem,
  EmbeddingStatus,
  JobItem,
  MappingSuggestion,
  RepoListing,
  api,
} from "../api";
import { ArmedDeleteButton } from "../components/ArmedDeleteButton";
import { MappingModal, MappingDecision } from "../components/MappingModal";
import { RepoSelectModal } from "../components/RepoSelectModal";
import { ErrorBanner, Field, SheetHead, StatusPill, formatDate } from "../components/ui";
import { codeFrom } from "../errors";
import { excerptOf, jobLabel, jobSubtitle } from "../jobLabel";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Phase 22 - inventory editor for the user's CV, JDs, and supporting
 * docs. The setup wizard handles "the next thing to do"; Manage owns
 * "I want to change something I already gave you" - replace CV,
 * re-analyze a JD, remap or retry-embed a supporting doc, and the
 * structured 409 blocking-sessions card so the user isn't stranded
 * when a delete is gated.
 */

type BlockingState = {
  /** Which JD triggered the 409 - used to scope the card to that card.
   * (Phase 22 dropped Delete CV in favour of Reset account, so the only
   * delete that can return a structured 409 today is ``DELETE /jobs/{id}``.) */
  scope: { kind: "job"; id: string };
  code: "job_in_use";
  sessionIds: string[];
};

export function ManagePage() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  // Phase 22: read jobs from the shared ActiveJobContext so re-analyze
  // / delete / make-active mutations show up consistently across the
  // sidebar dropdown, Setup wizard, and this page.
  const { activeJobId, jobs, setActiveJobId, refresh: refreshActiveJob } = useActiveJob();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<BlockingState | null>(null);
  /** Which JD's inline editor is open (re-analyze paste/url). */
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  /** Which project_doc's inline remap panel is open. */
  const [remapping, setRemapping] = useState<{
    docId: string;
    suggestion: MappingSuggestion;
  } | null>(null);
  const replaceCvInputRef = useRef<HTMLInputElement | null>(null);

  const cv = docs.find((d) => d.kind === "cv");
  const techDocs = docs.filter((d) => d.kind === "project_doc");
  const githubRepos = docs.filter((d) => d.kind === "github_repo");

  const load = async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const [nextDocs] = await Promise.all([api.listDocuments(token), refreshActiveJob()]);
      setDocs(nextDocs);
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Drop the blocking card whenever the underlying inventory changes -
  // an abandon-and-retry sequence shouldn't leave a stale "still
  // blocked" card pinned after the second delete succeeds.
  useEffect(() => {
    if (!blocking) return;
    if (!jobs.find((j) => j.id === blocking.scope.id)) {
      setBlocking(null);
    }
  }, [jobs, blocking]);

  // ─── delete helpers ────────────────────────────────────────────────
  const captureJobBlocking = (err: unknown, jobId: string): boolean => {
    if (!(err instanceof ApiError) || err.status !== 409) return false;
    const obj = err.detailObject;
    if (!obj || typeof obj !== "object") return false;
    const code = (obj as { code?: unknown }).code;
    const ids = (obj as { blocking_session_ids?: unknown }).blocking_session_ids;
    if (code !== "job_in_use" || !Array.isArray(ids)) return false;
    setBlocking({
      scope: { kind: "job", id: jobId },
      code,
      sessionIds: ids.filter((x): x is string => typeof x === "string"),
    });
    return true;
  };

  const deleteDoc = async (id: string) => {
    if (!token) return;
    setError(null);
    try {
      await api.deleteDocument(token, id);
      setMessage("Deleted.");
      await load();
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  const deleteJob = async (id: string) => {
    if (!token) return;
    setError(null);
    try {
      await api.deleteJob(token, id);
      if (activeJobId === id) setActiveJobId(null);
      setMessage("Deleted.");
      await load();
    } catch (err) {
      if (captureJobBlocking(err, id)) return;
      setError(codeFrom(err));
    }
  };

  // ─── CV: replace + retry embed ─────────────────────────────────────
  const onReplaceCvPick = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!token || !event.target.files?.[0]) return;
    setError(null);
    setBusy("cv");
    try {
      await api.uploadDocument(token, "cv", event.target.files[0]);
      // The wizard's work-driven auto-prep picks the rebuild up once
      // we land back there - surface the new CV via /setup so the user
      // sees prep stream live rather than a silent inventory refresh.
      navigate("/setup");
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
      event.target.value = "";
    }
  };

  const retryEmbed = async (docId: string) => {
    if (!token) return;
    setBusy(docId);
    setError(null);
    try {
      await api.retryEmbed(token, docId);
      setMessage("Embedding re-scheduled.");
      await load();
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  // ─── JD: re-analyze ────────────────────────────────────────────────
  const submitReanalyze = async (jobId: string, body: { text?: string; url?: string }) => {
    if (!token) return;
    setError(null);
    setBusy(jobId);
    try {
      await api.patchJob(token, jobId, body);
      setEditingJobId(null);
      setActiveJobId(jobId);
      navigate("/setup");
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  // ─── Doc: remap ────────────────────────────────────────────────────
  const openRemap = async (docId: string) => {
    if (!token) return;
    setBusy(docId);
    setError(null);
    try {
      const suggestion = await api.startRemap(token, docId);
      setRemapping({ docId, suggestion });
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  const confirmRemap = async (decision: MappingDecision) => {
    if (!token || !remapping) return;
    setBusy(remapping.docId);
    setError(null);
    try {
      await api.confirmRemap(token, remapping.docId, decision);
      setMessage(decision.action === "apply" ? "Mapping saved." : "Skipped.");
      setRemapping(null);
      await load();
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  // ─── blocking-sessions card actions ────────────────────────────────
  const abandonSession = async (sessionId: string) => {
    if (!token || !blocking) return;
    setError(null);
    try {
      await api.abandonSession(token, sessionId);
      const remaining = blocking.sessionIds.filter((s) => s !== sessionId);
      if (remaining.length > 0) {
        setBlocking({ ...blocking, sessionIds: remaining });
        return;
      }
      // Last blocking session cleared - auto-retry the original delete.
      const scope = blocking.scope;
      setBlocking(null);
      await deleteJob(scope.id);
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  // ─── Danger zone: account reset ────────────────────────────────────
  const resetAccount = async (confirmEmail: string): Promise<void> => {
    if (!token || !user) return;
    setError(null);
    setBusy("reset");
    try {
      await api.resetAccount(token, confirmEmail);
      // Clear in-memory references to data that just got wiped server-side.
      setActiveJobId(null);
      setDocs([]);
      // Refresh the shared jobs list + reload local state so every page
      // sees the empty account in one tick.
      await refreshActiveJob();
      await load();
      setMessage("Account reset - everything is now empty.");
      // Drop the user back onto Setup so the onboarding wizard takes over.
      navigate("/setup");
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  // ─── derived UI bits ───────────────────────────────────────────────
  const mappedTechDocs = techDocs.filter((d) => d.project_title).length;

  return (
    <div className="manage-page">
      <SheetHead title="File inventory" page="CV · job descriptions · supporting docs · repos">
        <Field label="Candidate" value={user?.email} />
      </SheetHead>
      <header className="manage-header">
        <button className="btn-quiet" type="button" onClick={() => navigate("/setup")}>
          <ArrowLeft size={14} /> Back to setup
        </button>
        <h1>Everything on file for this account.</h1>
        <p>Replace, re-analyze, remap, or remove. Changes that rebuild your profile route back to Setup so prep streams live.</p>
      </header>

      {message ? <div className="success-banner">{message}</div> : null}
      <ErrorBanner code={error} />

      {isLoading ? <p className="muted">Loading…</p> : null}

      {/* ─── CV ───────────────────────────────────────────────────── */}
      <section className="manage-section">
        <h2>CV</h2>
        {cv ? (
          <>
            {mappedTechDocs > 0 ? (
              <p className="manage-warning">
                Replacing your CV rebuilds your profile and asks you to remap each of your{" "}
                {mappedTechDocs} supporting doc{mappedTechDocs === 1 ? "" : "s"} against the new
                experiences. Uploading the same file is a no-op.
              </p>
            ) : (
              <p className="manage-warning manage-warning--quiet">
                Replacing your CV rebuilds your profile from scratch. Uploading the same file is a
                no-op.
              </p>
            )}
            <div className="manage-card">
              <div>
                <strong>{cv.filename}</strong>
                <span className="muted">{cv.char_count.toLocaleString()} chars</span>
                {cv.embedding_status ? <EmbedPill status={cv.embedding_status} /> : null}
                <span className="excerpt clamp">{excerptOf(cv.preview, cv.char_count)}</span>
              </div>
              <div className="manage-card-actions">
                <input
                  ref={replaceCvInputRef}
                  type="file"
                  accept=".pdf,.docx"
                  hidden
                  onChange={onReplaceCvPick}
                />
                <button
                  className="btn-ghost"
                  type="button"
                  onClick={() => replaceCvInputRef.current?.click()}
                  disabled={busy === "cv"}
                >
                  <FileUp size={14} /> Replace CV
                </button>
                {cv.embedding_status === "failed" ? (
                  <button
                    className="btn-ghost"
                    type="button"
                    onClick={() => retryEmbed(cv.id)}
                    disabled={busy === cv.id}
                  >
                    <RefreshCw size={14} /> Retry embedding
                  </button>
                ) : null}
              </div>
            </div>
          </>
        ) : (
          <p className="muted">
            No CV on file. <Link to="/setup">Upload one</Link>.
          </p>
        )}
      </section>

      {/* ─── JDs ──────────────────────────────────────────────────── */}
      <section className="manage-section">
        <h2>Job descriptions</h2>
        {jobs.length === 0 ? (
          <p className="muted">No JDs yet.</p>
        ) : (
          <div className="manage-list">
            {jobs.map((j) => {
              const isEditing = editingJobId === j.id;
              const isBlocked =
                blocking?.scope.kind === "job" && blocking.scope.id === j.id;
              return (
                <div key={j.id}>
                  <div className="manage-card">
                    <div>
                      <strong>{jobLabel(j)}</strong>
                      <span className="muted">
                        {[jobSubtitle(j), `${j.char_count.toLocaleString()} chars`, formatDate(j.created_at)]
                          .filter(Boolean)
                          .join(" · ")}
                      </span>
                      {j.id === activeJobId ? <StatusPill tone="good">Active</StatusPill> : null}
                      <span className="excerpt clamp">{excerptOf(j.preview, j.char_count)}</span>
                    </div>
                    <div className="manage-card-actions">
                      {j.id !== activeJobId ? (
                        <button
                          className="btn-ghost"
                          type="button"
                          onClick={() => {
                            // Phase 25: setActiveJobId only swaps the id;
                            // the cached activeJob detail (which the
                            // sidebar chip reads first) stays stale until
                            // a refresh. Mirror the dropdown switcher so
                            // the chip flips to this job immediately.
                            setActiveJobId(j.id);
                            void refreshActiveJob();
                          }}
                        >
                          Make active
                        </button>
                      ) : null}
                      <button
                        className="btn-ghost"
                        type="button"
                        onClick={() => setEditingJobId(isEditing ? null : j.id)}
                        disabled={busy === j.id}
                      >
                        <Sparkles size={14} /> {isEditing ? "Cancel" : "Re-analyze"}
                      </button>
                      <ArmedDeleteButton
                        label="Delete"
                        icon={<Trash2 size={14} />}
                        onConfirm={() => deleteJob(j.id)}
                      />
                    </div>
                  </div>
                  {isEditing ? (
                    <JobEditor
                      job={j}
                      disabled={busy === j.id}
                      onSubmit={(body) => submitReanalyze(j.id, body)}
                    />
                  ) : null}
                  {isBlocked ? (
                    <BlockingSessionsCard
                      sessionIds={blocking!.sessionIds}
                      onAbandon={abandonSession}
                      onDismiss={() => setBlocking(null)}
                    />
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ─── Supporting docs ──────────────────────────────────────── */}
      <section className="manage-section">
        <h2>Supporting docs</h2>
        {techDocs.length === 0 ? (
          <p className="muted">No project docs.</p>
        ) : (
          <div className="manage-list">
            {techDocs.map((d) => {
              return (
                <div key={d.id}>
                  <div className="manage-card">
                    <div>
                      <strong>{d.filename}</strong>
                      <span className="muted">
                        {d.project_title ? `"${d.project_title}"` : "Unmapped"} ·{" "}
                        {d.char_count.toLocaleString()} chars
                      </span>
                      {d.embedding_status ? <EmbedPill status={d.embedding_status} /> : null}
                      <span className="excerpt clamp">{excerptOf(d.preview, d.char_count)}</span>
                    </div>
                    <div className="manage-card-actions">
                      <button
                        className="btn-ghost"
                        type="button"
                        onClick={() => void openRemap(d.id)}
                        disabled={busy === d.id || remapping != null}
                      >
                        <Sparkles size={14} /> Remap
                      </button>
                      {d.embedding_status === "failed" && d.project_title ? (
                        <button
                          className="btn-ghost"
                          type="button"
                          onClick={() => retryEmbed(d.id)}
                          disabled={busy === d.id}
                        >
                          <RefreshCw size={14} /> Retry embedding
                        </button>
                      ) : null}
                      <ArmedDeleteButton
                        label="Delete"
                        icon={<Trash2 size={14} />}
                        onConfirm={() => deleteDoc(d.id)}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ─── GitHub repos ─────────────────────────────────────────── */}
      <GithubReposSection
        token={token}
        repos={githubRepos}
        onDeleteDoc={deleteDoc}
        onChanged={load}
      />

      {/* ─── Danger zone ──────────────────────────────────────────── */}
      <DangerZone onReset={resetAccount} busy={busy === "reset"} />

      {/* Phase 22: remap HITL is the same modal used by Setup. Backdrop /
          ESC close the modal *without* mutating the mapping - on Manage,
          Remap is opt-in and the user can always re-open it. The dedicated
          Skip button still goes through ``confirmRemap`` with action=skip
          so the no-op API call clears the in-flight state. */}
      <MappingModal
        open={remapping != null}
        suggestion={remapping?.suggestion ?? null}
        busy={busy === remapping?.docId}
        onDecision={confirmRemap}
        onClose={() => setRemapping(null)}
      />
    </div>
  );
}

// ─────────────────────────── JD editor ──────────────────────────────────

function JobEditor({
  job,
  disabled,
  onSubmit,
}: {
  job: JobItem;
  disabled: boolean;
  onSubmit: (body: { text?: string; url?: string }) => void;
}) {
  const [mode, setMode] = useState<"paste" | "url">(job.source_url ? "url" : "paste");

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (mode === "paste") {
      const text = String(form.get("jd_text") ?? "").trim();
      if (text) onSubmit({ text });
    } else {
      const url = String(form.get("jd_url") ?? "").trim();
      if (url) onSubmit({ url });
    }
  };

  return (
    <form className="manage-editor" onSubmit={submit}>
      <div className="wizard-tabs">
        <button
          type="button"
          className={`wizard-tab${mode === "paste" ? " active" : ""}`}
          onClick={() => setMode("paste")}
        >
          Paste text
        </button>
        <button
          type="button"
          className={`wizard-tab${mode === "url" ? " active" : ""}`}
          onClick={() => setMode("url")}
        >
          From URL
        </button>
      </div>
      {mode === "paste" ? (
        <textarea
          name="jd_text"
          rows={8}
          placeholder="Paste the corrected JD here…"
          autoFocus
          defaultValue=""
        />
      ) : (
        <div className="input-with-icon">
          <LinkIcon size={16} />
          <input
            name="jd_url"
            type="url"
            placeholder="https://…"
            autoFocus
            defaultValue={job.source_url ?? ""}
          />
        </div>
      )}
      <button className="btn-secondary" type="submit" disabled={disabled}>
        <Sparkles size={14} /> Re-analyze and run prep
      </button>
      <p className="muted">
        Replaces the JD text, clears the parsed analysis and company snapshot, then routes back
        to setup so prep streams live.
      </p>
    </form>
  );
}

// ─────────────────────────── Blocking sessions card ─────────────────────

function BlockingSessionsCard({
  sessionIds,
  onAbandon,
  onDismiss,
}: {
  sessionIds: string[];
  onAbandon: (id: string) => void;
  onDismiss: () => void;
}) {
  return (
    <div className="blocking-sessions-card">
      <p>Can&apos;t delete this JD - these sessions are still active:</p>
      <ul>
        {sessionIds.map((id) => (
          <li key={id}>
            <code>{id.slice(0, 8)}</code>
            <button className="btn-ghost" type="button" onClick={() => onAbandon(id)}>
              Abandon
            </button>
          </li>
        ))}
      </ul>
      <button className="btn-quiet" type="button" onClick={onDismiss}>
        Dismiss
      </button>
    </div>
  );
}

// ─────────────────────────── Danger zone ───────────────────────────────

/**
 * Reset account: wipes every document, JD, profile, session, mapping,
 * embedding, and prep checkpoint owned by the user. The ``users`` row +
 * auth token stay intact, so the user remains logged in with an empty
 * account ready to re-onboard. Two-stage confirmation: click "Reveal"
 * to expose the input, then type the registered email to enable submit
 * - guards against fat-fingered clicks.
 */
function DangerZone({
  onReset,
  busy,
}: {
  onReset: (confirmEmail: string) => Promise<void>;
  busy: boolean;
}) {
  const { user } = useAuth();
  const [armed, setArmed] = useState(false);
  const [typed, setTyped] = useState("");

  if (!user) return null;
  const canSubmit = !busy && typed.trim().toLowerCase() === user.email.toLowerCase();

  return (
    <section className="manage-section danger-zone">
      <h2>Danger zone</h2>
      <div className="manage-card danger-card">
        <div>
          <strong>Reset account</strong>
          <span className="muted">
            Permanently delete every document, job description, supporting doc, mapping,
            interview session, and AI cache attached to your account. Your login stays - you
            just start over with a blank slate.
          </span>
        </div>
        <div className="manage-card-actions">
          {!armed ? (
            <button
              className="btn-ghost danger-trigger"
              type="button"
              onClick={() => setArmed(true)}
              disabled={busy}
            >
              <Trash2 size={14} /> Reveal reset
            </button>
          ) : (
            <button
              className="btn-quiet"
              type="button"
              onClick={() => {
                setArmed(false);
                setTyped("");
              }}
              disabled={busy}
            >
              Cancel
            </button>
          )}
        </div>
      </div>
      {armed ? (
        <div className="danger-confirm">
          <label className="wizard-form">
            <span className="wizard-label">
              Type <code>{user.email}</code> to confirm
            </span>
            <input
              type="email"
              autoComplete="off"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={user.email}
              disabled={busy}
            />
          </label>
          <button
            className="btn-primary danger-confirm-btn"
            type="button"
            onClick={() => {
              void onReset(typed.trim());
              setArmed(false);
              setTyped("");
            }}
            disabled={!canSubmit}
            title={canSubmit ? "Wipe everything" : "Type your email to enable"}
          >
            <Trash2 size={14} /> {busy ? "Resetting…" : "Reset my account"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

// ─────────────────────────── GitHub repos section ───────────────────────

/**
 * Phase 32 follow-up: post-setup repo management. Lists the user's ingested
 * ``github_repo`` docs and exposes an "Add / manage repos" picker that calls
 * the out-of-graph ``/github/repos`` + ``/github/repos/select`` endpoints -
 * the same ``fold_github_projects`` re-fold the prep graph uses, so the
 * Profile, docs and grounding chunks stay consistent. If no handle is stored
 * yet, the button first reveals an inline verify step.
 */
function GithubReposSection({
  token,
  repos,
  onDeleteDoc,
  onChanged,
}: {
  token: string | null;
  repos: DocumentItem[];
  onDeleteDoc: (id: string) => Promise<void> | void;
  onChanged: () => Promise<void> | void;
}) {
  const [handle, setHandle] = useState<string | null>(null);
  const [showHandleInput, setShowHandleInput] = useState(false);
  const [handleInput, setHandleInput] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [loadingList, setLoadingList] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [repoList, setRepoList] = useState<RepoListing[] | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .suggestGithubHandle(token)
      .then((s) => setHandle(s.current ?? null))
      .catch(() => {});
  }, [token]);

  const openPicker = async () => {
    if (!token) return;
    setNote(null);
    setLoadingList(true);
    try {
      const res = await api.listGithubRepos(token);
      setHandle(res.handle);
      setShowHandleInput(false);
      setRepoList(res.repos);
    } catch (err) {
      // 400 no_handle → the user hasn't verified a handle yet; reveal the
      // inline verify step instead of erroring.
      if (err instanceof ApiError && err.status === 400) {
        setShowHandleInput(true);
        setHandleInput(handle ?? "");
      } else {
        setNote("Couldn't reach GitHub. Try again.");
      }
    } finally {
      setLoadingList(false);
    }
  };

  const verifyAndList = async () => {
    if (!token) return;
    const h = handleInput.trim().replace(/^@/, "");
    if (!h) return;
    setVerifying(true);
    setNote(null);
    try {
      const r = await api.verifyGithubHandle(token, h);
      if (r.exists && r.handle) {
        setHandle(r.handle);
        await openPicker();
      } else {
        setNote("No GitHub account by that name. Check the spelling.");
      }
    } catch {
      setNote("Couldn't reach GitHub. Try again.");
    } finally {
      setVerifying(false);
    }
  };

  const submit = async (urls: string[]) => {
    if (!token) return;
    setSubmitting(true);
    setNote(null);
    try {
      const res = await api.selectGithubRepos(token, urls);
      setRepoList(null);
      await onChanged();
      const bits: string[] = [];
      if (res.ingested) bits.push(`added ${res.ingested}`);
      if (res.removed) bits.push(`removed ${res.removed}`);
      setNote(bits.length ? `Repos updated (${bits.join(", ")}).` : "No changes.");
    } catch {
      setNote("Couldn't update repos. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="manage-section">
      <div className="manage-section-head">
        <h2>GitHub repos</h2>
        <button
          className="btn-ghost"
          type="button"
          onClick={() => void openPicker()}
          disabled={loadingList}
        >
          <Plus size={14} /> {repos.length > 0 ? "Add / manage repos" : "Add repos"}
        </button>
      </div>

      {note ? <p className="wizard-note wizard-note--warn">{note}</p> : null}

      {showHandleInput ? (
        <div className="manage-editor">
          <label className="wizard-form">
            <span className="wizard-label">Your GitHub username</span>
            <input
              value={handleInput}
              onChange={(e) => setHandleInput(e.target.value)}
              placeholder="your-github-username"
              autoFocus
              disabled={verifying}
            />
          </label>
          <button
            className="btn-secondary"
            type="button"
            onClick={() => void verifyAndList()}
            disabled={verifying || !handleInput.trim()}
          >
            {verifying ? "Verifying…" : "Verify & list repos"}
          </button>
        </div>
      ) : null}

      {repos.length === 0 ? (
        <p className="muted">
          No repos ingested{handle ? ` for @${handle}` : ""}. Use{" "}
          <strong>Add repos</strong> to pick from your public repositories.
        </p>
      ) : (
        <div className="manage-list">
          {repos.map((d) => (
            <GithubRepoCard key={d.id} doc={d} onDelete={() => onDeleteDoc(d.id)} />
          ))}
        </div>
      )}

      <RepoSelectModal
        open={repoList != null}
        repos={repoList}
        busy={submitting}
        onSubmit={(urls) => void submit(urls)}
        onClose={() => setRepoList(null)}
        closeLabel="Cancel"
      />
    </section>
  );
}

// ─────────────────────────── GitHub repo card ───────────────────────────

/**
 * Phase 32: a read-only Manage row for an ingested ``github_repo`` doc.
 * Tech chips, key features and the repo link come straight off the folded
 * ProjectItem on ``parsed_json`` (no extra fetch). Adding / re-selecting
 * repos stays in the setup wizard - Manage only surfaces and deletes them.
 */
function GithubRepoCard({ doc, onDelete }: { doc: DocumentItem; onDelete: () => void }) {
  const pj = (doc.parsed_json ?? {}) as {
    tech?: unknown;
    key_features?: unknown;
    urls?: unknown;
  };
  const asStrings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  const tech = asStrings(pj.tech);
  const features = asStrings(pj.key_features).slice(0, 4);
  const repoUrl = asStrings(pj.urls)[0];

  return (
    <div className="manage-card manage-card--repo">
      <div>
        <strong>{doc.project_title ?? doc.filename}</strong>
        {repoUrl ? (
          <a className="repo-link" href={repoUrl} target="_blank" rel="noreferrer">
            <ExternalLink size={12} /> {repoUrl.replace(/^https?:\/\/(www\.)?github\.com\//, "")}
          </a>
        ) : null}
        {tech.length > 0 ? (
          <div className="repo-chips">
            {tech.slice(0, 8).map((t) => (
              <span key={t} className="repo-chip">
                {t}
              </span>
            ))}
          </div>
        ) : null}
        {features.length > 0 ? (
          <ul className="repo-features">
            {features.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        ) : null}
        {doc.embedding_status ? <EmbedPill status={doc.embedding_status} /> : null}
      </div>
      <div className="manage-card-actions">
        <ArmedDeleteButton label="Delete" icon={<Trash2 size={14} />} onConfirm={onDelete} />
      </div>
    </div>
  );
}

// ─────────────────────────── Embed pill ─────────────────────────────────

function EmbedPill({ status }: { status: EmbeddingStatus }) {
  if (status === "ready") return <StatusPill tone="good">Embeddings ready</StatusPill>;
  if (status === "pending") return <StatusPill tone="warn">Embedding…</StatusPill>;
  if (status === "failed") return <StatusPill tone="bad">Embedding failed</StatusPill>;
  return <StatusPill tone="neutral">Not yet mapped</StatusPill>;
}
