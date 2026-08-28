import { ChangeEvent, FormEvent, ReactNode, useEffect, useRef, useState } from "react";
import { ArrowLeft, ExternalLink, FileUp, LinkIcon, Plus, RefreshCw, Sparkles, Trash2 } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";

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
import { EmptyState, ErrorBanner, Field, SheetHead, StatusPill, shortDate } from "../components/ui";
import {
  ProfileLite,
  currentRoleOf,
  educationOf,
  filedUnder,
  firstLine,
  leadOf,
  openingOf,
  rolesOf,
  sectionsOf,
  skillsOf,
  summaryOf,
  titleCase,
} from "../digest";
import { codeFrom } from "../errors";
import { jobLabel } from "../jobLabel";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

/**
 * Manage: the file cabinet. An index of everything on file on the left -
 * each entry named by what it is (the candidate, the role, the doc's title,
 * the repo), never by its filename - and, on the right, a reading pane for
 * the selected file: its provenance, what prep read out of it, and its
 * actions (replace, re-analyze, remap, retry, delete). The setup wizard
 * handles "the next thing to do"; this page owns "change something I
 * already gave you". The selection lives in `?file=` so Back leaves the page
 * rather than walking the selections.
 */

type BlockingState = {
  /** Which JD triggered the 409 - the only delete that can return one today
   * is ``DELETE /jobs/{id}`` (Phase 22 dropped Delete CV for Reset account). */
  scope: { kind: "job"; id: string };
  code: "job_in_use";
  sessionIds: string[];
};

type Entry =
  | { id: string; kind: "cv" | "doc" | "repo"; doc: DocumentItem }
  | { id: string; kind: "job"; job: JobItem };

const KIND_LABEL: Record<Entry["kind"], string> = {
  cv: "CV",
  job: "Job description",
  doc: "Supporting doc",
  repo: "GitHub repo",
};

const isString = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter(isString) : []);
const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
const chars = (n: number) => `${n.toLocaleString()} chars`;

