import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileUp,
  LinkIcon,
  RefreshCw,
} from "lucide-react";
import { Link, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";

import {
  DocIntakeExtracted,
  DocumentItem,
  EmbeddingStatus,
  JobItem,
  MappingRow,
  MappingSuggestion,
  PrepStatus,
  SseFrame,
  api,
  prepareSessionResumeStream,
  prepareSessionStream,
} from "../api";
import { LoadingStatus } from "../components/LoadingStatus";
import { MappingPanel } from "../components/MappingPanel";
import { ErrorBanner, StatusPill } from "../components/ui";
import { codeFrom } from "../errors";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

const nodeLabels: Record<string, string> = {
  profile_builder: "Reading your CV",
  doc_mapping: "Mapping supporting docs",
  job_analyzer: "Analyzing the JD",
  company_researcher: "Researching the company",
};

const nodeLoadingMessages: Record<string, string[]> = {
  profile_builder: [
    "Reading your CV",
    "Finding signal in your projects",
    "Building candidate profile",
  ],
  doc_mapping: [
    "Reading your supporting docs",
    "Suggesting where each one fits",
    "Waiting for your confirmation",
  ],
  job_analyzer: [
    "Parsing role expectations",
    "Extracting must-have skills",
    "Mapping interview focus",
  ],
  company_researcher: [
    "Scanning company context",
    "Collecting recent signals",
    "Preparing company notes",
  ],
};

const PREP_NODE_KEYS = ["profile_builder", "doc_mapping", "job_analyzer", "company_researcher"];

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
  prep: "Reads your CV, walks supporting docs, analyzes the JD, researches the company.",
};

// ─────────────────────────── Page ─────────────────────────────────────────

