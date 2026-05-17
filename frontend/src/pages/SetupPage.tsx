import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, FileUp, LinkIcon, RefreshCw } from "lucide-react";
import { Link, useNavigate, useOutletContext } from "react-router-dom";

import {
  DocumentItem,
  EmbeddingStatus,
  JobItem,
  PrepStatus,
  SseFrame,
  api,
  prepareSessionStream,
} from "../api";
import { DocMappingModal } from "../components/DocMappingModal";
import { LoadingStatus } from "../components/LoadingStatus";
import { ErrorBanner, StatusPill } from "../components/ui";
import { codeFrom } from "../errors";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

const nodeLabels: Record<string, string> = {
  profile_builder: "Reading your CV",
  job_analyzer: "Analyzing the JD",
  company_researcher: "Researching the company",
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

type Step = "cv" | "jd" | "docs" | "prep";
const STEPS: Step[] = ["cv", "jd", "docs", "prep"];
const STEP_TITLES: Record<Step, string> = {
  cv: "Upload your CV",
  jd: "Paste the job description",
  docs: "Add supporting docs (optional)",
  prep: "Process the setup",
};
const STEP_BLURBS: Record<Step, string> = {
  cv: "We'll read it once and use it to ground every question.",
  jd: "Role, company, must-haves — we'll extract them automatically.",
  docs: "Architecture notes, take-homes, or project write-ups. Skip if you don't have any.",
  prep: "Reads your CV, analyzes the JD, researches the company. Can take a minute or two.",
};

export function SetupPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { refreshReadiness, isSetupComplete } = useOutletContext<SetupOutletContext>();
  const { activeJobId, activeJob, setActiveJobId, refresh: refreshActiveJob } = useActiveJob();
  const prepAbort = useStreamAbort();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [status, setStatus] = useState<PrepStatus | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPreparing, setIsPreparing] = useState(false);
  const [nodeState, setNodeState] = useState<Record<string, string>>({});
  const [mappingDocId, setMappingDocId] = useState<string | null>(null);
  const [pendingMappingDocId, setPendingMappingDocId] = useState<string | null>(null);
  const [step, setStep] = useState<Step>("cv");
  // When the user clicks "Add another job" from the ReadyLanding, we
  // bypass the ready-landing for the rest of the session so the wizard
  // surfaces even though setup is technically complete.
  const [overrideReady, setOverrideReady] = useState(false);
  const [didInitStep, setDidInitStep] = useState(false);
  const [jdMode, setJdMode] = useState<"paste" | "url">("paste");
  const profilePollRef = useRef<number | null>(null);
  const messageTimerRef = useRef<number | null>(null);

  const hasCv = docs.some((doc) => doc.kind === "cv");
  const cv = docs.find((doc) => doc.kind === "cv");
  const technicalDocs = docs.filter((doc) => doc.kind === "project_doc");
  const selectedJob = jobs.find((j) => j.id === activeJobId) ?? null;
  const stepIndex = STEPS.indexOf(step);

  const setupReady = useMemo(
    () => Boolean(hasCv && selectedJob && status?.can_start),
    [hasCv, selectedJob, status?.can_start],
  );

  const load = async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const [nextDocs, nextJobs] = await Promise.all([api.listDocuments(token), api.listJobs(token)]);
      setDocs(nextDocs);
      setJobs(nextJobs);
      if (!activeJobId && nextJobs[0]) {
        setActiveJobId(nextJobs[0].id);
      }
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

  useEffect(() => {
    if (!token || !activeJobId) {
      setStatus(null);
      return;
    }
    api
      .prepStatus(token, activeJobId)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [token, activeJobId]);

  // One-shot initial step picker — runs once after the first load so
  // returning users land on the right step. After that, the user drives
  // navigation via Back / Continue and we don't override their choice.
  useEffect(() => {
    if (isLoading || didInitStep) return;
    if (!hasCv) {
      setStep("cv");
    } else if (!selectedJob) {
      setStep("jd");
    } else if (!status?.can_start) {
      setStep("prep");
    }
    setDidInitStep(true);
  }, [isLoading, didInitStep, hasCv, selectedJob, status?.can_start]);

  // Auto-dismiss success messages after 3.5s. Also clears when the step
  // changes so a "saved" toast from step 2 doesn't bleed into step 3.
  useEffect(() => {
    if (!message) return;
    if (messageTimerRef.current !== null) {
      window.clearTimeout(messageTimerRef.current);
    }
    messageTimerRef.current = window.setTimeout(() => {
      setMessage(null);
      messageTimerRef.current = null;
    }, 3500);
    return () => {
      if (messageTimerRef.current !== null) {
        window.clearTimeout(messageTimerRef.current);
        messageTimerRef.current = null;
      }
    };
  }, [message]);
  useEffect(() => {
    // Clear stale message on step change.
    setMessage(null);
  }, [step]);

  // Profile-readiness polling.
  useEffect(() => {
    if (!token || !activeJobId) return;
    if (status?.profile_ready) {
      if (profilePollRef.current !== null) {
        window.clearInterval(profilePollRef.current);
        profilePollRef.current = null;
      }
      if (pendingMappingDocId) {
        setMappingDocId(pendingMappingDocId);
        setPendingMappingDocId(null);
      }
      return;
    }
    if (profilePollRef.current !== null) return;
    let iterations = 0;
    const MAX_ITERATIONS = 40;
    profilePollRef.current = window.setInterval(() => {
      iterations += 1;
      if (iterations >= MAX_ITERATIONS) {
        if (profilePollRef.current !== null) {
          window.clearInterval(profilePollRef.current);
          profilePollRef.current = null;
        }
        return;
      }
      api
        .prepStatus(token, activeJobId)
        .then(setStatus)
        .catch(() => {});
    }, 2000);
    return () => {
      if (profilePollRef.current !== null) {
        window.clearInterval(profilePollRef.current);
        profilePollRef.current = null;
      }
    };
  }, [token, activeJobId, status?.profile_ready, pendingMappingDocId]);

  const uploadCv = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!token || !event.target.files?.[0]) return;
    setMessage(null);
    setError(null);
    try {
      await api.uploadDocument(token, "cv", event.target.files[0]);
      setMessage("Got it — building your profile.");
      await load();
      setStep(jobs.length === 0 ? "jd" : "prep");
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      event.target.value = "";
    }
  };

  const uploadProjectDoc = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!token || !event.target.files?.[0]) return;
    setMessage(null);
    setError(null);
    try {
      const doc = await api.uploadDocument(token, "project_doc", event.target.files[0]);
      setMessage(`Uploaded ${doc.filename}.`);
      await load();
      if (status?.profile_ready) {
        setMappingDocId(doc.id);
      } else {
        setPendingMappingDocId(doc.id);
      }
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      event.target.value = "";
    }
  };

  const submitJobText = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) return;
    const form = new FormData(event.currentTarget);
    const text = String(form.get("jd_text") ?? "").trim();
    if (!text) {
      setError("empty_answer");
      return;
    }
    try {
      const job = await api.submitJobText(token, text);
      setMessage("Job description saved.");
      setActiveJobId(job.id);
      await load();
      setStep("docs");
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  const submitJobUrl = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!token) return;
    const form = new FormData(event.currentTarget);
    const url = String(form.get("jd_url") ?? "").trim();
    if (!url) {
      setError("empty_answer");
      return;
    }
    try {
      const job = await api.submitJobUrl(token, url);
      setMessage("Fetched and saved.");
      setActiveJobId(job.id);
      await load();
      setStep("docs");
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  const runPrep = async (forceRefresh: boolean) => {
    if (!token || !activeJobId) return;
    setIsPreparing(true);
    setNodeState({
      profile_builder: "pending",
      job_analyzer: "pending",
      company_researcher: "pending",
    });
    setError(null);
    setMessage(null);
    const signal = prepAbort.fresh();
    try {
      await prepareSessionStream(
        token,
        activeJobId,
        forceRefresh,
        (frame: SseFrame) => {
          const data = frame.data as { node?: string; reason?: string; code?: string; detail?: string };
          if (frame.event === "node_started" && data.node) {
            setNodeState((c) => ({ ...c, [data.node!]: "running" }));
          }
          if (frame.event === "node_done" && data.node) {
            setNodeState((c) => ({ ...c, [data.node!]: "done" }));
          }
          if (frame.event === "node_skipped" && data.node) {
            setNodeState((c) => ({ ...c, [data.node!]: `cached ${data.reason ?? ""}` }));
          }
          if (frame.event === "error") {
            setError(data.code || data.detail || "stream_interrupted");
          }
          if (frame.event === "done") {
            setMessage("Ready for interview.");
          }
        },
        signal,
      );
      await load();
      const nextStatus = await api.prepStatus(token, activeJobId);
      setStatus(nextStatus);
      await refreshReadiness();
      await refreshActiveJob();
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsPreparing(false);
    }
  };

  const goNext = () => {
    const idx = STEPS.indexOf(step);
    if (idx < STEPS.length - 1) setStep(STEPS[idx + 1]);
  };
  const goBack = () => {
    const idx = STEPS.indexOf(step);
    if (idx > 0) setStep(STEPS[idx - 1]);
  };

  if (isLoading) {
    return (
      <div className="wizard">
        <p className="wizard-loading">Loading your setup…</p>
      </div>
    );
  }

  // Returning-user landing: CV + JD + prep done. Show a focused summary
  // card instead of dumping them back in the wizard.
  if (setupReady && !isPreparing && !overrideReady) {
    return (
      <ReadyLanding
        cv={cv}
        job={selectedJob}
        techDocCount={technicalDocs.length}
        activeJobParsed={
          activeJob?.parsed_json as { title?: string; company_name?: string } | null | undefined
        }
        onStart={() => navigate("/interview")}
        onAddJob={() => {
          setOverrideReady(true);
          setStep("jd");
        }}
        onAddDocs={() => {
          setOverrideReady(true);
          setStep("docs");
        }}
        onManage={() => navigate("/setup/manage")}
      />
    );
  }

  return (
    <div className="wizard">
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

      <header className="wizard-header">
        <div className="wizard-progress">
          {STEPS.map((s, i) => (
            <span
              key={s}
              className={`wizard-dot${i === stepIndex ? " active" : ""}${i < stepIndex ? " done" : ""}`}
              title={STEP_TITLES[s]}
            />
          ))}
        </div>
        <span className="wizard-step-counter">
          Step {stepIndex + 1} of {STEPS.length}
        </span>
      </header>

      <div className="wizard-stage">
        <h1 className="wizard-title">{STEP_TITLES[step]}</h1>
        <p className="wizard-blurb">{STEP_BLURBS[step]}</p>

        {message ? <div className="success-banner">{message}</div> : null}
        <ErrorBanner code={error} />

        {step === "cv" ? <StepCv cv={cv} onPick={uploadCv} /> : null}

        {step === "jd" ? (
          <StepJd
            mode={jdMode}
            setMode={setJdMode}
            onPaste={submitJobText}
            onUrl={submitJobUrl}
            currentJob={selectedJob}
            jobs={jobs}
            onSelectJob={(id) => setActiveJobId(id)}
          />
        ) : null}

        {step === "docs" ? (
          <StepDocs
            docs={technicalDocs}
            onPick={uploadProjectDoc}
            profileReady={status?.profile_ready ?? false}
            pendingMappingDocId={pendingMappingDocId}
            onMap={(id) => {
              if (status?.profile_ready) setMappingDocId(id);
              else setPendingMappingDocId(id);
            }}
          />
        ) : null}

        {step === "prep" ? (
          <StepPrep
            status={status}
            isPreparing={isPreparing}
            onPrep={() => runPrep(false)}
            onRefreshCompany={() => runPrep(true)}
            nodeState={nodeState}
          />
        ) : null}
      </div>

      <footer className="wizard-footer">
        <button
          className="btn-quiet"
          type="button"
          onClick={goBack}
          disabled={stepIndex === 0 || isPreparing}
        >
          <ArrowLeft size={14} /> Back
        </button>

        <div className="wizard-footer-right">
          {step === "docs" ? (
            <button className="btn-ghost" type="button" onClick={goNext}>
              Skip
            </button>
          ) : null}

          {step === "prep" ? (
            isSetupComplete ? (
              <button
                className="btn-primary"
                type="button"
                onClick={() => navigate("/interview")}
              >
                Start practicing <ArrowRight size={14} />
              </button>
            ) : null
          ) : step === "jd" ? (
            <button
              className="btn-primary"
              type="button"
              onClick={goNext}
              disabled={!selectedJob}
              title={selectedJob ? undefined : "Save a JD first"}
            >
              Continue <ArrowRight size={14} />
            </button>
          ) : step === "docs" ? (
            <button className="btn-primary" type="button" onClick={goNext}>
              Continue <ArrowRight size={14} />
            </button>
          ) : (
            <button
              className="btn-primary"
              type="button"
              onClick={goNext}
              disabled={!hasCv}
            >
              Continue <ArrowRight size={14} />
            </button>
          )}
        </div>
      </footer>

      <div className="wizard-aside">
        <Link to="/setup/manage" className="btn-quiet">
          Manage CV, JDs &amp; docs
        </Link>
      </div>
    </div>
  );
}

