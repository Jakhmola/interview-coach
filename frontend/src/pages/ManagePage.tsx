import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";
import { ArrowLeft, FileUp, LinkIcon, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import {
  ApiError,
  DocumentItem,
  EmbeddingStatus,
  JobItem,
  MappingSuggestion,
  api,
} from "../api";
import { ArmedDeleteButton } from "../components/ArmedDeleteButton";
import { MappingPanel, MappingDecision } from "../components/MappingPanel";
import { ErrorBanner, StatusPill, formatDate } from "../components/ui";
import { codeFrom } from "../errors";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Phase 22 — inventory editor for the user's CV, JDs, and supporting
 * docs. The setup wizard handles "the next thing to do"; Manage owns
 * "I want to change something I already gave you" — replace CV,
 * re-analyze a JD, remap or retry-embed a supporting doc, and the
 * structured 409 blocking-sessions card so the user isn't stranded
 * when a delete is gated.
 */

type BlockingState = {
  /** Which row triggered the 409 — used to scope the card to that card. */
  scope: { kind: "cv"; id: string } | { kind: "job"; id: string };
  code: "cv_in_use" | "job_in_use";
  sessionIds: string[];
};

export function ManagePage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { activeJobId, setActiveJobId } = useActiveJob();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
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

  const load = async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const [nextDocs, nextJobs] = await Promise.all([
        api.listDocuments(token),
        api.listJobs(token),
      ]);
      setDocs(nextDocs);
      setJobs(nextJobs);
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

  // Drop the blocking card whenever the underlying inventory changes —
  // an abandon-and-retry sequence shouldn't leave a stale "still
  // blocked" card pinned after the second delete succeeds.
  useEffect(() => {
    if (!blocking) return;
    if (blocking.scope.kind === "cv" && !docs.find((d) => d.id === blocking.scope.id)) {
      setBlocking(null);
    } else if (blocking.scope.kind === "job" && !jobs.find((j) => j.id === blocking.scope.id)) {
      setBlocking(null);
    }
  }, [docs, jobs, blocking]);

  // ─── delete helpers ────────────────────────────────────────────────
  const captureBlocking = (
    err: unknown,
    scope: BlockingState["scope"],
  ): boolean => {
    if (!(err instanceof ApiError) || err.status !== 409) return false;
    const obj = err.detailObject;
    if (!obj || typeof obj !== "object") return false;
    const code = (obj as { code?: unknown }).code;
    const ids = (obj as { blocking_session_ids?: unknown }).blocking_session_ids;
    if (code !== "cv_in_use" && code !== "job_in_use") return false;
    if (!Array.isArray(ids)) return false;
    setBlocking({
      scope,
      code: code as BlockingState["code"],
      sessionIds: ids.filter((x): x is string => typeof x === "string"),
    });
    return true;
  };

  const deleteDocument = async (id: string, kind: "cv" | "project_doc") => {
    if (!token) return;
    setError(null);
    try {
      await api.deleteDocument(token, id);
      setMessage("Deleted.");
      await load();
    } catch (err) {
      if (kind === "cv" && captureBlocking(err, { kind: "cv", id })) return;
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
      if (captureBlocking(err, { kind: "job", id })) return;
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
      // we land back there — surface the new CV via /setup so the user
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
      // Last blocking session cleared — auto-retry the original delete.
      const scope = blocking.scope;
      setBlocking(null);
      if (scope.kind === "cv") {
        await deleteDocument(scope.id, "cv");
      } else {
        await deleteJob(scope.id);
      }
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  // ─── derived UI bits ───────────────────────────────────────────────
  const mappedTechDocs = techDocs.filter((d) => d.project_title).length;

  return (
    <div className="manage-page">
      <header className="manage-header">
        <button className="btn-quiet" type="button" onClick={() => navigate("/setup")}>
          <ArrowLeft size={14} /> Back to setup
        </button>
        <h1>Manage</h1>
        <p>CV, job descriptions, supporting docs.</p>
      </header>

      {message ? <div className="success-banner">{message}</div> : null}
      <ErrorBanner code={error} />

      {isLoading ? <p className="muted">Loading…</p> : null}

      {/* ─── CV ───────────────────────────────────────────────────── */}
      <section className="manage-section">
        <h2>CV</h2>
        {cv ? (
          <>
            <div className="manage-card">
              <div>
                <strong>{cv.filename}</strong>
                <span className="muted">{cv.char_count.toLocaleString()} chars</span>
                {cv.embedding_status ? <EmbedPill status={cv.embedding_status} /> : null}
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
                <ArmedDeleteButton
                  label="Delete CV"
                  icon={<Trash2 size={14} />}
                  consequenceLabel={
                    mappedTechDocs > 0
                      ? `Will clear your profile and unmap ${mappedTechDocs} supporting doc${mappedTechDocs === 1 ? "" : "s"}`
                      : "Will clear your profile"
                  }
                  onConfirm={() => deleteDocument(cv.id, "cv")}
                />
              </div>
            </div>
            {blocking && blocking.scope.kind === "cv" && blocking.scope.id === cv.id ? (
              <BlockingSessionsCard
                code={blocking.code}
                sessionIds={blocking.sessionIds}
                onAbandon={abandonSession}
                onDismiss={() => setBlocking(null)}
              />
            ) : null}
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
                      <strong>{j.source_url || "Pasted JD"}</strong>
                      <span className="muted">
                        {j.char_count.toLocaleString()} chars · {formatDate(j.created_at)}
                      </span>
                      {j.id === activeJobId ? <StatusPill tone="good">Active</StatusPill> : null}
                    </div>
                    <div className="manage-card-actions">
                      {j.id !== activeJobId ? (
                        <button
                          className="btn-ghost"
                          type="button"
                          onClick={() => setActiveJobId(j.id)}
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
                      code={blocking!.code}
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
              const isRemapping = remapping?.docId === d.id;
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
                    </div>
                    <div className="manage-card-actions">
                      <button
                        className="btn-ghost"
                        type="button"
                        onClick={() =>
                          isRemapping ? setRemapping(null) : void openRemap(d.id)
                        }
                        disabled={busy === d.id}
                      >
                        <Sparkles size={14} /> {isRemapping ? "Cancel" : "Remap"}
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
                        onConfirm={() => deleteDocument(d.id, "project_doc")}
                      />
                    </div>
                  </div>
                  {isRemapping ? (
                    <MappingPanel
                      suggestion={remapping!.suggestion}
                      onDecision={confirmRemap}
                      disabled={busy === d.id}
                    />
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </section>
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
  code,
  sessionIds,
  onAbandon,
  onDismiss,
}: {
  code: "cv_in_use" | "job_in_use";
  sessionIds: string[];
  onAbandon: (id: string) => void;
  onDismiss: () => void;
}) {
  const headline =
    code === "cv_in_use"
      ? "Can't delete your CV — these sessions are still active:"
      : "Can't delete this JD — these sessions are still active:";
  return (
    <div className="blocking-sessions-card">
      <p>{headline}</p>
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

// ─────────────────────────── Embed pill ─────────────────────────────────

function EmbedPill({ status }: { status: EmbeddingStatus }) {
  if (status === "ready") return <StatusPill tone="good">Embeddings ready</StatusPill>;
  if (status === "pending") return <StatusPill tone="warn">Embedding…</StatusPill>;
  if (status === "failed") return <StatusPill tone="bad">Embedding failed</StatusPill>;
  return <StatusPill tone="neutral">Not yet mapped</StatusPill>;
}