export function SetupPage() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const { refreshReadiness } = useOutletContext<SetupOutletContext>();
  const { activeJobId, activeJob, setActiveJobId, refresh: refreshActiveJob } = useActiveJob();
  const prepAbort = useStreamAbort();
  const [searchParams, setSearchParams] = useSearchParams();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [status, setStatus] = useState<PrepStatus | null>(null);
  // Phase 22: which job the held status payload describes. Auto-prep
  // refuses to fire until this matches `activeJobId` — kills the race
  // where Stage-2 saves a new job, status is still stale for the
  // previous job, and the effect prematurely fires prep against the
  // wrong target.
  const [statusJobId, setStatusJobId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPreparing, setIsPreparing] = useState(false);
  const [nodeState, setNodeState] = useState<Record<string, string>>({});
  // The mapping suggestion the prep_graph paused on, if any. When non-null,
  // <StepPrep /> renders the inline MappingPanel; on user submit we POST
  // /sessions/prepare/resume which re-opens the SSE stream.
  const [pendingMapping, setPendingMapping] = useState<MappingSuggestion | null>(null);
  const [step, setStep] = useState<Step>("cv");
  const [didInitStep, setDidInitStep] = useState(false);
  // Phase 22: when the user explicitly enters the wizard from
  // ReadyLanding (?new_job=1 / ?add_doc=1) or by uploading a new
  // supporting doc, we suppress the landing short-circuit so they
  // can actually see the step they navigated to. Auto-cleared once
  // prep completes and there's no outstanding work (the natural
  // recompute then shows the landing). This is the targeted
  // replacement for the deleted ``overrideReady`` flag — same intent
  // (keep the wizard visible while there's pending wizard work),
  // narrower scope (auto-clears, not sticky).
  const [bypassLanding, setBypassLanding] = useState(false);
  const [jdMode, setJdMode] = useState<"paste" | "url">("paste");
  const messageTimerRef = useRef<number | null>(null);
  // Phase 22: auto-prep is work-driven, not fire-once. The ref stores a
  // (job, work-signature) tuple so the effect re-arms whenever the
  // outstanding work changes (e.g. user uploads a new project_doc and
  // `unmapped_project_doc_count` ticks up).
  const lastAutoPrepKeyRef = useRef<string | null>(null);

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
      const [nextDocs, nextJobs] = await Promise.all([
        api.listDocuments(token),
        api.listJobs(token),
      ]);
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
      setStatusJobId(null);
      return;
    }
    api
      .prepStatus(token, activeJobId)
      .then((s) => {
        setStatus(s);
        setStatusJobId(activeJobId);
      })
      .catch(() => {
        setStatus(null);
        setStatusJobId(null);
      });
  }, [token, activeJobId]);

  // Phase 22: query-param navigation from ReadyLanding. Consume the
  // params each time they change so re-entering /setup with a different
  // param (`?new_job=1` then `?add_doc=1`) lands the user correctly.
  // We strip the params after handling so the effect doesn't loop.
  useEffect(() => {
    if (isLoading) return;
    const newJob = searchParams.get("new_job");
    const addDoc = searchParams.get("add_doc");
    if (!newJob && !addDoc) return;
    if (newJob) {
      setStep("jd");
      setActiveJobId(null);
    } else if (addDoc) {
      setStep("docs");
    }
    setBypassLanding(true);
    setDidInitStep(true);
    const next = new URLSearchParams(searchParams);
    next.delete("new_job");
    next.delete("add_doc");
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, searchParams]);

  // One-shot initial step picker — runs once after the first load.
  useEffect(() => {
    if (isLoading || didInitStep) return;
    if (!hasCv) setStep("cv");
    else if (!selectedJob) setStep("jd");
    else if (!status?.can_start) setStep("prep");
    setDidInitStep(true);
  }, [isLoading, didInitStep, hasCv, selectedJob, status?.can_start]);

  useEffect(() => {
    if (!message) return;
    if (messageTimerRef.current !== null) window.clearTimeout(messageTimerRef.current);
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
  useEffect(() => setMessage(null), [step]);

  const handlePrepFrame = (frame: SseFrame) => {
    const data = frame.data as {
      node?: string;
      reason?: string;
      code?: string;
      detail?: string;
      document_id?: string;
      payload?: MappingSuggestion;
      remaining?: number;
      n_rows?: number;
    };
    if (frame.event === "node_started" && data.node) {
      setNodeState((c) => ({ ...c, [data.node!]: "running" }));
    } else if (frame.event === "node_done" && data.node) {
      setNodeState((c) => ({ ...c, [data.node!]: "done" }));
    } else if (frame.event === "node_skipped" && data.node) {
      setNodeState((c) => ({ ...c, [data.node!]: "cached" }));
    } else if (frame.event === "mapping_suggestion" && data.payload) {
      // Mark doc_mapping as running and show the inline mapping panel.
      setNodeState((c) => ({ ...c, doc_mapping: "running" }));
      setPendingMapping(data.payload);
    } else if (frame.event === "mapping_suggestion_failed") {
      // The intake LLM call failed for this doc; the graph already
      // skiplisted it. Surface a soft warning; the loop carries on.
      setMessage(`Couldn't read one of your project docs. Skipped.`);
    } else if (frame.event === "mapping_applied") {
      setPendingMapping(null);
    } else if (frame.event === "mapping_skipped") {
      setPendingMapping(null);
    } else if (frame.event === "mapping_apply_failed") {
      setPendingMapping(null);
      setError(data.code || data.detail || "mapping_apply_failed");
    } else if (frame.event === "awaiting_mapping") {
      // Stream ended pending user input; pendingMapping already set above.
    } else if (frame.event === "done") {
      setNodeState((c) => {
        const next = { ...c };
        for (const k of PREP_NODE_KEYS) {
          if (!next[k]) next[k] = "done";
        }
        return next;
      });
      setPendingMapping(null);
    } else if (frame.event === "error") {
      setError(data.code || data.detail || "stream_interrupted");
    }
  };

  const runPrep = async (forceRefresh: boolean) => {
    if (!token || !activeJobId) return;
    setIsPreparing(true);
    setNodeState({
      profile_builder: "pending",
      doc_mapping: "pending",
      job_analyzer: "pending",
      company_researcher: "pending",
    });
    setError(null);
    setMessage(null);
    setPendingMapping(null);
    const signal = prepAbort.fresh();
    try {
      await prepareSessionStream(token, activeJobId, forceRefresh, handlePrepFrame, signal);
      await load();
      const nextStatus = await api.prepStatus(token, activeJobId);
      setStatus(nextStatus);
      setStatusJobId(activeJobId);
      await refreshReadiness();
      await refreshActiveJob();
      // Phase 22: clear the wizard-bypass once prep is genuinely
      // complete — the natural re-render then drops the user back on
      // ReadyLanding. We don't clear on mapping-pending state because
      // the MappingPanel lives inside the wizard.
      if (nextStatus.can_start && (nextStatus.unmapped_project_doc_count ?? 0) === 0) {
        setBypassLanding(false);
      }
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsPreparing(false);
    }
  };

  const resumeMapping = async (decision:
    | { action: "apply"; rows: MappingRow[]; title: string; extracted: DocIntakeExtracted }
    | { action: "skip" }) => {
    if (!token || !activeJobId) return;
    setIsPreparing(true);
    setPendingMapping(null);
    const signal = prepAbort.fresh();
    try {
      await prepareSessionResumeStream(
        token,
        { job_id: activeJobId, ...decision },
        handlePrepFrame,
        signal,
      );
      await load();
      const nextStatus = await api.prepStatus(token, activeJobId);
      setStatus(nextStatus);
      setStatusJobId(activeJobId);
      await refreshReadiness();
      if (nextStatus.can_start && (nextStatus.unmapped_project_doc_count ?? 0) === 0) {
        setBypassLanding(false);
      }
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsPreparing(false);
    }
  };

  // Phase 22: work-driven auto-prep. Fires whenever there's outstanding
  // setup work that prep would resolve — either a missing
  // profile/job-analysis/snapshot OR a project_doc the user uploaded
  // and hasn't mapped yet. The (job, signature) key re-arms when work
  // legitimately changes, so uploading a new supporting doc from
  // ReadyLanding flows straight through prep without a manual click.
  // Guards:
  //   - status must match the active job (`statusJobId === activeJobId`)
  //     so a Stage-2 race doesn't fire prep against the prior job;
  //   - skip the CV step — the user hasn't told us anything to prep yet;
  //   - in-flight prep takes priority over re-firing.
  useEffect(() => {
    if (!token || !activeJobId || !hasCv) return;
    if (isPreparing) return;
    if (step === "cv") return;
    if (statusJobId !== activeJobId) return;
    if (!status) return;
    const unmapped = status.unmapped_project_doc_count ?? 0;
    const needsPrep = !status.can_start || unmapped > 0;
    if (!needsPrep) return;
    const key = `${activeJobId}:${status.can_start ? 1 : 0}:${unmapped}`;
    if (lastAutoPrepKeyRef.current === key) return;
    lastAutoPrepKeyRef.current = key;
    void runPrep(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeJobId, hasCv, status, statusJobId, step]);

  const uploadCv = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!token || !event.target.files?.[0]) return;
    setMessage(null);
    setError(null);
    try {
      await api.uploadDocument(token, "cv", event.target.files[0]);
      setMessage("Got it.");
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
      setMessage(`Uploaded ${doc.filename}. We'll ask where it fits during prep.`);
      await load();
      // Phase 22: the upload bumps `unmapped_project_doc_count`, so the
      // work-driven auto-prep effect fires once the next status refresh
      // surfaces it. Routing to "prep" here removes the manual Continue
      // click that the old wizard required.
      if (activeJobId) {
        const nextStatus = await api.prepStatus(token, activeJobId);
        setStatus(nextStatus);
        setStatusJobId(activeJobId);
        setStep("prep");
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

  // Phase 22: landing recomputes from real readiness every render, but
  // ``bypassLanding`` keeps the wizard visible while the user is in
  // the middle of an explicit wizard task they navigated to (Add
  // another job, Add supporting doc, mid-stream prep). Cleared when
  // prep completes and there's no outstanding mapping work.
  const hasUnmapped = (status?.unmapped_project_doc_count ?? 0) > 0;
  if (setupReady && !isPreparing && !hasUnmapped && !bypassLanding) {
    return (
      <ReadyLanding
        cv={cv}
        job={selectedJob}
        techDocCount={technicalDocs.length}
        activeJobParsed={
          activeJob?.parsed_json as { title?: string; company_name?: string } | null | undefined
        }
        onStart={() => navigate("/interview")}
        onAddJob={() => navigate("/setup?new_job=1")}
        onAddDocs={() => navigate("/setup?add_doc=1")}
        onManage={() => navigate("/setup/manage")}
      />
    );
  }

  return (
    <div className="wizard">
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

        {step === "docs" ? <StepDocs docs={technicalDocs} onPick={uploadProjectDoc} /> : null}

        {step === "prep" ? (
          <StepPrep
            status={status}
            isPreparing={isPreparing}
            onPrep={() => runPrep(false)}
            onRefreshCompany={() => runPrep(true)}
            nodeState={nodeState}
            pendingMapping={pendingMapping}
            onMappingDecision={resumeMapping}
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

          {/* Phase 22: the wizard footer never shows "Start practicing".
              ReadyLanding owns that affordance — once setup is genuinely
              complete the page re-renders and the landing replaces this
              footer entirely. */}
          {step === "prep" ? null : step === "jd" ? (
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
            <button className="btn-primary" type="button" onClick={goNext} disabled={!hasCv}>
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
        <span className="dropzone-title">{cv ? "Replace your CV" : "Upload your CV"}</span>
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
          <textarea name="jd_text" rows={10} placeholder="Paste the role description here…" autoFocus />
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
                <span className="wizard-job-item-meta">{j.char_count.toLocaleString()} chars</span>
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
}: {
  docs: DocumentItem[];
  onPick: (e: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div className="wizard-body">
      <label className="dropzone">
        <FileUp size={24} />
        <span className="dropzone-title">Add a project doc</span>
        <span className="dropzone-sub">Optional — PDF or DOCX</span>
        <input type="file" accept=".pdf,.docx" onChange={onPick} hidden />
      </label>

      {docs.length > 0 ? (
        <div className="wizard-doc-list">
          {docs.map((d) => {
            const pill = embeddingPillProps(d.embedding_status);
            return (
              <div key={d.id} className="wizard-doc-item">
                <strong>{d.filename}</strong>
                <span className="wizard-doc-meta">
                  {d.project_title ? `"${d.project_title}"` : "Unmapped — we'll ask in the next step"}
                </span>
                {pill ? <StatusPill tone={pill.tone}>{pill.label}</StatusPill> : null}
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
  pendingMapping,
  onMappingDecision,
}: {
  status: PrepStatus | null;
  isPreparing: boolean;
  onPrep: () => void;
  onRefreshCompany: () => void;
  nodeState: Record<string, string>;
  pendingMapping: MappingSuggestion | null;
  onMappingDecision: (
    d:
      | { action: "apply"; rows: MappingRow[]; title: string; extracted: DocIntakeExtracted }
      | { action: "skip" },
  ) => void;
}) {
  const ready = status?.can_start ?? false;
  const showNodes = Object.keys(nodeState).length > 0;

  return (
    <div className="wizard-body">
      {pendingMapping ? (
        <MappingPanel suggestion={pendingMapping} onDecision={onMappingDecision} disabled={isPreparing} />
      ) : null}

      {showNodes ? (
        <div className="node-list">
          {PREP_NODE_KEYS.map((key) => (
            <TaskStatus
              key={key}
              label={nodeLabels[key]}
              state={nodeState[key] ?? "pending"}
              messages={nodeLoadingMessages[key] ?? [`Preparing ${nodeLabels[key].toLowerCase()}`]}
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

      {!pendingMapping ? (
        <div className="wizard-actions">
          {!ready ? (
            <button className="btn-primary" type="button" onClick={onPrep} disabled={isPreparing}>
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
      ) : null}
    </div>
  );
}
// ─────────────────────────── Ready landing + helpers ─────────────────────

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
