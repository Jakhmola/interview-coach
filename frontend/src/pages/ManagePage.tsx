import { useEffect, useState } from "react";
import { ArrowLeft, Trash2 } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { DocumentItem, EmbeddingStatus, JobItem, api } from "../api";
import { ArmedDeleteButton } from "../components/ArmedDeleteButton";
import { ErrorBanner, StatusPill, formatDate } from "../components/ui";
import { codeFrom } from "../errors";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Advanced operations: delete CV, rebuild profile, delete JDs, remap or
 * delete project docs. Lifted off the Setup wizard so the wizard surface
 * stays focused on "the one next thing to do".
 */
export function ManagePage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { activeJobId, setActiveJobId } = useActiveJob();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

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

  const deleteDocument = async (id: string) => {
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
      setError(codeFrom(err));
    }
  };

  const rebuildProfile = async () => {
    if (!token || !cv) return;
    setError(null);
    try {
      await api.rebuildProfile(token, cv.id);
      setMessage("Rebuilding your profile…");
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  return (
    <div className="manage-page">
      <header className="manage-header">
        <button
          className="btn-quiet"
          type="button"
          onClick={() => navigate("/setup")}
        >
          <ArrowLeft size={14} /> Back to setup
        </button>
        <h1>Manage</h1>
        <p>CV, job descriptions, supporting docs.</p>
      </header>

      {message ? <div className="success-banner">{message}</div> : null}
      <ErrorBanner code={error} />

      {isLoading ? <p className="muted">Loading…</p> : null}

      <section className="manage-section">
        <h2>CV</h2>
        {cv ? (
          <div className="manage-card">
            <div>
              <strong>{cv.filename}</strong>
              <span className="muted">{cv.char_count.toLocaleString()} chars</span>
              {cv.embedding_status ? (
                <EmbedPill status={cv.embedding_status} />
              ) : null}
            </div>
            <div className="manage-card-actions">
              <button className="btn-ghost" type="button" onClick={rebuildProfile}>
                Rebuild profile
              </button>
              <ArmedDeleteButton
                label="Delete CV"
                icon={<Trash2 size={14} />}
                onConfirm={() => deleteDocument(cv.id)}
              />
            </div>
          </div>
        ) : (
          <p className="muted">
            No CV on file. <Link to="/setup">Upload one</Link>.
          </p>
        )}
      </section>

      <section className="manage-section">
        <h2>Job descriptions</h2>
        {jobs.length === 0 ? (
          <p className="muted">No JDs yet.</p>
        ) : (
          <div className="manage-list">
            {jobs.map((j) => (
              <div key={j.id} className="manage-card">
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
                  <ArmedDeleteButton
                    label="Delete"
                    icon={<Trash2 size={14} />}
                    onConfirm={() => deleteJob(j.id)}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="manage-section">
        <h2>Supporting docs</h2>
        {techDocs.length === 0 ? (
          <p className="muted">No project docs.</p>
        ) : (
          <div className="manage-list">
            {techDocs.map((d) => (
              <div key={d.id} className="manage-card">
                <div>
                  <strong>{d.filename}</strong>
                  <span className="muted">
                    {d.project_title ? `"${d.project_title}"` : "Unmapped"} ·{" "}
                    {d.char_count.toLocaleString()} chars
                  </span>
                  {d.embedding_status ? <EmbedPill status={d.embedding_status} /> : null}
                </div>
                <div className="manage-card-actions">
                  <ArmedDeleteButton
                    label="Delete"
                    icon={<Trash2 size={14} />}
                    onConfirm={() => deleteDocument(d.id)}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function EmbedPill({ status }: { status: EmbeddingStatus }) {
  if (status === "ready") return <StatusPill tone="good">Embeddings ready</StatusPill>;
  if (status === "pending") return <StatusPill tone="warn">Embedding…</StatusPill>;
  if (status === "failed") return <StatusPill tone="bad">Embedding failed</StatusPill>;
  return <StatusPill tone="neutral">Not yet mapped</StatusPill>;
}
