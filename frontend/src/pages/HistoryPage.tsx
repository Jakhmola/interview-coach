import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { JobItem, Session, SessionDetail, SessionStatus, Thread, api } from "../api";
import { ErrorBanner, RatingCells, SheetHead } from "../components/ui";
import { codeFrom } from "../errors";
import { topicLabel } from "../jobLabel";
import { useAuth } from "../state/auth";

const roundLabels = {
  experience_deep_dive: "Experience deep-dive",
  technical_challenge: "Technical challenge",
  behavioral_star: "Behavioral / STAR",
};

// The stamp's colour is the state: complete is ok green, abandoned is the red
// pen, active is plain ink.
const statusTone: Record<SessionStatus, string> = {
  active: "info",
  complete: "good",
  abandoned: "bad",
};

export function HistoryPage() {
  const { token } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [jobDetails, setJobDetails] = useState<
    Record<string, { title?: string; company?: string }>
  >({});
  const [filter, setFilter] = useState<"all" | SessionStatus>("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    Promise.all([api.listSessions(token), api.listJobs(token)])
      .then(([ss, jj]) => {
        setSessions(ss);
        setJobs(jj);
      })
      .catch((err: unknown) => setError(codeFrom(err)));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    const seenJobIds = new Set(sessions.map((s) => s.job_id));
    const missing = [...seenJobIds].filter((id) => !(id in jobDetails));
    if (missing.length === 0) return;
    let cancelled = false;
    void Promise.all(
      missing.map((id) =>
        api
          .getJob(token, id)
          .then((j) => {
            const parsed = (j.parsed_json ?? null) as {
              title?: string;
              company_name?: string;
            } | null;
            return [id, { title: parsed?.title, company: parsed?.company_name }] as const;
          })
          .catch(() => [id, {}] as const),
      ),
    ).then((entries) => {
      if (cancelled) return;
      setJobDetails((prev) => {
        const next = { ...prev };
        for (const [id, v] of entries) next[id] = v;
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [token, sessions, jobDetails]);

  const filtered = useMemo(
    () =>
      filter === "all"
        ? sessions
        : sessions.filter((session) => session.status === filter),
    [sessions, filter],
  );

  const groups = useMemo(() => {
    const byJob = new Map<string, Session[]>();
    for (const s of filtered) {
      const arr = byJob.get(s.job_id) ?? [];
      arr.push(s);
      byJob.set(s.job_id, arr);
    }
    return [...byJob.entries()]
      .map(([jobId, list]) => ({
        jobId,
        sessions: list.sort((a, b) => b.created_at.localeCompare(a.created_at)),
        latest: list.reduce((m, s) => (s.created_at > m ? s.created_at : m), ""),
      }))
      .sort((a, b) => b.latest.localeCompare(a.latest));
  }, [filtered]);

  return (
    <div className="history">
      <SheetHead title="Session records" page={`${sessions.length} session${sessions.length === 1 ? "" : "s"} on file`} />
      <header className="history-header">
        <div className="history-filter" role="group" aria-label="Filter by status">
          {(["all", "complete", "active", "abandoned"] as const).map((f) => (
            <button
              key={f}
              type="button"
              className={`history-filter-pill${filter === f ? " active" : ""}`}
              onClick={() => setFilter(f)}
            >
              {f}
            </button>
          ))}
        </div>
      </header>

      <ErrorBanner code={error} />

      {filtered.length === 0 ? (
        <div className="history-empty">
          <p>
            {sessions.length === 0
              ? "Nothing filed yet. Your first practice round will appear here with every topic, rating, and model answer."
              : "No sessions match this filter."}
          </p>
        </div>
      ) : (
        <div className="history-groups">
          {groups.map((g) => {
            const jobMeta = jobDetails[g.jobId];
            const jobInList = jobs.find((j) => j.id === g.jobId);
            const role = jobMeta?.title;
            const company = jobMeta?.company || jobInList?.source_url;
            const isDeleted = !jobInList && !jobMeta;
            return (
              <section className="history-group" key={g.jobId}>
                <header className="history-group-header">
                  {isDeleted ? (
                    <span className="history-group-deleted">JD deleted</span>
                  ) : (
                    <>
                      <span className="history-group-role">{role || "Role"}</span>
                      <span className="history-group-sep">·</span>
                      <span className="history-group-company">{company || "Company"}</span>
                    </>
                  )}
                  <span className="history-group-count">
                    {g.sessions.length} session{g.sessions.length === 1 ? "" : "s"}
                  </span>
                </header>
                <div className="history-list">
                  {g.sessions.map((session) => (
                    <HistorySession key={session.id} session={session} token={token!} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function HistorySession({ session, token }: { session: Session; token: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || detail) return;
    api
      .getSession(token, session.id)
      .then(setDetail)
      .catch((err: unknown) => setError(codeFrom(err)));
  }, [isOpen, detail, session.id, token]);

  const scored = detail?.threads.filter((t) => t.score !== null && t.score !== undefined) ?? [];
  const average = scored.length
    ? scored.reduce((total, t) => total + (t.score ?? 0), 0) / scored.length
    : null;

  const date = new Date(session.created_at).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });

  return (
    <article className={`history-card${isOpen ? " open" : ""}`}>
      <button
        type="button"
        className="history-card-trigger"
        onClick={() => setIsOpen((x) => !x)}
      >
        <div className="history-card-main">
          <strong>{roundLabels[session.round_type]}</strong>
          <span className="history-card-meta">
            {date} · {session.n_questions} topic{session.n_questions === 1 ? "" : "s"}
            {average !== null ? <> · {average.toFixed(1)}/10 average</> : null}
          </span>
        </div>
        <span className={`history-card-status status-${statusTone[session.status]}`}>
          {session.status}
        </span>
        <ChevronDown
          size={16}
          className={`history-card-chevron${isOpen ? " open" : ""}`}
        />
      </button>
      {isOpen ? (
        <div className="history-card-body">
          <ErrorBanner code={error} />
          {!detail ? <p className="history-card-loading">Loading…</p> : null}
          {detail?.threads.length === 0 ? <p className="record-empty">No topics recorded.</p> : null}
          {detail?.threads.map((thread) => (
            <HistoryThread key={thread.id} thread={thread} />
          ))}
        </div>
      ) : null}
    </article>
  );
}

const moveLabels: Record<string, string> = {
  probe: "Probe",
  clarify: "Clarify",
  nudge: "Nudge",
};

type Message = Thread["messages"][number];

/**
 * One topic as a record: the full topic line, then the exchange on the left
 * and the interviewer's verdict on the right. Collapsed, it shows the
 * question, the candidate's first answer and the verdict cut to a few lines;
 * "Full exchange" opens every turn and the model answer.
 */
function HistoryThread({ thread }: { thread: Thread }) {
  const [open, setOpen] = useState(false);
  const num = thread.thread_index + 1;
  const messages = thread.messages;
  const question = messages.find((m) => m.role === "interviewer" && (m.kind ?? "question") === "question");
  const firstAnswer = messages.find((m) => m.role === "candidate");
  const summary = [question, firstAnswer].filter((m): m is Message => !!m);
  const shown = open ? messages : summary;
  const scored = thread.status === "closed" && thread.score !== null && thread.score !== undefined;
  // Whether there is anything beyond the summary to open (and to close again).
  const hasMore = messages.length > summary.length || !!thread.model_answer;

  return (
    <article className="record">
      <header className="record-head">
        <strong className="record-topic">Topic {num}</strong>
        <span className="record-focus">{topicLabel(thread.focus_label) ?? "Untitled topic"}</span>
        <RatingCells score={thread.score} mini />
      </header>
      <div className="record-body">
        <div className="record-exchange">
          {shown.map((m) => {
            const isQ = m.role === "interviewer" && (m.kind ?? "question") === "question";
            const isFollowUp = m.role === "interviewer" && !isQ;
            const clamp = !open && m.role === "candidate";
            const voice = m.role === "candidate" ? "typed" : isFollowUp ? "pen" : "";
            return (
              <div key={m.id} className="exchange">
                <span className={`who${isFollowUp ? " pen" : ""}`}>
                  {m.role === "candidate" ? "You" : isQ ? `Q${num}` : moveLabels[m.kind ?? ""] ?? "Interviewer"}
                </span>
                <p className={`${voice}${clamp ? " clamp" : ""}`.trim()}>{m.text}</p>
              </div>
            );
          })}
        </div>
        <div className="record-verdict">
          {scored ? (
            <>
              <span className="who">Rated {thread.score}/10</span>
              <p
                className={open ? undefined : "clamp"}
                style={open ? undefined : ({ "--lines": 4 } as CSSProperties)}
              >
                {thread.feedback}
              </p>
              {open && thread.model_answer ? (
                <details className="model-answer" open>
                  <summary>
                    <ChevronRight aria-hidden="true" />
                    Model answer
                  </summary>
                  <p>{thread.model_answer}</p>
                </details>
              ) : null}
            </>
          ) : (
            <p className="record-empty">No evaluation yet.</p>
          )}
        </div>
      </div>
      {hasMore ? (
        <button
          type="button"
          className="btn-quiet record-toggle"
          onClick={() => setOpen((x) => !x)}
          aria-expanded={open}
        >
          {open
            ? "Show less"
            : `Full exchange · ${messages.length} turn${messages.length === 1 ? "" : "s"}${thread.model_answer ? " · model answer" : ""}`}
        </button>
      ) : null}
    </article>
  );
}
