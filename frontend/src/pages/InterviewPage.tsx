import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Play, RotateCcw, Square } from "lucide-react";

import {
  ApiError,
  JobItem,
  RoundType,
  Session,
  SessionDetail,
  SseFrame,
  Turn,
  answerStream,
  api,
  nextQuestionStream,
} from "../api";
import { EmptyState, StatusPill, formatDate, shortId } from "../components/ui";
import { useAuth } from "../state/auth";

const roundLabels: Record<RoundType, string> = {
  resume_walkthrough: "Resume / Project deep-dive",
  behavioral_star: "Behavioral / STAR",
};

export function InterviewPage() {
  const { token } = useAuth();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [jobId, setJobId] = useState("");
  const [roundType, setRoundType] = useState<RoundType>("resume_walkthrough");
  const [nQuestions, setNQuestions] = useState(5);
  const [answer, setAnswer] = useState("");
  const [streamQuestion, setStreamQuestion] = useState("");
  const [streamFeedback, setStreamFeedback] = useState("");
  const [streamModelAnswer, setStreamModelAnswer] = useState("");
  const [streamScore, setStreamScore] = useState<number | null>(null);
  const [streamPhase, setStreamPhase] = useState<"idle" | "evaluating" | "feedback" | "model_answer">("idle");
  const [pendingAnswer, setPendingAnswer] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  const activeSessions = useMemo(
    () => sessions.filter((session) => session.status === "active"),
    [sessions],
  );

  const refresh = async () => {
    if (!token) return;
    const [nextJobs, nextSessions] = await Promise.all([api.listJobs(token), api.listSessions(token)]);
    setJobs(nextJobs);
    setSessions(nextSessions);
    setJobId((current) => current || nextJobs[0]?.id || "");
    if (activeId) {
      setDetail(await api.getSession(token, activeId));
    }
  };

  useEffect(() => {
    refresh().catch((err: unknown) => {
      setError(err instanceof ApiError ? err.detail : "Could not load interview data.");
    });
  }, [token, activeId]);

  // Scroll to bottom whenever chat content changes
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.turns, streamQuestion, streamFeedback, streamModelAnswer, pendingAnswer]);

  const loadSession = async (id: string) => {
    if (!token) return;
    setActiveId(id);
    setDetail(await api.getSession(token, id));
    setError(null);
  };

  const askNext = async (sessionId: string, sessionToken: string) => {
    setIsBusy(true);
    setStreamQuestion("");
    setError(null);
    try {
      await nextQuestionStream(sessionToken, sessionId, (frame: SseFrame) => {
        if (frame.event === "token" && typeof frame.data === "string") {
          setStreamQuestion((current) => current + frame.data);
        }
        if (frame.event === "error") {
          setError(extractStreamError(frame.data));
        }
      });
      setStreamQuestion("");
      setDetail(await api.getSession(sessionToken, sessionId));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Question generation failed.");
    } finally {
      setIsBusy(false);
    }
  };

  const startSession = async (event: FormEvent) => {
    event.preventDefault();
    if (!token || !jobId) return;
    setError(null);
    try {
      const session = await api.createSession(token, jobId, roundType, nQuestions);
      setActiveId(session.id);
      setDetail(await api.getSession(token, session.id));
      await refresh();
      // Auto-ask the first question immediately
      await askNext(session.id, token);
    } catch (err) {
      setError(err instanceof ApiError ? prereqHint(err.detail) : "Could not start the interview.");
    }
  };

  const abandon = async (id: string) => {
    if (!token) return;
    await api.abandonSession(token, id);
    setActiveId(null);
    setDetail(null);
    await refresh();
  };

  const latest = detail?.turns.at(-1);
  const needsQuestion =
    detail?.status === "active" &&
    detail.turns.length < detail.n_questions &&
    (!latest || (latest.answer && latest.score !== null && latest.score !== undefined));
  const needsAnswer = detail?.status === "active" && latest && !latest.answer;
  const needsRetry =
    detail?.status === "active" && latest && latest.answer && latest.score === null;

  const submitAnswer = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!token || !detail || !latest) return;
    const text = needsRetry ? latest.answer || "" : answer.trim();
    if (!text) {
      setError("Type an answer before submitting.");
      return;
    }
    // Show the user's answer bubble immediately
    setPendingAnswer(text);
    setAnswer("");
    setIsBusy(true);
    setStreamFeedback("");
    setStreamModelAnswer("");
    setStreamScore(null);
    setStreamPhase("evaluating");
    setError(null);
    try {
      await answerStream(token, detail.id, text, (frame: SseFrame) => {
        if (frame.event === "score" && typeof frame.data === "object" && frame.data !== null) {
          setStreamScore(Number((frame.data as { score?: number }).score));
          setStreamPhase("feedback");
        }
        if (frame.event === "feedback_token" && typeof frame.data === "string") {
          setStreamPhase("feedback");
          setStreamFeedback((current) => current + frame.data);
        }
        if (frame.event === "model_answer_token" && typeof frame.data === "string") {
          setStreamPhase("model_answer");
          setStreamModelAnswer((current) => current + frame.data);
        }
        if (frame.event === "model_answer_error") {
          setStreamModelAnswer("Model answer unavailable for this turn.");
        }
        if (frame.event === "error") {
          setError(extractStreamError(frame.data));
        }
      });
      setDetail(await api.getSession(token, detail.id));
      setPendingAnswer(null);
      setStreamFeedback("");
      setStreamModelAnswer("");
      setStreamScore(null);
      setStreamPhase("idle");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Evaluation failed.");
      setPendingAnswer(null);
      setStreamPhase("idle");
    } finally {
      setIsBusy(false);
    }
  };

  const handleAskNext = () => {
    if (!token || !detail) return;
    askNext(detail.id, token);
  };

  if (!activeId || !detail) {
    return (
      <div className="page-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <span className="eyebrow">Start</span>
              <h2>New interview round</h2>
            </div>
          </div>
          {error ? <div className="error-banner">{error}</div> : null}
          {jobs.length === 0 ? (
            <EmptyState title="No job yet" body="Save and prepare a job on the Setup page first." />
          ) : (
            <form className="form-stack" onSubmit={startSession}>
              <label>
                Job description
                <select value={jobId} onChange={(event) => setJobId(event.target.value)}>
                  {jobs.map((job) => (
                    <option value={job.id} key={job.id}>
                      {(job.source_url || "Pasted JD").slice(0, 90)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Round type
                <select value={roundType} onChange={(event) => setRoundType(event.target.value as RoundType)}>
                  {Object.entries(roundLabels).map(([value, label]) => (
                    <option value={value} key={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Questions: {nQuestions}
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={nQuestions}
                  onChange={(event) => setNQuestions(Number(event.target.value))}
                />
              </label>
              <button className="primary-button" type="submit" disabled={isBusy}>
                {isBusy ? <Loader2 size={18} className="spin" /> : <Play size={18} />}
                {isBusy ? "Preparing your first question..." : "Start interview"}
              </button>
            </form>
          )}
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <span className="eyebrow">Resume</span>
              <h2>Active sessions</h2>
            </div>
          </div>
          {activeSessions.length === 0 ? (
            <EmptyState title="No active sessions" body="Start a new round when you're ready." />
          ) : (
            <div className="list">
              {activeSessions.map((session) => (
                <article className="list-item" key={session.id}>
                  <div>
                    <strong>{roundLabels[session.round_type]}</strong>
                    <span>
                      {shortId(session.id)} · {formatDate(session.created_at)}
                    </span>
                  </div>
                  <button className="secondary-button small" onClick={() => loadSession(session.id)}>
                    Resume
                  </button>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    );
  }

  const nextQuestionIndex = detail.turns.length + 1;

  return (
    <div className="interview-layout">
      <section className="panel interview-panel">
        <div className="panel-header">
          <div>
            <span className="eyebrow">Live round</span>
            <h2>{roundLabels[detail.round_type]}</h2>
          </div>
          <StatusPill tone={detail.status === "active" ? "info" : "good"}>{detail.status}</StatusPill>
        </div>
        <div className="progress-line">
          <span>
            {detail.turns.length} / {detail.n_questions} questions
          </span>
          <button className="ghost-button danger" onClick={() => abandon(detail.id)}>
            <Square size={15} />
            End
          </button>
        </div>
        {error ? <div className="error-banner">{error}</div> : null}
        <div className="chat-stack">
          {detail.turns.map((turn) => (
            <TurnView key={turn.id} turn={turn} />
          ))}

          {/* Streaming: question being generated */}
          {streamQuestion ? (
            <div className="chat-bubble coach stream-in">
              <strong>Q{nextQuestionIndex}</strong>
              <p>{streamQuestion}<span className="cursor-blink" /></p>
            </div>
          ) : null}

          {/* Question loading placeholder */}
          {isBusy && !streamQuestion && streamPhase === "idle" && needsQuestion ? (
            <div className="chat-bubble coach thinking-bubble">
              <Loader2 size={16} className="spin" />
              <span>Preparing question {nextQuestionIndex}…</span>
            </div>
          ) : null}

          {/* Pending answer shown immediately after submit */}
          {pendingAnswer ? (
            <div className="chat-bubble candidate stream-in">
              <strong>You</strong>
              <p>{pendingAnswer}</p>
            </div>
          ) : null}

          {/* Evaluation phases */}
          {streamPhase === "evaluating" && !streamFeedback ? (
            <div className="chat-bubble coach thinking-bubble">
              <Loader2 size={16} className="spin" />
              <span>Evaluating your answer…</span>
            </div>
          ) : null}

          {streamFeedback || streamScore !== null ? (
            <div className="chat-bubble coach stream-in">
              <strong>
                {streamScore !== null ? `Score: ${streamScore}/10` : (
                  <span className="score-loading">
                    <Loader2 size={14} className="spin" /> Scoring…
                  </span>
                )}
              </strong>
              <p>{streamFeedback}{streamPhase === "feedback" ? <span className="cursor-blink" /> : null}</p>

              {streamPhase === "model_answer" || streamModelAnswer ? (
                <details open className="stream-in">
                  <summary>Model answer</summary>
                  <p>
                    {streamModelAnswer}
                    {streamPhase === "model_answer" ? <span className="cursor-blink" /> : null}
                  </p>
                </details>
              ) : streamPhase === "feedback" ? (
                <div className="model-answer-loading">
                  <Loader2 size={14} className="spin" />
                  <span>Preparing model answer…</span>
                </div>
              ) : null}
            </div>
          ) : null}

          <div ref={chatBottomRef} />
        </div>

        {needsQuestion && !isBusy ? (
          <button className="primary-button" onClick={handleAskNext}>
            <Play size={18} />
            Next question
          </button>
        ) : null}

        {needsAnswer && !isBusy ? (
          <form className="answer-composer" onSubmit={submitAnswer}>
            <textarea
              rows={5}
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              placeholder="Answer as you would in the interview…"
            />
            <button className="primary-button" type="submit">
              Submit answer
            </button>
          </form>
        ) : null}

        {needsRetry && !isBusy ? (
          <button className="primary-button" onClick={() => submitAnswer()}>
            <RotateCcw size={18} />
            Retry evaluation
          </button>
        ) : null}

        {detail.status !== "active" ? (
          <div className="success-banner">Session {detail.status}. Review it in History.</div>
        ) : null}
      </section>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <>
      <div className="chat-bubble coach">
        <strong>Q{turn.turn_index + 1}</strong>
        <p>{turn.question}</p>
      </div>
      {turn.answer ? (
        <div className="chat-bubble candidate">
          <strong>You</strong>
          <p>{turn.answer}</p>
        </div>
      ) : null}
      {turn.score !== null && turn.score !== undefined ? (
        <div className="chat-bubble coach">
          <strong>Score: {turn.score}/10</strong>
          {turn.feedback ? <p>{turn.feedback}</p> : null}
          {turn.model_answer ? (
            <details>
              <summary>Model answer</summary>
              <p>{turn.model_answer}</p>
            </details>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

function prereqHint(detail: string) {
  const hints: Record<string, string> = {
    profile_missing: "No profile yet. Go to Setup and prepare this job.",
    job_not_analyzed: "This JD has not been analyzed yet. Go to Setup and prepare it.",
    company_snapshot_missing: "Company research is missing. Go to Setup and prepare this job.",
  };
  return hints[detail] ?? detail;
}

function extractStreamError(data: unknown) {
  if (typeof data === "object" && data !== null) {
    const payload = data as { detail?: unknown; code?: unknown };
    return String(payload.detail || payload.code || "Stream failed.");
  }
  return "Stream failed.";
}
