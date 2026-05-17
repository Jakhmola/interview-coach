import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { JobItem, Session, SessionDetail, SessionStatus, api } from "../api";
import { EmptyState, ErrorBanner, StatusPill, formatDate } from "../components/ui";
import { codeFrom } from "../errors";
import { useAuth } from "../state/auth";

const roundLabels = {
  resume_walkthrough: "Resume / Project deep-dive",
  behavioral_star: "Behavioral / STAR",
};

const statusTone: Record<SessionStatus, "neutral" | "good" | "warn" | "bad" | "info"> = {
  active: "info",
  complete: "good",
  abandoned: "neutral",
};

export function HistoryPage() {
  const { token } = useAuth();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [jobs, setJobs] = useState<JobItem[]>([]);
  // Per-job detail cache keyed by job_id so we can render group headers
  // ("Senior PM @ Acme") without burning a getJob call per session.
  const [jobDetails, setJobDetails] = useState<Record<string, { title?: string; company?: string }>>({});
  const [filter, setFilter] = useState<"all" | SessionStatus>("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;
    Promise.all([api.listSessions(token), api.listJobs(token)])
      .then(([ss, jj]) => {
        setSessions(ss);
        setJobs(jj);
      })
      .catch((err: unknown) => {
        setError(codeFrom(err));
      });
  }, [token]);

  // Fetch per-job detail once we know which jobs appear in the history.
  // Skipped silently for jobs that have been deleted (404).
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
        for (const [id, v] of entries) {
          next[id] = v;
        }
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [token, sessions, jobDetails]);

  const filtered = useMemo(
    () => (filter === "all" ? sessions : sessions.filter((session) => session.status === filter)),
    [sessions, filter],
  );

  // Group filtered sessions by job_id. Group order: by most-recent session
  // within each group, descending.
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
    <section className="panel wide">
      <div className="panel-header">
        <div>
          <span className="eyebrow">Review</span>
          <h2>Interview history</h2>
        </div>
        <select value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}>
          <option value="all">All sessions</option>
          <option value="active">Active</option>
          <option value="complete">Complete</option>
          <option value="abandoned">Abandoned</option>
        </select>
      </div>
      <ErrorBanner code={error} />
      {filtered.length === 0 ? (
        <EmptyState title="No sessions here" body="Completed interviews and drafts will appear here." />
      ) : (
        <div className="history-groups">
          {groups.map((g) => {
            const jobMeta = jobDetails[g.jobId];
            const jobInList = jobs.find((j) => j.id === g.jobId);
            const role = jobMeta?.title || "(role TBD)";
            const company = jobMeta?.company || jobInList?.source_url || "Pasted JD";
            const isDeleted = !jobInList && !jobMeta;
            const label = isDeleted ? "(JD deleted)" : `${role} @ ${company}`;
            return (
              <div className="history-group" key={g.jobId}>
                <h3 className="history-group-header">{label}</h3>
                <div className="history-list">
                  {g.sessions.map((session) => (
                    <HistorySession key={session.id} session={session} token={token!} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function HistorySession({ session, token }: { session: Session; token: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isOpen || detail) {
      return;
    }
    api
      .getSession(token, session.id)
      .then(setDetail)
      .catch((err: unknown) => {
        setError(codeFrom(err));
      });
  }, [isOpen, detail, session.id, token]);

  const scored = detail?.turns.filter((turn) => turn.score !== null && turn.score !== undefined) ?? [];
  const average = scored.length
    ? scored.reduce((total, turn) => total + (turn.score ?? 0), 0) / scored.length
    : null;

  return (
    <article className="history-card">
      <button className="history-trigger" onClick={() => setIsOpen((value) => !value)}>
        <div>
          <strong>{roundLabels[session.round_type]}</strong>
          <span>
            {formatDate(session.created_at)} · {session.n_questions} question
            {session.n_questions === 1 ? "" : "s"}
          </span>
        </div>
        <StatusPill tone={statusTone[session.status]}>{session.status}</StatusPill>
        <ChevronDown size={18} />
      </button>
      {isOpen ? (
        <div className="history-detail">
          <ErrorBanner code={error} />
          {!detail ? <p>Loading session...</p> : null}
          {average !== null ? <p className="score-summary">Average score: {average.toFixed(1)}/10</p> : null}
          {detail?.turns.length === 0 ? <p>No turns recorded.</p> : null}
          {detail?.turns.map((turn) => (
            <div className="turn-review" key={turn.id}>
              <strong>Q{turn.turn_index + 1}. {turn.question}</strong>
              {turn.answer ? <p><b>Your answer.</b> {turn.answer}</p> : <p>No answer recorded.</p>}
              {turn.score !== null && turn.score !== undefined ? (
                <>
                  <p><b>Score.</b> {turn.score}/10</p>
                  {turn.feedback ? <p><b>Feedback.</b> {turn.feedback}</p> : null}
                  {turn.model_answer ? (
                    <details>
                      <summary>Model answer</summary>
                      <p>{turn.model_answer}</p>
                    </details>
                  ) : null}
                </>
              ) : (
                <p>No evaluation yet.</p>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}
