import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileUp,
  LinkIcon,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useOutletContext } from "react-router-dom";

import {
  ApiError,
  DocumentItem,
  JobItem,
  PrepStatus,
  SseFrame,
  api,
  prepareSessionStream,
} from "../api";
import { DocMappingModal } from "../components/DocMappingModal";
import { LoadingStatus } from "../components/LoadingStatus";
import { EmptyState, StatusPill, formatDate } from "../components/ui";
import { useAuth } from "../state/auth";

const nodeLabels: Record<string, string> = {
  profile_builder: "Profile builder",
  job_analyzer: "Job analyzer",
  company_researcher: "Company research",
};

const nodeLoadingMessages: Record<string, string[]> = {
  profile_builder: ["Reading your CV", "Finding signal in your projects", "Building candidate profile"],
  job_analyzer: ["Parsing role expectations", "Extracting must-have skills", "Mapping interview focus"],
  company_researcher: ["Scanning company context", "Collecting recent signals", "Preparing company notes"],
};

type SetupOutletContext = {
  refreshReadiness: () => Promise<void>;
  isSetupComplete: boolean;
};

function asText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.filter((item) => typeof item === "string").join(", ");
  }
  return "";
}

export function SetupPage() {
  const { token } = useAuth();
  const { refreshReadiness, isSetupComplete } = useOutletContext<SetupOutletContext>();
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string>("");
  const [status, setStatus] = useState<PrepStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPreparing, setIsPreparing] = useState(false);
  const [nodeState, setNodeState] = useState<Record<string, string>>({});
  const [mappingDocId, setMappingDocId] = useState<string | null>(null);
  const [setupStep, setSetupStep] = useState(0);

  const selectedJob = useMemo(
    () => jobs.find((job) => job.id === selectedJobId) ?? null,
    [jobs, selectedJobId],
  );
  const hasCv = docs.some((doc) => doc.kind === "cv");
  const technicalDocs = docs.filter((doc) => doc.kind === "project_doc");

  const load = async () => {
    if (!token) {
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const [nextDocs, nextJobs] = await Promise.all([api.listDocuments(token), api.listJobs(token)]);
      setDocs(nextDocs);
      setJobs(nextJobs);
      setSelectedJobId((current) => current || nextJobs[0]?.id || "");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not load setup data.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [token]);

  useEffect(() => {
    if (!token || !selectedJobId) {
      setStatus(null);
      return;
    }
    api
      .prepStatus(token, selectedJobId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [token, selectedJobId]);

  useEffect(() => {
    if (!isLoading && !hasCv && setupStep > 0) {
      setSetupStep(0);
    }
  }, [hasCv, isLoading, setupStep]);

  const upload = async (event: ChangeEvent<HTMLInputElement>, kind: DocumentItem["kind"]) => {
    if (!token || !event.target.files?.[0]) {
      return;
    }
    setMessage(null);
    setError(null);
    try {
      const doc = await api.uploadDocument(token, kind, event.target.files[0]);
      setMessage(`Uploaded ${doc.filename}.`);
      await load();
      if (kind === "cv") {
        setSetupStep(1);
      }
      if (kind === "project_doc") {
        setMappingDocId(doc.id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Upload failed.");
    } finally {
      event.target.value = "";
    }
  };

  const submitJobText = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const text = String(form.get("jd_text") ?? "").trim();
    if (!text) {
      setError("Paste a job description first.");
      return;
    }
    try {
      const job = await api.submitJobText(token, text);
      setMessage("Saved job description.");
      await load();
      setSelectedJobId(job.id);
      setSetupStep(3);
      event.currentTarget.reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not save job.");
    }
  };

  const submitJobUrl = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) {
      return;
    }
    const form = new FormData(event.currentTarget);
    const url = String(form.get("jd_url") ?? "").trim();
    if (!url) {
      setError("Enter a job URL first.");
      return;
    }
    try {
      const job = await api.submitJobUrl(token, url);
      setMessage("Fetched and saved job description.");
      await load();
      setSelectedJobId(job.id);
      setSetupStep(3);
      event.currentTarget.reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not fetch job.");
    }
  };

  const deleteDocument = async (id: string) => {
    if (!token) {
      return;
    }
    await api.deleteDocument(token, id);
    await load();
    await refreshReadiness();
  };

  const deleteJob = async (id: string) => {
    if (!token) {
      return;
    }
    await api.deleteJob(token, id);
    setSelectedJobId("");
    setStatus(null);
    setSetupStep(2);
    await load();
    await refreshReadiness();
  };

  const runPrep = async (forceRefresh: boolean) => {
    if (!token || !selectedJobId) {
      return;
    }
    setIsPreparing(true);
    setNodeState({
      profile_builder: "pending",
      job_analyzer: "pending",
      company_researcher: "pending",
    });
    setError(null);
    setMessage(null);
    try {
      await prepareSessionStream(token, selectedJobId, forceRefresh, (frame: SseFrame) => {
        const data = frame.data as { node?: string; reason?: string; code?: string; detail?: string };
        if (frame.event === "node_started" && data.node) {
          setNodeState((current) => ({ ...current, [data.node!]: "running" }));
        }
        if (frame.event === "node_done" && data.node) {
          setNodeState((current) => ({ ...current, [data.node!]: "done" }));
        }
        if (frame.event === "node_skipped" && data.node) {
          setNodeState((current) => ({ ...current, [data.node!]: `cached ${data.reason ?? ""}` }));
        }
        if (frame.event === "error") {
          setError(data.detail || data.code || "Preparation failed.");
        }
        if (frame.event === "done") {
          setMessage("Ready for interview.");
        }
      });
      await load();
      const nextStatus = await api.prepStatus(token, selectedJobId);
      setStatus(nextStatus);
      await refreshReadiness();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Preparation failed.");
    } finally {
      setIsPreparing(false);
    }
  };

  return (
    <div className="setup-workflow">
      {mappingDocId && token ? (
        <DocMappingModal
          token={token}
          documentId={mappingDocId}
          onClose={() => setMappingDocId(null)}
          onApplied={async () => {
            setMappingDocId(null);
            setMessage("Project mapping saved.");
            await load();
          }}
        />
      ) : null}
      <aside className="setup-steps">
        {[
          ["CV", hasCv],
          ["Technical context", technicalDocs.length > 0],
          ["Job", Boolean(selectedJob)],
          ["Process", status?.can_start || isSetupComplete],
        ].map(([label, complete], index) => (
          <button
            key={String(label)}
            className={`setup-step ${setupStep === index ? "active" : ""} ${complete ? "complete" : ""}`}
            type="button"
            onClick={() => {
              if (index === 0 || hasCv) {
                setSetupStep(index);
              }
            }}
            disabled={index > 0 && !hasCv}
          >
            <span>{index + 1}</span>
            <strong>{label}</strong>
          </button>
        ))}
      </aside>

      <section className="panel setup-stage">
        <div className="panel-header">
          <div>
            <span className="eyebrow">Setup</span>
            <h2>{["Upload your CV", "Add technical context", "Attach the job", "Process and unlock"][setupStep]}</h2>
          </div>
          {status?.can_start || isSetupComplete ? (
            <StatusPill tone="good">Interview unlocked</StatusPill>
          ) : (
            <StatusPill tone="warn">Setup required</StatusPill>
          )}
        </div>
        {isLoading ? <p>Loading setup...</p> : null}
        {message ? <div className="success-banner">{message}</div> : null}
        {error ? <div className="error-banner">{error}</div> : null}

        {setupStep === 0 ? (
          <div className="step-pane">
            <label className="upload-drop hero-upload">
              <FileUp size={28} />
              <span>Upload CV</span>
              <small>PDF or DOCX</small>
              <input type="file" accept=".pdf,.docx" onChange={(event) => upload(event, "cv")} />
            </label>
            {docs.filter((doc) => doc.kind === "cv").length === 0 ? (
              <EmptyState title="No CV yet" body="Start here so the coach can build your candidate profile." />
            ) : (
              <DocumentList docs={docs.filter((doc) => doc.kind === "cv")} onDelete={deleteDocument} />
            )}
            <div className="wizard-actions">
              <button className="primary-button" type="button" onClick={() => setSetupStep(1)} disabled={!hasCv}>
                Continue
                <ArrowRight size={18} />
              </button>
            </div>
          </div>
        ) : null}

        {setupStep === 1 ? (
          <div className="step-pane">
            <label className="upload-drop hero-upload">
              <FileUp size={28} />
              <span>Upload technical docs</span>
              <small>Architecture notes, project docs, or take-home material</small>
              <input
                type="file"
                accept=".pdf,.docx"
                onChange={(event) => upload(event, "project_doc")}
              />
            </label>
            <div className="coming-soon-grid">
              <label>
                GitHub URL
                <span className="input-with-icon disabled-field">
                  <LinkIcon size={17} />
                  <input type="url" placeholder="Coming later" disabled />
                </span>
              </label>
              <label>
                Portfolio URL
                <span className="input-with-icon disabled-field">
                  <LinkIcon size={17} />
                  <input type="url" placeholder="Coming later" disabled />
                </span>
              </label>
            </div>
            {technicalDocs.length === 0 ? (
              <EmptyState title="Optional for now" body="Add supporting docs if you have them, or skip to the job." />
            ) : (
              <DocumentList
                docs={technicalDocs}
                onDelete={deleteDocument}
                onMap={(id) => setMappingDocId(id)}
              />
            )}
            <div className="wizard-actions">
              <button className="secondary-button" type="button" onClick={() => setSetupStep(0)}>
                <ArrowLeft size={18} />
                Back
              </button>
              <button className="primary-button" type="button" onClick={() => setSetupStep(2)}>
                Continue
                <ArrowRight size={18} />
              </button>
            </div>
          </div>
        ) : null}

        {setupStep === 2 ? (
          <div className="step-pane">
            <div className="two-column">
              <form className="form-stack" onSubmit={submitJobText}>
                <label>
                  Paste job description
                  <textarea name="jd_text" rows={10} placeholder="Paste the role description..." />
                </label>
                <button className="secondary-button" type="submit">
                  Save pasted JD
                </button>
              </form>
              <form className="form-stack" onSubmit={submitJobUrl}>
                <label>
                  Fetch job URL
                  <span className="input-with-icon">
                    <LinkIcon size={17} />
                    <input name="jd_url" type="url" placeholder="https://..." />
                  </span>
                </label>
                <button className="secondary-button" type="submit">
                  Fetch and save
                </button>
              </form>
            </div>
            {jobs.length === 0 ? (
              <EmptyState title="No job saved" body="Paste a JD or fetch one from a public URL." />
            ) : (
              <div className="job-list">
                {jobs.map((job) => (
                  <button
                    className={`job-option ${selectedJobId === job.id ? "selected" : ""}`}
                    key={job.id}
                    onClick={() => setSelectedJobId(job.id)}
                  >
                    <span>{job.source_url || "Pasted job description"}</span>
                    <small>
                      {job.char_count.toLocaleString()} chars · {formatDate(job.created_at)}
                    </small>
                  </button>
                ))}
              </div>
            )}
            <div className="wizard-actions">
              <button className="secondary-button" type="button" onClick={() => setSetupStep(1)}>
                <ArrowLeft size={18} />
                Back
              </button>
              <button className="primary-button" type="button" onClick={() => setSetupStep(3)} disabled={!selectedJob}>
                Continue
                <ArrowRight size={18} />
              </button>
            </div>
          </div>
        ) : null}

        {setupStep === 3 ? (
          <div className="step-pane">
            {!selectedJob ? (
              <EmptyState title="Pick a job" body="Preparation status appears after a JD is selected." />
            ) : (
              <>
                <div className="readiness-grid">
                  <Readiness label="CV uploaded" ready={status?.has_cv} />
                  <Readiness label="Profile built" ready={status?.profile_ready} />
                  <Readiness label="JD analyzed" ready={status?.job_analyzed} />
                  <Readiness label="Company researched" ready={status?.company_researched} />
                </div>
                <div className="button-row">
                  <button className="primary-button" onClick={() => runPrep(false)} disabled={isPreparing}>
                    <CheckCircle2 size={18} />
                    {isPreparing ? "Preparing..." : "Process setup"}
                  </button>
                  <button className="secondary-button" onClick={() => runPrep(true)} disabled={isPreparing}>
                    <RefreshCw size={18} />
                    Re-research
                  </button>
                  <button className="ghost-button danger" onClick={() => deleteJob(selectedJob.id)}>
                    <Trash2 size={17} />
                    Delete JD
                  </button>
                </div>
                {Object.keys(nodeState).length ? (
                  <div className="node-list">
                    {Object.entries(nodeLabels).map(([key, label]) => (
                      <TaskStatus
                        key={key}
                        label={label}
                        state={nodeState[key] ?? "pending"}
                        messages={nodeLoadingMessages[key] ?? [`Preparing ${label.toLowerCase()}`]}
                      />
                    ))}
                  </div>
                ) : null}
                {status ? <Insights status={status} /> : null}
              </>
            )}
            <div className="wizard-actions">
              <button className="secondary-button" type="button" onClick={() => setSetupStep(2)}>
                <ArrowLeft size={18} />
                Back
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => setSetupStep(2)}
              >
                Add another job
              </button>
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function DocumentList({
  docs,
  onDelete,
  onMap,
}: {
  docs: DocumentItem[];
  onDelete: (id: string) => void;
  onMap?: (id: string) => void;
}) {
  return (
    <div className="list">
      {docs.map((doc) => (
        <article className="list-item" key={doc.id}>
          <div>
            <strong>{doc.filename}</strong>
            <span>
              {doc.kind === "cv" ? "CV" : "Technical doc"}
              {doc.project_title ? ` · "${doc.project_title}"` : ""} · {doc.char_count.toLocaleString()} chars
            </span>
          </div>
          <div className="list-actions">
            {doc.kind === "project_doc" && onMap ? (
              <button className="ghost-button" onClick={() => onMap(doc.id)} title="Re-map to profile">
                Re-map
              </button>
            ) : null}
            <button className="icon-button danger" onClick={() => onDelete(doc.id)} title="Delete">
              <Trash2 size={17} />
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}

function TaskStatus({
  label,
  state,
  messages,
}: {
  label: string;
  state: string;
  messages: string[];
}) {
  const normalizedState = state.startsWith("cached") ? "cached" : state;
  const isActive = normalizedState === "pending" || normalizedState === "running";
  const fallback =
    normalizedState === "done"
      ? "Complete"
      : normalizedState === "cached"
        ? "Using cached result"
        : normalizedState === "running"
          ? "Working"
          : "Queued";

  return (
    <article className={`task-status task-${normalizedState}`}>
      <span className="task-status-dot" />
      <div>
        <strong>{label}</strong>
        <LoadingStatus active={isActive} messages={messages} fallback={fallback} />
      </div>
    </article>
  );
}

function Readiness({ label, ready }: { label: string; ready?: boolean }) {
  return (
    <div className={`readiness-card ${ready ? "ready" : ""}`}>
      <span>{label}</span>
      <strong>{ready ? "Done" : "Open"}</strong>
    </div>
  );
}

function Insights({ status }: { status: PrepStatus }) {
  const job = status.job ?? {};
  const profile = status.profile ?? {};
  const company = status.company?.snapshot ?? {};

  return (
    <div className="insight-grid">
      <article>
        <span className="eyebrow">Role</span>
        <h3>{asText(job.title) || "Not analyzed yet"}</h3>
        <p>{asText(job.seniority)}</p>
        <small>{asText(job.must_have_skills)}</small>
      </article>
      <article>
        <span className="eyebrow">Candidate</span>
        <h3>{asText(profile.summary) || "Profile pending"}</h3>
        <small>{asText(profile.skills)}</small>
      </article>
      <article>
        <span className="eyebrow">Company</span>
        <h3>{status.company?.company_name || "Research pending"}</h3>
        <p>{asText(company.mission)}</p>
        <small>{asText(company.values_and_signals)}</small>
      </article>
    </div>
  );
}
