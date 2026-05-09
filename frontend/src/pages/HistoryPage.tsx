import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";

import { ApiError, Session, SessionDetail, SessionStatus, api } from "../api";
import { EmptyState, StatusPill, formatDate } from "../components/ui";
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
  const [filter, setFilter] = useState<"all" | SessionStatus>("all");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      return;
    }
    api
      .listSessions(token)
      .then(setSessions)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.detail : "Could not load history.");
      });
  }, [token]);

  const filtered = useMemo(
    () => (filter === "all" ? sessions : sessions.filter((session) => session.status === filter)),
    [sessions, filter],
  );

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
      {error ? <div className="error-banner">{error}</div> : null}
      {filtered.length === 0 ? (
        <EmptyState title="No sessions here" body="Completed interviews and drafts will appear here." />
      ) : (
        <div className="history-list">
          {filtered.map((session) => (
            <HistorySession key={session.id} session={session} token={token!} />
          ))}
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
        setError(err instanceof ApiError ? err.detail : "Could not load session.");
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
          {error ? <div className="error-banner">{error}</div> : null}
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
