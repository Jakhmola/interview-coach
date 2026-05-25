import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, CheckCircle2, FileUp, LinkIcon } from "lucide-react";
import { Link, useNavigate, useOutletContext, useSearchParams } from "react-router-dom";

import {
  DocIntakeExtracted,
  DocumentItem,
  EmbeddingStatus,
  JobItem,
  MappingRow,
  MappingSuggestion,
  PrepNodeOutcome,
  PrepRunReason,
  PrepSkipReason,
  PrepStatus,
  SseFrame,
  api,
  prepareSessionResumeStream,
  prepareSessionStream,
} from "../api";
import { LoadingStatus } from "../components/LoadingStatus";
import { MappingModal } from "../components/MappingModal";
import { ErrorBanner, StatusPill } from "../components/ui";
import { codeFrom } from "../errors";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { jobLabel } from "../jobLabel";
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

// Per-node prep state: the settled pill plus the verdict reason that rode in on
// node_started/node_skipped (Phase 27 protocol). node_done merges — it updates
// the pill but preserves the reason captured at start (Phase 28).
type NodePill = { pill: string; reason?: PrepRunReason | PrepSkipReason };

// Phase 28: terminal-state sub-label for the run reasons that tell a story.
// Total over (node, reason, pill) — any combo not listed returns null so
// TaskStatus keeps its plain fallback. Targets the real firing set traced
// through prep_cache.py: `stale` only fires on profile_builder; `degraded`
// only on company_researcher. `missing` and every skip reason mean "reused /
// nothing changed" → no added copy on the fresh-setup happy path. (`forced`
// has no UI trigger — the "Refresh company info" button was removed — so it is
// deliberately unhandled; if the backend ever emits it, it degrades to plain
// copy rather than rendering a button-driven message no path can produce.)
//
// Company's degraded run is the one conflict case: outcome wins. A degraded-run
// that settled `done` announces the self-heal ("Recovered…"); a degraded-run
// that settled `degraded` returns null and defers to the existing Phase-27
// "Completed with warnings" pill + toast, so the user never sees two messages.
export function nodeReasonLabel(
  node: string,
  reason: PrepRunReason | PrepSkipReason | undefined,
  pill: string,
): string | null {
  if (node === "profile_builder" && reason === "stale" && pill === "done") {
    return "Rebuilt — your documents changed";
  }
  if (node === "company_researcher" && reason === "degraded" && pill === "done") {
    return "Recovered — earlier company info was incomplete";
  }
  return null;
}

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
  const { activeJobId, jobs, setActiveJobId, refresh: refreshActiveJob } = useActiveJob();
  const prepAbort = useStreamAbort();
  const [searchParams, setSearchParams] = useSearchParams();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
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
  const [nodeState, setNodeState] = useState<Record<string, NodePill>>({});
  // The mapping suggestion the prep_graph paused on, if any. When non-null,
  // the page renders <MappingModal /> at the wizard top level; on user
  // submit we POST /sessions/prepare/resume which re-opens the SSE stream.
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
  // Job ids whose most recent auto-prep ended in an SSE ``error``
  // event. Phase 22: company-research soft errors no longer surface
  // here (they're swallowed inside the graph node and reported as a
  // degraded ``node_done``), so this set fills only on genuinely
  // fatal errors — ``NoDocumentsError`` or downstream LLM failures.
  // We refuse to auto-refire for jobs in the set until the user
  // takes an explicit action (manual Run prep click, Re-analyze from
  // Manage, etc.).
  const failedAutoPrepJobsRef = useRef<Set<string>>(new Set());

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
      // Phase 22: jobs live on ActiveJobContext now — refreshing it
      // updates the sidebar dropdown + Setup wizard + Manage + the
      // landing in one go. We only own ``docs`` locally.
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

  useEffect(() => {
    if (!token || !activeJobId) {
      setStatus(null);
      setStatusJobId(null);
      return;
    }
    // Phase 25 (B9): if a prep stream is in flight against the prior
    // job when the user switches, abort it cleanly. Without this the
    // tail of the prior stream keeps writing into setStatus /
    // setNodeState / failedAutoPrepJobsRef under the *new* activeJobId
    // — node pills appear to belong to the new job, failure flags get
    // mis-attributed (already addressed for runPrep by B7's pinning,
    // but the abort is what stops the bytes flowing).
    if (isPreparing) {
      prepAbort.abort();
      setIsPreparing(false);
      setPendingMapping(null);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const handlePrepFrameFor = (frame: SseFrame, runJobId: string) => {
    // Lifecycle fields (node/reason/outcome/code/detail) are typed against the
    // prep_events contract; the mapping sub-protocol fields (document_id,
    // payload, remaining, n_rows) ride the same handler and stay loose.
    const data = frame.data as {
      node?: string;
      reason?: PrepRunReason | PrepSkipReason;
      outcome?: PrepNodeOutcome;
      code?: string;
      detail?: string;
      document_id?: string;
      payload?: MappingSuggestion;
      remaining?: number;
      n_rows?: number;
    };
    if (frame.event === "node_started" && data.node) {
      // Keep the run reason — node_done merges it through to the settled pill.
      setNodeState((c) => ({
        ...c,
        [data.node!]: { pill: "running", reason: data.reason },
      }));
    } else if (frame.event === "node_done" && data.node) {
      // Phase 27: a node_done with ``outcome === "degraded"`` is a
      // soft-fail (currently only company_researcher when the JD has
      // no company_name or no search hits). Mark the pill differently
      // and surface a non-blocking message so the user knows to fix
      // it via Manage → Re-analyze if they want, but can still
      // proceed.
      const pill = data.outcome === "degraded" ? "degraded" : "done";
      // Merge: settle the pill but preserve the reason from node_started so
      // nodeReasonLabel can render the terminal sub-label (Phase 28).
      setNodeState((c) => ({ ...c, [data.node!]: { ...c[data.node!], pill } }));
      if (data.outcome === "degraded" && data.code) {
        setMessage(
          data.code === "CompanyNameMissing"
            ? "Couldn't extract a company name from this JD — questions will be less company-specific. Fix it any time via Manage → Re-analyze."
            : "Company research came up empty — questions will be less company-specific. Try again later from Manage.",
        );
      }
    } else if (frame.event === "node_skipped" && data.node) {
      // Phase 25: a doc_mapping skip is the loop finishing (no unmapped
      // docs left, or a company-only refresh) — never a cache hit. The
      // mapping it just walked ran fresh, so don't render "Using cached
      // result". Only profile/JD/company skips are genuine cache hits.
      const pill = data.node === "doc_mapping" ? "done" : "cached";
      setNodeState((c) => ({ ...c, [data.node!]: { pill, reason: data.reason } }));
    } else if (frame.event === "mapping_suggestion" && data.payload) {
      // Mark doc_mapping as running and show the inline mapping panel.
      setNodeState((c) => ({ ...c, doc_mapping: { ...c.doc_mapping, pill: "running" } }));
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
          if (!next[k]) next[k] = { pill: "done" };
        }
        return next;
      });
      setPendingMapping(null);
    } else if (frame.event === "error") {
      setError(data.code || data.detail || "stream_interrupted");
      // Phase 22: a node-level error never emits ``node_done`` for the
      // failed node, so the task pill would otherwise keep spinning
      // forever even after the stream closes. Mark the failing node
      // (when named) as failed and any remaining pending nodes as
      // skipped so the UI settles instead of looking stuck. Also mark
      // the job as auto-prep-failed so the work-driven effect doesn't
      // re-fire the same failing run on every mount.
      setNodeState((c) => {
        const next = { ...c };
        if (data.node) next[data.node] = { ...next[data.node], pill: "failed" };
        for (const k of PREP_NODE_KEYS) {
          if (next[k]?.pill === "pending" || next[k]?.pill === "running") {
            if (k !== data.node) next[k] = { ...next[k], pill: "skipped" };
          }
        }
        return next;
      });
      failedAutoPrepJobsRef.current.add(runJobId);
    }
  };

  const runPrep = async () => {
    if (!token || !activeJobId) return;
    // Phase 25 (B7): pin the job id we're prepping at function entry.
    // If the user switches the active job mid-stream, every write —
    // status, failure flag, SSE error handler — still references the
    // job the run was *for*, not whatever's active when the closure
    // fires. Otherwise a failure on job A could be recorded against
    // job B and a successful run on A could refresh job B's status.
    const runJobId = activeJobId;
    setIsPreparing(true);
    setNodeState({
      profile_builder: { pill: "pending" },
      doc_mapping: { pill: "pending" },
      job_analyzer: { pill: "pending" },
      company_researcher: { pill: "pending" },
    });
    setError(null);
    setMessage(null);
    setPendingMapping(null);
    failedAutoPrepJobsRef.current.delete(runJobId);
    const signal = prepAbort.fresh();
    const frameHandler = (frame: SseFrame) => handlePrepFrameFor(frame, runJobId);
    try {
      await prepareSessionStream(token, runJobId, frameHandler, signal);
      await load();
      const nextStatus = await api.prepStatus(token, runJobId);
      setStatus(nextStatus);
      setStatusJobId(runJobId);
      await refreshReadiness();
      await refreshActiveJob();
      if (nextStatus.can_start && (nextStatus.unmapped_project_doc_count ?? 0) === 0) {
        setBypassLanding(false);
        failedAutoPrepJobsRef.current.delete(runJobId);
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
    // Phase 25 (B7): same job-id pinning as runPrep — a job switch
    // mid-resume must not corrupt the bookkeeping for the job whose
    // mapping the user just confirmed.
    const runJobId = activeJobId;
    setIsPreparing(true);
    setPendingMapping(null);
    const signal = prepAbort.fresh();
    const frameHandler = (frame: SseFrame) => handlePrepFrameFor(frame, runJobId);
    try {
      await prepareSessionResumeStream(
        token,
        { job_id: runJobId, ...decision },
        frameHandler,
        signal,
      );
      await load();
      const nextStatus = await api.prepStatus(token, runJobId);
      setStatus(nextStatus);
      setStatusJobId(runJobId);
      await refreshReadiness();
      if (nextStatus.can_start && (nextStatus.unmapped_project_doc_count ?? 0) === 0) {
        setBypassLanding(false);
      }
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsPreparing(false);
      // Phase 25 (B8): mapping decided → drop the auto-prep key so a
      // status delta from the just-applied mapping (or a fresh
      // unmapped doc surfaced by it) re-arms the effect cleanly.
      lastAutoPrepKeyRef.current = null;
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
    // Phase 25 (B8): a pending mapping means the prior prep is paused
    // on the HITL interrupt and the modal is open. Re-firing prep
    // would reset the prep_graph thread and destroy the interrupt
    // state mid-modal. Wait for the user's decision (apply/skip)
    // before allowing another run.
    if (pendingMapping) return;
    // Phase 25 (B1): the docs step is where the user is actively
    // adding supporting docs. Auto-prep would yank them out mid-add.
    // The wizard's Continue button is the explicit advance.
    if (step === "cv" || step === "docs") return;
    if (statusJobId !== activeJobId) return;
    if (!status) return;
    // Don't auto-refire on a job that just failed — let the user
    // take an explicit action (manual Run prep, Re-analyze, etc.).
    if (failedAutoPrepJobsRef.current.has(activeJobId)) return;
    const unmapped = status.unmapped_project_doc_count ?? 0;
    const needsPrep = !status.can_start || unmapped > 0;
    if (!needsPrep) return;
    const key = `${activeJobId}:${status.can_start ? 1 : 0}:${unmapped}`;
    if (lastAutoPrepKeyRef.current === key) return;
    lastAutoPrepKeyRef.current = key;
    void runPrep();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeJobId, hasCv, status, statusJobId, step, pendingMapping]);

  const uploadCv = async (event: ChangeEvent<HTMLInputElement>) => {
    if (!token || !event.target.files?.[0]) return;
    setMessage(null);
    setError(null);
    // Phase 25 (B10): capture replace-state pre-upload. A new CV
    // invalidates the profile and every project_doc mapping
    // (handled server-side in upload_document's cv-replace cascade);
    // the user deserves to know their mappings were wiped before
    // they hit prep and the modal re-asks for every doc.
    const wasReplace = docs.some((d) => d.kind === "cv");
    try {
      await api.uploadDocument(token, "cv", event.target.files[0]);
      setMessage(
        wasReplace
          ? "New CV uploaded. Your project doc mappings were cleared — we'll re-ask during prep."
          : "Got it.",
      );
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
      setMessage(`Uploaded ${doc.filename}. Add more or click Continue when you're done.`);
      await load();
      // Phase 25 (B1): stay on the docs step after upload so the user
      // can add additional supporting docs. The wizard's "Continue"
      // button is the explicit advance to prep. (Pre-Phase-25 this
      // routed straight to prep, blocking multi-doc uploads.)
      if (activeJobId) {
        const nextStatus = await api.prepStatus(token, activeJobId);
        setStatus(nextStatus);
        setStatusJobId(activeJobId);
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
      // Phase 25 (#4): commit activeJobId and the docs step in the same
      // render so the auto-prep effect never sees the transient
      // (newJob, step="jd") window. The B1 guard only blocks cv/docs —
      // a job that's active while step is still "jd" auto-fires a full
      // prep with zero docs, skipping doc_mapping, *before* the user can
      // add a supporting doc. Setting step before the await keeps both
      // state updates in one batch.
      setActiveJobId(job.id);
      setStep("docs");
      await load();
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
      // Phase 25 (#4): see submitJobText — commit activeJobId + docs step
      // together so auto-prep can't fire a doc-less prep in the gap.
      setActiveJobId(job.id);
      setStep("docs");
      await load();
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
            onPrep={() => runPrep()}
            nodeState={nodeState}
          />
        ) : null}
      </div>

      {/* Phase 22: mapping HITL renders as a top-level modal so it floats
          above the wizard regardless of which step the user is on. ESC /
          backdrop close = "skip this doc" (matches the modal's Skip button
          and the prep_graph's HITL contract — skipping is the user's
          escape hatch). */}
      <MappingModal
        open={pendingMapping != null}
        suggestion={pendingMapping}
        busy={isPreparing}
        onDecision={resumeMapping}
        onClose={() => resumeMapping({ action: "skip" })}
      />

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
            const headline = jobLabel(j);
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
  nodeState,
}: {
  status: PrepStatus | null;
  isPreparing: boolean;
  onPrep: () => void;
  nodeState: Record<string, NodePill>;
}) {
  const ready = status?.can_start ?? false;
  const showNodes = Object.keys(nodeState).length > 0;

  return (
    <div className="wizard-body">
      {showNodes ? (
        <div className="node-list">
          {PREP_NODE_KEYS.map((key) => (
            <TaskStatus
              key={key}
              node={key}
              label={nodeLabels[key]}
              state={nodeState[key]?.pill ?? "pending"}
              reason={nodeState[key]?.reason}
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

      <div className="wizard-actions">
        {!ready ? (
          <button className="btn-primary" type="button" onClick={onPrep} disabled={isPreparing}>
            <CheckCircle2 size={14} />
            {isPreparing ? "Preparing…" : "Run prep"}
          </button>
        ) : null}
      </div>
    </div>
  );
}
// ─────────────────────────── Ready landing + helpers ─────────────────────

function ReadyLanding({
  cv,
  job,
  techDocCount,
  onStart,
  onAddJob,
  onAddDocs,
  onManage,
}: {
  cv: DocumentItem | undefined;
  job: JobItem | null;
  techDocCount: number;
  onStart: () => void;
  onAddJob: () => void;
  onAddDocs: () => void;
  onManage: () => void;
}) {
  // Phase 22: parsed_json now arrives on the list endpoint too, so
  // ReadyLanding can derive role/company straight from the active
  // JobItem instead of taking a separate ``activeJobParsed`` prop
  // that could drift from what Manage/dropdown render.
  const parsed = (job?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined) ?? null;
  const role = parsed?.title;
  const company = parsed?.company_name;
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
  node,
  label,
  state,
  reason,
  messages,
}: {
  node: string;
  label: string;
  state: string;
  reason?: PrepRunReason | PrepSkipReason;
  messages: string[];
}) {
  const normalizedState = state.startsWith("cached") ? "cached" : state;
  const isActive = normalizedState === "pending" || normalizedState === "running";
  // Phase 28: a story reason (stale rebuild, forced/recovered company) replaces
  // the plain settled copy; everything else returns null and keeps it.
  const fallback =
    nodeReasonLabel(node, reason, normalizedState) ??
    (normalizedState === "done"
      ? "Complete"
      : normalizedState === "cached"
        ? "Using cached result"
        : normalizedState === "running"
          ? "Working"
          : normalizedState === "failed"
            ? "Failed"
            : normalizedState === "skipped"
              ? "Skipped"
              : normalizedState === "degraded"
                ? "Completed with warnings"
                : "Queued");

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
