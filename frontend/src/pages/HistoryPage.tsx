import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { JobItem, Session, SessionDetail, SessionStatus, api } from "../api";
import { ErrorBanner } from "../components/ui";
import { codeFrom } from "../errors";
import { useAuth } from "../state/auth";

const roundLabels = {
  resume_walkthrough: "Resume / Project deep-dive",
  behavioral_star: "Behavioral / STAR",
};

const statusTone: Record<SessionStatus, string> = {
  active: "info",
  complete: "good",
  abandoned: "neutral",
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
      <header className="history-header">
        <h1 className="history-title">History</h1>
        <div className="history-filter">
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
          <p>No sessions yet.</p>
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

  const scored = detail?.turns.filter((t) => t.score !== null && t.score !== undefined) ?? [];
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
            {date} · {session.n_questions} q
            {average !== null ? <> · {average.toFixed(1)}/10</> : null}
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
          {detail?.turns.length === 0 ? <p>No turns recorded.</p> : null}
          {detail?.turns.map((turn) => (
            <div className="history-turn" key={turn.id}>
              <strong className="history-turn-q">
                Q{turn.turn_index + 1}. {turn.question}
              </strong>
              {turn.answer ? (
                <p>
                  <span className="history-turn-label">You</span> {turn.answer}
                </p>
              ) : (
                <p className="history-turn-empty">No answer recorded.</p>
              )}
              {turn.score !== null && turn.score !== undefined ? (
                <>
                  <p>
                    <span className="history-turn-label">{turn.score}/10</span>{" "}
                    {turn.feedback}
                  </p>
                  {turn.model_answer ? (
                    <details>
                      <summary>Model answer</summary>
                      <p>{turn.model_answer}</p>
                    </details>
                  ) : null}
                </>
              ) : (
                <p className="history-turn-empty">No evaluation yet.</p>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}