export function ManagePage() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  // Jobs come from the shared ActiveJobContext so re-analyze / delete /
  // make-active show up consistently in the header chip and the wizard.
  const { activeJobId, jobs, setActiveJobId, refresh: refreshActiveJob } = useActiveJob();

  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [profile, setProfile] = useState<ProfileLite | null>(null);
  /** Full texts by document / job id, fetched when a file is opened. */
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [showText, setShowText] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [blocking, setBlocking] = useState<BlockingState | null>(null);
  /** Which JD's inline editor is open (re-analyze paste/url). */
  const [editingJobId, setEditingJobId] = useState<string | null>(null);
  /** Which project_doc's remap slip is open. */
  const [remapping, setRemapping] = useState<{ docId: string; suggestion: MappingSuggestion } | null>(null);
  const replaceCvInputRef = useRef<HTMLInputElement | null>(null);

  const cv = docs.find((d) => d.kind === "cv");
  const techDocs = docs.filter((d) => d.kind === "project_doc");
  const githubRepos = docs.filter((d) => d.kind === "github_repo");

  const entries: Entry[] = [
    ...(cv ? [{ id: cv.id, kind: "cv" as const, doc: cv }] : []),
    ...jobs.map((job) => ({ id: job.id, kind: "job" as const, job })),
    ...techDocs.map((doc) => ({ id: doc.id, kind: "doc" as const, doc })),
    ...githubRepos.map((doc) => ({ id: doc.id, kind: "repo" as const, doc })),
  ];
  const fileParam = searchParams.get("file");
  const selected = entries.find((e) => e.id === fileParam) ?? entries[0] ?? null;

  const select = (id: string) => {
    const params = new URLSearchParams(searchParams);
    params.set("file", id);
    setSearchParams(params, { replace: true });
    setShowText(false);
    setEditingJobId(null);
  };

  const load = async () => {
    if (!token) return;
    setIsLoading(true);
    setError(null);
    try {
      const [nextDocs] = await Promise.all([api.listDocuments(token), refreshActiveJob()]);
      setDocs(nextDocs);
      setTexts({});
      // The profile only travels with a job's prep status; any prepped job
      // carries the same one.
      if (activeJobId) {
        const status = await api.prepStatus(token, activeJobId, true).catch(() => null);
        setProfile((status?.profile as ProfileLite | null | undefined) ?? null);
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

  // A selection that no longer exists (deleted, or a stale link) falls back
  // to the first file, and the URL follows.
  useEffect(() => {
    if (isLoading || !fileParam || entries.some((e) => e.id === fileParam)) return;
    const params = new URLSearchParams(searchParams);
    params.delete("file");
    setSearchParams(params, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLoading, fileParam, docs, jobs]);

  // The opened file's full text, once per file.
  useEffect(() => {
    if (!token || !selected || texts[selected.id] !== undefined) return;
    const { id, kind } = selected;
    let cancelled = false;
    const req = kind === "job" ? api.getJob(token, id) : api.getDocument(token, id);
    req
      .then((d) => {
        if (!cancelled) setTexts((t) => ({ ...t, [id]: d.raw_text }));
      })
      .catch(() => {
        if (!cancelled) setTexts((t) => ({ ...t, [id]: "" }));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, selected?.id, texts]);

  // Drop the blocking card whenever the underlying inventory changes - an
  // abandon-and-retry sequence shouldn't leave a stale "still blocked" card
  // pinned after the second delete succeeds.
  useEffect(() => {
    if (!blocking) return;
    if (!jobs.find((j) => j.id === blocking.scope.id)) setBlocking(null);
  }, [jobs, blocking]);

  // ─── delete helpers ────────────────────────────────────────────────
  const captureJobBlocking = (err: unknown, jobId: string): boolean => {
    if (!(err instanceof ApiError) || err.status !== 409) return false;
    const obj = err.detailObject;
    if (!obj || typeof obj !== "object") return false;
    const code = (obj as { code?: unknown }).code;
    const ids = (obj as { blocking_session_ids?: unknown }).blocking_session_ids;
    if (code !== "job_in_use" || !Array.isArray(ids)) return false;
    setBlocking({ scope: { kind: "job", id: jobId }, code, sessionIds: strings(ids) });
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
      // The wizard's work-driven auto-prep picks the rebuild up once we land
      // back there - the user sees prep stream rather than a silent refresh.
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
      setMessage("Embedding again.");
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
      // Last blocking session cleared - retry the original delete.
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
      setActiveJobId(null);
      setDocs([]);
      setProfile(null);
      await refreshActiveJob();
      await load();
      setMessage("Account reset. Nothing on file.");
      navigate("/setup");
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setBusy(null);
    }
  };

  // ─── the index ─────────────────────────────────────────────────────
  const mappedTechDocs = techDocs.filter((d) => d.project_title).length;
  const onFile = [!!cv, jobs.length > 0, techDocs.length > 0, githubRepos.length > 0].filter(Boolean).length;
  const text = selected ? texts[selected.id] : undefined;

  const entryButton = (id: string, title: string, meta?: ReactNode) => {
    const on = selected?.id === id;
    return (
      <button
        key={id}
        type="button"
        className={`entry${on ? " on" : ""}`}
        aria-current={on ? "true" : undefined}
        onClick={() => select(id)}
      >
        <strong>{title}</strong>
        {meta ? <span className="meta">{meta}</span> : null}
      </button>
    );
  };

  return (
    <div className="manage-page">
      <SheetHead title="On file" page={`${onFile} of 4 in the packet`}>
        <Field label="Candidate" value={user?.email} />
      </SheetHead>
      <button className="btn-quiet back" type="button" onClick={() => navigate("/setup")}>
        <ArrowLeft size={14} /> Back to the packet
      </button>

      {message ? <div className="success-banner">{message}</div> : null}
      <ErrorBanner code={error} />

      <div className="cabinet">
        <nav className="index" aria-label="Files">
          <section>
            <div className="head">
              <h2>CV</h2>
              {!cv ? (
                <button className="btn-quiet" type="button" onClick={() => navigate("/setup?step=cv")}>
                  <Plus size={14} /> Upload
                </button>
              ) : null}
            </div>
            {cv ? (
              entryButton(cv.id, firstLine(cv.preview) || "CV", currentRoleOf(profile))
            ) : (
              <p className="none">{isLoading ? "Loading…" : "None yet"}</p>
            )}
          </section>

          <section>
            <div className="head">
              <h2>Job descriptions</h2>
              <button className="btn-quiet" type="button" onClick={() => navigate("/setup?step=jd")}>
                <Plus size={14} /> Add
              </button>
            </div>
            {jobs.length === 0 ? <p className="none">None yet</p> : null}
            {jobs.map((j) => {
              const seniority = (j.parsed_json as { seniority?: unknown } | null | undefined)?.seniority;
              return entryButton(
                j.id,
                jobLabel(j),
                <>
                  {isString(seniority) && seniority !== "unknown" ? <span>{titleCase(seniority)}</span> : null}
                  {j.id === activeJobId ? <StatusPill tone="good">Active</StatusPill> : null}
                </>,
              );
            })}
          </section>

          <section>
            <div className="head">
              <h2>Supporting docs</h2>
              <button className="btn-quiet" type="button" onClick={() => navigate("/setup?step=docs")}>
                <Plus size={14} /> Add
              </button>
            </div>
            {techDocs.length === 0 ? <p className="none">None yet</p> : null}
            {techDocs.map((d) => {
              const company = filedUnder(profile, d.id);
              return entryButton(
                d.id,
                firstLine(d.preview) || d.project_title || d.filename,
                d.project_title ? (
                  company ? (
                    `Filed under ${company}`
                  ) : null
                ) : (
                  <StatusPill tone="neutral">Unmapped</StatusPill>
                ),
              );
            })}
          </section>

          <RepoAdder token={token} onChanged={load}>
            {githubRepos.length === 0 ? <p className="none">None yet</p> : null}
            {githubRepos.map((d) => {
              const tech = strings((d.parsed_json as { tech?: unknown } | null | undefined)?.tech);
              return entryButton(d.id, d.project_title ?? d.filename, tech.slice(0, 3).join(" · ") || null);
            })}
          </RepoAdder>
        </nav>

        {selected ? (
          <div className="box pane" key={selected.id}>
            <span className="lbl">{KIND_LABEL[selected.kind]}</span>

            {selected.kind === "cv" ? (
              <>
                <PaneHead
                  title={selected.doc.filename}
                  meta={
                    <>
                      <span>
                        Uploaded {shortDate(selected.doc.created_at)} · {chars(selected.doc.char_count)}
                      </span>
                      <EmbedPill status={selected.doc.embedding_status} />
                    </>
                  }
                >
                  <div className="acts">
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
                    {selected.doc.embedding_status === "failed" ? (
                      <button
                        className="btn-ghost"
                        type="button"
                        onClick={() => retryEmbed(selected.id)}
                        disabled={busy === selected.id}
                      >
                        <RefreshCw size={14} /> Retry embedding
                      </button>
                    ) : null}
                  </div>
                  <span className="consequence">
                    Rebuilds the profile{mappedTechDocs > 0 ? ` and remaps ${plural(mappedTechDocs, "doc")}` : ""}
                    . The same file is a no-op.
                  </span>
                </PaneHead>
                {showText ? (
                  <FullText text={text} />
                ) : profile ? (
                  <div className="digest">
                    {summaryOf(profile) ? <Row k="Summary">{leadOf(summaryOf(profile)!)}</Row> : null}
                    {rolesOf(profile).length > 0 ? <Row k="Roles">{rolesOf(profile).join(" · ")}</Row> : null}
                    {skillsOf(profile).length > 0 ? (
                      <Row k="Skills">
                        <Chips items={skillsOf(profile)} />
                      </Row>
                    ) : null}
                    {educationOf(profile).length > 0 ? (
                      <Row k="Education">{educationOf(profile).join(" · ")}</Row>
                    ) : null}
                  </div>
                ) : (
                  <div className="digest">
                    <Row k="Opens">
                      <span className="q">{openingOf(text ?? selected.doc.preview)}</span>
                    </Row>
                    <Row k="Profile">Not built yet - prep builds it on Setup.</Row>
                  </div>
                )}
              </>
            ) : null}

            {selected.kind === "job" ? (
              <JobPane
                job={selected.job}
                text={text}
                showText={showText}
                active={selected.id === activeJobId}
                busy={busy === selected.id}
                editing={editingJobId === selected.id}
                blocking={blocking?.scope.id === selected.id ? blocking.sessionIds : null}
                onMakeActive={() => {
                  // setActiveJobId only swaps the id; refresh so the header
                  // chip flips to this job immediately.
                  setActiveJobId(selected.id);
                  void refreshActiveJob();
                }}
                onToggleEdit={() => setEditingJobId(editingJobId === selected.id ? null : selected.id)}
                onSubmitEdit={(body) => submitReanalyze(selected.id, body)}
                onDelete={() => deleteJob(selected.id)}
                onAbandon={abandonSession}
                onDismissBlocking={() => setBlocking(null)}
              />
            ) : null}

            {selected.kind === "doc" ? (
              <>
                <PaneHead
                  title={selected.doc.filename}
                  meta={
                    <>
                      <span>
                        Uploaded {shortDate(selected.doc.created_at)} · {chars(selected.doc.char_count)}
                      </span>
                      <EmbedPill status={selected.doc.embedding_status} />
                    </>
                  }
                >
                  <div className="acts">
                    <button
                      className="btn-ghost"
                      type="button"
                      onClick={() => void openRemap(selected.id)}
                      disabled={busy === selected.id || remapping != null}
                    >
                      <Sparkles size={14} /> Remap
                    </button>
                    {selected.doc.embedding_status === "failed" && selected.doc.project_title ? (
                      <button
                        className="btn-ghost"
                        type="button"
                        onClick={() => retryEmbed(selected.id)}
                        disabled={busy === selected.id}
                      >
                        <RefreshCw size={14} /> Retry embedding
                      </button>
                    ) : null}
                    <ArmedDeleteButton
                      label="Delete"
                      icon={<Trash2 size={14} />}
                      onConfirm={() => deleteDoc(selected.id)}
                    />
                  </div>
                </PaneHead>
                {showText ? (
                  <FullText text={text} />
                ) : (
                  <div className="digest">
                    <Row k="About">
                      <span className="q">{openingOf(text ?? selected.doc.preview)}</span>
                    </Row>
                    {text && sectionsOf(text).length > 0 ? (
                      <Row k="Sections">{sectionsOf(text).join(" · ")}</Row>
                    ) : null}
                    <Row k="Filed under">
                      {selected.doc.project_title
                        ? [selected.doc.project_title, filedUnder(profile, selected.id)]
                            .filter(Boolean)
                            .join(" at ")
                        : "Nothing yet - Remap files it under one of your CV's experiences."}
                    </Row>
                  </div>
                )}
              </>
            ) : null}

            {selected.kind === "repo" ? (
              <RepoPane
                doc={selected.doc}
                text={text}
                showText={showText}
                onDelete={() => deleteDoc(selected.id)}
              />
            ) : null}

            {text !== "" ? (
              <button className="btn-quiet" type="button" onClick={() => setShowText((s) => !s)}>
                {showText ? "Back to the digest" : "Read the full text"}
              </button>
            ) : null}
          </div>
        ) : (
          <div className="pane-empty">
            {isLoading ? (
              <p className="muted">Loading…</p>
            ) : (
              <EmptyState title="Nothing on file yet." body="Setup starts with your CV." />
            )}
          </div>
        )}
      </div>

      <DangerZone onReset={resetAccount} busy={busy === "reset"} />

      {/* Remap HITL is the same slip Setup uses. Backdrop / ESC close it
          without mutating the mapping; Skip goes through confirmRemap so the
          no-op call clears the in-flight state. */}
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

// ─────────────────────────── pane pieces ────────────────────────────────

function PaneHead({ title, meta, children }: { title: ReactNode; meta?: ReactNode; children?: ReactNode }) {
  return (
    <div className="pane-head">
      <div className="about">
        <strong>{title}</strong>
        {meta ? <span className="meta">{meta}</span> : null}
      </div>
      {children ? <div className="pane-actions">{children}</div> : null}
    </div>
  );
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="row">
      <span className="k">{k}</span>
      <div>{children}</div>
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="repo-chips">
      {items.map((t) => (
        <span key={t} className="repo-chip">
          {t}
        </span>
      ))}
    </div>
  );
}

function FullText({ text }: { text: string | undefined }) {
  return text === undefined ? <p className="muted">Loading…</p> : <pre className="pane-text">{text}</pre>;
}

function EmbedPill({ status }: { status: EmbeddingStatus | undefined }) {
  // A healthy file says nothing; only a state that needs the user shows.
  if (status === "pending") return <StatusPill tone="warn">Embedding…</StatusPill>;
  if (status === "failed") return <StatusPill tone="bad">Embedding failed</StatusPill>;
  return null;
}

function JobPane({
  job,
  text,
  showText,
  active,
  busy,
  editing,
  blocking,
  onMakeActive,
  onToggleEdit,
  onSubmitEdit,
  onDelete,
  onAbandon,
  onDismissBlocking,
}: {
  job: JobItem;
  text: string | undefined;
  showText: boolean;
  active: boolean;
  busy: boolean;
  editing: boolean;
  blocking: string[] | null;
  onMakeActive: () => void;
  onToggleEdit: () => void;
  onSubmitEdit: (body: { text?: string; url?: string }) => void;
  onDelete: () => void;
  onAbandon: (id: string) => void;
  onDismissBlocking: () => void;
}) {
  const brief = (job.parsed_json ?? {}) as {
    must_have_skills?: unknown;
    nice_to_have_skills?: unknown;
    behavioral_signals?: unknown;
  };
  const mustHave = strings(brief.must_have_skills);
  const niceToHave = strings(brief.nice_to_have_skills);
  const looksFor = strings(brief.behavioral_signals);
  const analysed = job.parsed_json != null;
  return (
    <>
      <PaneHead
        title={job.source_url ? job.source_url.replace(/^https?:\/\//, "") : "Pasted text"}
        meta={
          <>
            <span>
              {shortDate(job.created_at)} · {chars(job.char_count)}
            </span>
            {active ? <StatusPill tone="good">Active</StatusPill> : null}
          </>
        }
      >
        <div className="acts">
          {!active ? (
            <button className="btn-ghost" type="button" onClick={onMakeActive}>
              Make active
            </button>
          ) : null}
          <button className="btn-ghost" type="button" onClick={onToggleEdit} disabled={busy}>
            <Sparkles size={14} /> {editing ? "Cancel" : "Re-analyze"}
          </button>
          <ArmedDeleteButton label="Delete" icon={<Trash2 size={14} />} onConfirm={onDelete} />
        </div>
      </PaneHead>
      {editing ? <JobEditor job={job} disabled={busy} onSubmit={onSubmitEdit} /> : null}
      {blocking ? (
        <BlockingSessionsCard sessionIds={blocking} onAbandon={onAbandon} onDismiss={onDismissBlocking} />
      ) : null}
      {showText ? (
        <FullText text={text} />
      ) : (
        <div className="digest">
          <Row k="Opens">
            <span className="q">{openingOf(text ?? job.preview)}</span>
          </Row>
          {analysed ? (
            <>
              {mustHave.length > 0 ? <Row k="Must have">{mustHave.join(" · ")}</Row> : null}
              {niceToHave.length > 0 ? <Row k="Nice to have">{niceToHave.join(" · ")}</Row> : null}
              {looksFor.length > 0 ? <Row k="Looks for">{looksFor.join(" · ")}</Row> : null}
            </>
          ) : (
            <Row k="Read as">Not analysed yet - prep reads it on Setup.</Row>
          )}
        </div>
      )}
    </>
  );
}

/**
 * Phase 32: a read-only pane for an ingested ``github_repo`` doc. Tech and
 * key features come straight off the folded ProjectItem on ``parsed_json``.
 */
function RepoPane({
  doc,
  text,
  showText,
  onDelete,
}: {
  doc: DocumentItem;
  text: string | undefined;
  showText: boolean;
  onDelete: () => void;
}) {
  const pj = (doc.parsed_json ?? {}) as { tech?: unknown; key_features?: unknown; urls?: unknown };
  const tech = strings(pj.tech);
  const features = strings(pj.key_features).slice(0, 4);
  const repoUrl = strings(pj.urls)[0];
  return (
    <>
      <PaneHead
        title={
          repoUrl ? (
            <a className="repo-link" href={repoUrl} target="_blank" rel="noreferrer">
              <ExternalLink size={12} /> {repoUrl.replace(/^https?:\/\/(www\.)?github\.com\//, "")}
            </a>
          ) : (
            doc.filename
          )
        }
        meta={
          <>
            <span>Added {shortDate(doc.created_at)}</span>
            <EmbedPill status={doc.embedding_status} />
          </>
        }
      >
        <div className="acts">
          <ArmedDeleteButton label="Delete" icon={<Trash2 size={14} />} onConfirm={onDelete} />
        </div>
      </PaneHead>
      {showText ? (
        <FullText text={text} />
      ) : (
        <div className="digest">
          {tech.length > 0 ? (
            <Row k="Tech">
              <Chips items={tech.slice(0, 8)} />
            </Row>
          ) : null}
          {features.length > 0 ? <Row k="Does">{features.join(" · ")}</Row> : null}
          {tech.length === 0 && features.length === 0 ? (
            <Row k="About">
              <span className="q">{openingOf(text ?? doc.preview)}</span>
            </Row>
          ) : null}
        </div>
      )}
    </>
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
        <textarea name="jd_text" rows={8} placeholder="Paste the corrected job description…" autoFocus defaultValue="" />
      ) : (
        <div className="input-with-icon">
          <LinkIcon size={16} />
          <input name="jd_url" type="url" placeholder="https://…" autoFocus defaultValue={job.source_url ?? ""} />
        </div>
      )}
      <button className="btn-secondary" type="submit" disabled={disabled}>
        <Sparkles size={14} /> Re-analyze
      </button>
      <p className="muted">Replaces the text and re-runs prep on Setup.</p>
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
      <p>Can&apos;t delete this job description while these rounds are still open:</p>
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
 * embedding, and prep checkpoint owned by the user. The ``users`` row + auth
 * token stay intact, so the user remains logged in with an empty account
 * ready to re-onboard. Two-stage confirmation: "Reset account…" exposes the
 * input, then typing the registered email enables submit.
 */
function DangerZone({ onReset, busy }: { onReset: (confirmEmail: string) => Promise<void>; busy: boolean }) {
  const { user } = useAuth();
  const [armed, setArmed] = useState(false);
  const [typed, setTyped] = useState("");

  if (!user) return null;
  const canSubmit = !busy && typed.trim().toLowerCase() === user.email.toLowerCase();

  return (
    <section className="danger-zone">
      <h2>Danger zone</h2>
      <div className="danger-row">
        <div className="about">
          <strong>Reset account</strong>
          <span className="meta">
            Deletes every file, job description, round and the profile. Your login stays.
          </span>
        </div>
        <div className="pane-actions">
          <div className="acts">
            {!armed ? (
              <button className="btn-ghost danger-trigger" type="button" onClick={() => setArmed(true)} disabled={busy}>
                <Trash2 size={14} /> Reset account…
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

// ─────────────────────────── GitHub repos: the index section ─────────────

/**
 * Phase 32 follow-up: the repos section of the index - its head with Add,
 * the inline handle step when no handle is stored yet, and the picker that
 * calls ``/github/repos`` + ``/github/repos/select`` (the same
 * ``fold_github_projects`` re-fold the prep graph uses). The entries
 * themselves come in as children so the index owns their look.
 */
function RepoAdder({
  token,
  onChanged,
  children,
}: {
  token: string | null;
  onChanged: () => Promise<void> | void;
  children: ReactNode;
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
      // 400 no_handle → no verified handle yet; reveal the inline step.
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
    <section>
      <div className="head">
        <h2>GitHub repos</h2>
        <button className="btn-quiet" type="button" onClick={() => void openPicker()} disabled={loadingList}>
          <Plus size={14} /> Add
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

      {children}

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