// ─────────────────────────── Step components ──────────────────────────────

function StepCv({
  cv,
  onPick,
}: {
  cv?: DocumentItem;
  onPick: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="wizard-body">
      <label className="dropzone">
        <FileUp size={28} />
        <span className="dropzone-title">
          {cv ? "Replace your CV" : "Upload your CV"}
        </span>
        <span className="dropzone-sub">PDF or DOCX</span>
        <input type="file" accept=".pdf,.docx" onChange={onPick} hidden />
      </label>
      {cv ? (
        <p className="wizard-note">
          <CheckCircle2 size={14} /> {cv.filename} · {cv.char_count.toLocaleString()} chars
        </p>
      ) : null}
    </div>
  );
}

function StepJd({
  mode,
  setMode,
  onPaste,
  onUrl,
  currentJob,
  jobs,
  onSelectJob,
}: {
  mode: "paste" | "url";
  setMode: (m: "paste" | "url") => void;
  onPaste: (e: FormEvent<HTMLFormElement>) => void;
  onUrl: (e: FormEvent<HTMLFormElement>) => void;
  currentJob: JobItem | null;
  jobs: JobItem[];
  onSelectJob: (id: string) => void;
}) {
  return (
    <div className="wizard-body">
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
        <form className="wizard-form" onSubmit={onPaste}>
          <textarea
            name="jd_text"
            rows={10}
            placeholder="Paste the role description here…"
            autoFocus
          />
          <button className="btn-secondary" type="submit">
            Save JD
          </button>
        </form>
      ) : (
        <form className="wizard-form" onSubmit={onUrl}>
          <div className="input-with-icon">
            <LinkIcon size={16} />
            <input name="jd_url" type="url" placeholder="https://…" autoFocus />
          </div>
          <button className="btn-secondary" type="submit">
            Fetch and save
          </button>
        </form>
      )}

      {jobs.length > 0 ? (
        <div className="wizard-job-list">
          <span className="wizard-job-list-label">Saved JDs</span>
          {jobs.map((j) => {
            const isCurrent = j.id === currentJob?.id;
            const snippet = j.preview ? j.preview.replace(/\s+/g, " ").slice(0, 90) : "";
            const headline = j.source_url
              ? j.source_url.replace(/^https?:\/\//, "").slice(0, 70)
              : snippet
                ? `"${snippet}…"`
                : "Pasted JD";
            return (
              <button
                key={j.id}
                type="button"
                className={`wizard-job-item${isCurrent ? " current" : ""}`}
                onClick={() => onSelectJob(j.id)}
              >
                <span className="wizard-job-item-label">{headline}</span>
                <span className="wizard-job-item-meta">
                  {j.char_count.toLocaleString()} chars
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function StepDocs({
  docs,
  onPick,
  profileReady,
  pendingMappingDocId,
  onMap,
}: {
  docs: DocumentItem[];
  onPick: (e: ChangeEvent<HTMLInputElement>) => void;
  profileReady: boolean;
  pendingMappingDocId: string | null;
  onMap: (id: string) => void;
}) {
  return (
    <div className="wizard-body">
      <label className="dropzone">
        <FileUp size={24} />
        <span className="dropzone-title">Add a project doc</span>
        <span className="dropzone-sub">Optional — PDF or DOCX</span>
        <input type="file" accept=".pdf,.docx" onChange={onPick} hidden />
      </label>

      {pendingMappingDocId && !profileReady ? (
        <div className="deferred-mapping-card">
          <RefreshCw size={16} className="spin" />
          <div>
            <strong>Profile is still building.</strong>
            <span> Mapping will open as soon as it's ready.</span>
          </div>
        </div>
      ) : null}

      {docs.length > 0 ? (
        <div className="wizard-doc-list">
          {docs.map((d) => {
            const pill = embeddingPillProps(d.embedding_status);
            return (
              <div key={d.id} className="wizard-doc-item">
                <strong>{d.filename}</strong>
                <span className="wizard-doc-meta">
                  {d.project_title ? `"${d.project_title}"` : "Unmapped"}
                </span>
                {pill ? <StatusPill tone={pill.tone}>{pill.label}</StatusPill> : null}
                <button className="btn-quiet" type="button" onClick={() => onMap(d.id)}>
                  Re-map
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function StepPrep({
  status,
  isPreparing,
  onPrep,
  onRefreshCompany,
  nodeState,
}: {
  status: PrepStatus | null;
  isPreparing: boolean;
  onPrep: () => void;
  onRefreshCompany: () => void;
  nodeState: Record<string, string>;
}) {
  const ready = status?.can_start ?? false;
  const showNodes = Object.keys(nodeState).length > 0;

  return (
    <div className="wizard-body">
      {showNodes ? (
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
      ) : (
        <div className="prep-checks">
          <Check label="CV uploaded" ok={status?.has_cv} />
          <Check label="Profile built" ok={status?.profile_ready} />
          <Check label="JD analyzed" ok={status?.job_analyzed} />
          <Check label="Company researched" ok={status?.company_researched} />
        </div>
      )}

      <div className="wizard-actions">
        {!ready ? (
          <button
            className="btn-primary"
            type="button"
            onClick={onPrep}
            disabled={isPreparing}
          >
            <CheckCircle2 size={14} />
            {isPreparing ? "Preparing…" : "Run prep"}
          </button>
        ) : null}
        <button
          className="btn-ghost"
          type="button"
          onClick={onRefreshCompany}
          disabled={isPreparing}
          title="Re-runs only the company researcher; keeps your profile and JD analysis as-is."
        >
          <RefreshCw size={14} /> Refresh company info
        </button>
      </div>
    </div>
  );
}

function ReadyLanding({
  cv,
  job,
  techDocCount,
  activeJobParsed,
  onStart,
  onAddJob,
  onAddDocs,
  onManage,
}: {
  cv: DocumentItem | undefined;
  job: JobItem | null;
  techDocCount: number;
  activeJobParsed: { title?: string; company_name?: string } | null | undefined;
  onStart: () => void;
  onAddJob: () => void;
  onAddDocs: () => void;
  onManage: () => void;
}) {
  const role = activeJobParsed?.title;
  const company = activeJobParsed?.company_name;
  return (
    <div className="ready-landing">
      <span className="ready-eyebrow">Ready to practice</span>
      <h1 className="ready-title">
        {role || "Role"} <span className="ready-at">@</span> {company || "Company"}
      </h1>
      <div className="ready-meta">
        {cv ? <span>CV · {cv.filename}</span> : null}
        {job ? (
          <span>
            JD · {job.source_url || (job.preview ? `"${job.preview.slice(0, 80)}…"` : "Pasted")}
          </span>
        ) : null}
        <span>
          {techDocCount === 0
            ? "No supporting docs"
            : `${techDocCount} supporting doc${techDocCount === 1 ? "" : "s"}`}
        </span>
      </div>
      <div className="ready-actions">
        <button className="btn-primary" type="button" onClick={onStart}>
          Start a practice round <ArrowRight size={14} />
        </button>
        <div className="ready-actions-row">
          <button className="btn-ghost" type="button" onClick={onAddJob}>
            Add another job
          </button>
          <button className="btn-ghost" type="button" onClick={onAddDocs}>
            Add supporting doc
          </button>
        </div>
        <button className="btn-quiet" type="button" onClick={onManage}>
          Manage CV, JDs &amp; docs
        </button>
      </div>
    </div>
  );
}

// ─────────────────────────── small helpers ────────────────────────────────

function Check({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <div className={`prep-check${ok ? " ok" : ""}`}>
      <span className="prep-check-dot" />
      <span>{label}</span>
    </div>
  );
}

function embeddingPillProps(status: EmbeddingStatus | undefined): {
  tone: "good" | "warn" | "bad" | "neutral";
  label: string;
} | null {
  switch (status) {
    case "ready":
      return { tone: "good", label: "Embeddings ready" };
    case "pending":
      return { tone: "warn", label: "Embedding…" };
    case "failed":
      return { tone: "bad", label: "Embedding failed" };
    case "n_a":
      return { tone: "neutral", label: "Not yet mapped" };
    default:
      return null;
  }
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
