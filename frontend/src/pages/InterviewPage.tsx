import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Loader2, Play, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import Confetti from "react-confetti";

import {
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
import { ArmedDeleteButton } from "../components/ArmedDeleteButton";
import { LoadingStatus } from "../components/LoadingStatus";
import { ErrorBanner } from "../components/ui";
import { codeFrom } from "../errors";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

const roundLabels: Record<RoundType, string> = {
  resume_walkthrough: "Resume / Project deep-dive",
  behavioral_star: "Behavioral / STAR",
};

const roundDescriptions: Record<RoundType, string> = {
  resume_walkthrough:
    "Drill into projects on your CV. Expect follow-ups on tradeoffs, scale, and your specific contribution.",
  behavioral_star:
    "STAR-format questions about how you handled situations. Expect questions on conflict, ownership, and ambiguity.",
};

export function InterviewPage() {
  const { token } = useAuth();
  const { activeJobId, activeJob } = useActiveJob();
  const questionAbort = useStreamAbort();
  const answerAbort = useStreamAbort();
  const windowSize = useWindowSize();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [roundType, setRoundType] = useState<RoundType>("resume_walkthrough");
  const [nQuestions, setNQuestions] = useState(5);
  const [answer, setAnswer] = useState("");
  const [streamQuestion, setStreamQuestion] = useState("");
  const [streamFeedback, setStreamFeedback] = useState("");
  const [streamModelAnswer, setStreamModelAnswer] = useState("");
  const [streamScore, setStreamScore] = useState<number | null>(null);
  const [streamPhase, setStreamPhase] = useState<
    "idle" | "evaluating" | "feedback" | "model_answer" | "done"
  >("idle");
  const [pendingAnswer, setPendingAnswer] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [celebrationPieces, setCelebrationPieces] = useState<number | null>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const celebratedSessionsRef = useRef<Set<string>>(new Set());

  const activeSessions = useMemo(
    () =>
      sessions.filter(
        (session) => session.status === "active" && session.job_id === activeJobId,
      ),
    [sessions, activeJobId],
  );

  const overallScore = useMemo(() => {
    const scored =
      detail?.turns.filter((t) => t.score !== null && t.score !== undefined) ?? [];
    if (!scored.length) return null;
    return scored.reduce((t, x) => t + (x.score ?? 0), 0) / scored.length;
  }, [detail?.turns]);

  const refresh = async () => {
    if (!token) return;
    const [nextJobs, nextSessions] = await Promise.all([
      api.listJobs(token),
      api.listSessions(token),
    ]);
    setJobs(nextJobs);
    setSessions(nextSessions);
    if (activeId) {
      setDetail(await api.getSession(token, activeId));
    }
  };

  useEffect(() => {
    refresh().catch((err: unknown) => setError(codeFrom(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeId]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.turns, streamQuestion, streamFeedback, streamModelAnswer, pendingAnswer]);

  useEffect(() => {
    if (!detail || detail.status !== "complete" || overallScore === null) return;
    if (celebratedSessionsRef.current.has(detail.id)) return;
    celebratedSessionsRef.current.add(detail.id);
    setCelebrationPieces(scoreToConfettiPieces(overallScore));
  }, [detail, overallScore]);

  const askNext = async (sessionId: string, sessionToken: string) => {
    setIsBusy(true);
    setStreamQuestion("");
    setError(null);
    const signal = questionAbort.fresh();
    try {
      await nextQuestionStream(
        sessionToken,
        sessionId,
        (frame: SseFrame) => {
          if (frame.event === "token" && typeof frame.data === "string") {
            setStreamQuestion((c) => c + frame.data);
          }
          if (frame.event === "error") setError(codeFrom(frame.data));
        },
        signal,
      );
      setStreamQuestion("");
      setDetail(await api.getSession(sessionToken, sessionId));
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setIsBusy(false);
    }
  };

  const startSession = async (event: FormEvent) => {
    event.preventDefault();
    if (!token || !activeJobId) return;
    setError(null);
    try {
      const session = await api.createSession(token, activeJobId, roundType, nQuestions);
      setActiveId(session.id);
      setDetail(await api.getSession(token, session.id));
      await refresh();
      await askNext(session.id, token);
    } catch (err) {
      setError(codeFrom(err));
    }
  };

  const abandon = async (id: string) => {
    if (!token) return;
    try {
      await api.abandonSession(token, id);
    } catch (err) {
      setError(codeFrom(err));
    }
    setActiveId(null);
    setDetail(null);
    await refresh();
  };

  const latest = detail?.turns.at(-1);
  // While streamPhase === "done" we're parked on the just-answered turn so
  // the user can read the feedback. All "next-state" affordances are gated
  // off until they click the advance CTA, which flips phase back to idle.
  const showingFeedback = streamPhase === "done";
  const needsQuestion =
    !showingFeedback &&
    detail?.status === "active" &&
    detail.turns.length < detail.n_questions &&
    (!latest || (latest.answer && latest.score !== null && latest.score !== undefined));
  const needsAnswer =
    !showingFeedback && detail?.status === "active" && latest && !latest.answer;
  const needsRetry =
    !showingFeedback &&
    detail?.status === "active" &&
    latest &&
    latest.answer &&
    latest.score === null;
  const isLastTurn =
    detail !== null && detail.turns.length >= detail.n_questions;

  const submitAnswer = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!token || !detail || !latest) return;
    const text = needsRetry ? latest.answer || "" : answer.trim();
    if (!text) {
      setError("Type an answer before submitting.");
      return;
    }
    setPendingAnswer(text);
    setAnswer("");
    setIsBusy(true);
    setStreamFeedback("");
    setStreamModelAnswer("");
    setStreamScore(null);
    setStreamPhase("evaluating");
    setError(null);
    const signal = answerAbort.fresh();
    try {
      await answerStream(
        token,
        detail.id,
        text,
        (frame: SseFrame) => {
          if (frame.event === "score" && typeof frame.data === "object" && frame.data !== null) {
            setStreamScore(Number((frame.data as { score?: number }).score));
            setStreamPhase("feedback");
          }
          if (frame.event === "feedback_token" && typeof frame.data === "string") {
            setStreamPhase("feedback");
            setStreamFeedback((c) => c + frame.data);
          }
          if (frame.event === "model_answer_token" && typeof frame.data === "string") {
            setStreamPhase("model_answer");
            setStreamModelAnswer((c) => c + frame.data);
          }
          if (frame.event === "model_answer_error") {
            setStreamModelAnswer("Model answer unavailable for this turn.");
          }
          if (frame.event === "error") setError(codeFrom(frame.data));
        },
        signal,
      );
      setDetail(await api.getSession(token, detail.id));
      // Keep the streamed feedback/score/model-answer visible — the user
      // hasn't had time to read them yet. Phase flips to "done" so the
      // page renders an explicit "Next question" / "Finish round" CTA.
      // The next user click is what flushes this state.
      setStreamPhase("done");
    } catch (err) {
      setError(codeFrom(err));
      setPendingAnswer(null);
      setStreamPhase("idle");
    } finally {
      setIsBusy(false);
    }
  };

  // Called when the user explicitly advances past the feedback they just
  // read. Flushes the local stream state, then either fetches the next
  // question or simply lets the page render the done-state for the last
  // turn (which the n_questions-reached invariant already guarantees).
  const advance = async () => {
    if (!token || !detail) return;
    setPendingAnswer(null);
    setStreamFeedback("");
    setStreamModelAnswer("");
    setStreamScore(null);
    setStreamPhase("idle");
    // If more turns remain, fetch the next question immediately.
    if (detail.turns.length < detail.n_questions) {
      await askNext(detail.id, token);
    }
  };

  // ───── Empty / setup states ─────

  const parsed = activeJob?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined;
  const role = parsed?.title;
  const company = parsed?.company_name;
  const jobLabel =
    role && company
      ? `${role} @ ${company}`
      : role || company || "Active job";

  if (!activeJobId) {
    return (
      <div className="practice-empty">
        <h1 className="practice-empty-title">Pick a job to practice for</h1>
        <p className="practice-empty-body">
          Use the active-job pill in the sidebar to switch, or set one up in{" "}
          <Link to="/setup">Setup</Link>.
        </p>
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="practice-empty">
        <h1 className="practice-empty-title">No jobs yet</h1>
        <p className="practice-empty-body">
          <Link to="/setup">Set one up</Link> to start practicing.
        </p>
      </div>
    );
  }

  // ───── Start screen ─────

  if (!activeId || !detail) {
    return (
      <div className="practice-start">
        <header className="practice-start-header">
          <span className="practice-start-eyebrow">Ready when you are</span>
          <h1 className="practice-start-title">{jobLabel}</h1>
        </header>

        <ErrorBanner code={error} />

        <form className="practice-start-form" onSubmit={startSession}>
          <fieldset className="round-type-fieldset">
            <legend>Round type</legend>
            {(Object.keys(roundLabels) as RoundType[]).map((rt) => (
              <label
                key={rt}
                className={`round-type-option${roundType === rt ? " selected" : ""}`}
              >
                <input
                  type="radio"
                  name="roundType"
                  value={rt}
                  checked={roundType === rt}
                  onChange={() => setRoundType(rt)}
                />
                <span className="round-type-label">{roundLabels[rt]}</span>
                <span className="round-type-desc">{roundDescriptions[rt]}</span>
              </label>
            ))}
          </fieldset>

          <label className="practice-questions-row">
            <span>
              Questions <strong>{nQuestions}</strong>
            </span>
            <input
              type="range"
              min={1}
              max={10}
              value={nQuestions}
              onChange={(e) => setNQuestions(Number(e.target.value))}
            />
          </label>

          <button className="btn-primary practice-start-cta" type="submit" disabled={isBusy}>
            {isBusy ? <Loader2 size={16} className="spin" /> : <Play size={16} />}
            {isBusy ? "Preparing first question…" : "Start round"}
          </button>
        </form>

        {activeSessions.length > 0 ? (
          <div className="practice-resume">
            <span className="practice-resume-eyebrow">Resume in progress</span>
            <div className="practice-resume-list">
              {activeSessions.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  className="practice-resume-item"
                  onClick={() => setActiveId(s.id)}
                >
                  <span>{roundLabels[s.round_type]}</span>
                  <span className="practice-resume-meta">
                    {new Date(s.created_at).toLocaleDateString(undefined, {
                      month: "short",
                      day: "numeric",
                    })}{" "}
                    · {s.n_questions} questions
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  // ───── Live round ─────

  const nextQuestionIndex = detail.turns.length + 1;
  const turnQuestion =
    needsAnswer || needsRetry || showingFeedback ? latest?.question : null;

  return (
    <div className="practice-live">
      {celebrationPieces !== null ? (
        <Confetti
          width={windowSize.width}
          height={windowSize.height}
          numberOfPieces={celebrationPieces}
          recycle={false}
          run
          gravity={0.18}
          tweenDuration={6500}
          colors={["#C56B62", "#DEA785", "#6C739C", "#BFB9B5", "#f0ebe6"]}
          className="completion-confetti"
          onConfettiComplete={() => setCelebrationPieces(null)}
        />
      ) : null}

      <header className="practice-live-header">
        <span className="practice-live-meta">
          {roundLabels[detail.round_type]} · question{" "}
          {Math.min(detail.turns.length || 1, detail.n_questions)}/{detail.n_questions}
        </span>
        <span className="practice-live-meta">{jobLabel}</span>
      </header>

      <ErrorBanner code={error} />

      {detail.status !== "active" ? (
        <div className="practice-done">
          <h1 className="practice-done-title">Round {detail.status}</h1>
          {overallScore !== null ? (
            <p className="practice-done-score">
              <strong>{overallScore.toFixed(1)}</strong>
              <span>/ 10 average</span>
            </p>
          ) : null}
          <p className="practice-done-hint">
            Review it in <Link to="/history">History</Link>, or start another round.
          </p>
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              setActiveId(null);
              setDetail(null);
            }}
          >
            <RotateCcw size={14} /> Start another round
          </button>
          <details className="practice-done-review">
            <summary>Show this round</summary>
            <div className="practice-transcript">
              {detail.turns.map((t) => (
                <TurnView key={t.id} turn={t} />
              ))}
            </div>
          </details>
        </div>
      ) : (
        <>
          {/* Current question — display type, dominant focus */}
          {streamQuestion ? (
            <article
              className="practice-question stream-in"
              aria-live="polite"
              aria-atomic="false"
            >
              <span className="practice-question-num">Q{nextQuestionIndex}</span>
              <p>
                {streamQuestion}
                <span className="cursor-blink" />
              </p>
            </article>
          ) : turnQuestion ? (
            <article className="practice-question">
              <span className="practice-question-num">Q{latest!.turn_index + 1}</span>
              <p>{turnQuestion}</p>
            </article>
          ) : null}

          {/* Question loading */}
          {isBusy && !streamQuestion && streamPhase === "idle" && needsQuestion ? (
            <div className="practice-loading">
              <Loader2 size={16} className="spin" />
              <LoadingStatus
                active
                messages={[
                  `Preparing question ${nextQuestionIndex}`,
                  "Choosing the sharpest follow-up",
                  "Grounding it in your profile",
                ]}
                fallback={`Preparing question ${nextQuestionIndex}`}
              />
            </div>
          ) : null}

          {needsQuestion && !isBusy && detail.turns.length === 0 ? (
            <button className="btn-primary" onClick={() => askNext(detail.id, token!)}>
              <Play size={14} /> Begin
            </button>
          ) : null}

          {/* Answer composer */}
          {needsAnswer && !isBusy ? (
            <form className="practice-composer" onSubmit={submitAnswer}>
              <textarea
                rows={8}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Answer as you would in the interview…"
                autoFocus
              />
              <div className="practice-composer-actions">
                <button className="btn-primary" type="submit">
                  Submit <ArrowRight size={14} />
                </button>
              </div>
            </form>
          ) : null}

          {/* Pending answer bubble + evaluation */}
          {pendingAnswer ? (
            <article className="practice-your-answer">
              <span className="practice-your-answer-label">Your answer</span>
              <p>{pendingAnswer}</p>
            </article>
          ) : null}

          {streamPhase === "evaluating" && !streamFeedback ? (
            <div className="practice-loading">
              <Loader2 size={16} className="spin" />
              <LoadingStatus
                active
                messages={[
                  "Scoring your structure",
                  "Checking evidence and specificity",
                  "Drafting feedback",
                ]}
                fallback="Evaluating your answer"
              />
            </div>
          ) : null}

          {streamFeedback || streamScore !== null ? (
            <article
              className="practice-feedback stream-in"
              aria-live="polite"
              aria-atomic="false"
            >
              <header>
                {streamScore !== null ? (
                  <span className="practice-feedback-score">
                    <strong>{streamScore}</strong>
                    <span>/ 10</span>
                  </span>
                ) : (
                  <span className="practice-feedback-score loading">
                    <Loader2 size={14} className="spin" /> Scoring…
                  </span>
                )}
              </header>
              <p>
                {streamFeedback}
                {streamPhase === "feedback" ? <span className="cursor-blink" /> : null}
              </p>

              {streamPhase === "model_answer" || streamModelAnswer ? (
                <details open className="stream-in">
                  <summary>Model answer</summary>
                  <p>
                    {streamModelAnswer}
                    {streamPhase === "model_answer" ? <span className="cursor-blink" /> : null}
                  </p>
                </details>
              ) : streamPhase === "feedback" ? (
                <div className="practice-loading subtle">
                  <Loader2 size={14} className="spin" />
                  <LoadingStatus
                    active
                    messages={[
                      "Preparing model answer",
                      "Tuning it to the role",
                      "Making the example sharper",
                    ]}
                    fallback="Preparing model answer"
                  />
                </div>
              ) : null}
            </article>
          ) : null}

          {showingFeedback ? (
            <div className="practice-advance">
              <button className="btn-primary" onClick={advance} disabled={isBusy}>
                {isLastTurn ? (
                  <>
                    Finish round <ArrowRight size={14} />
                  </>
                ) : (
                  <>
                    Next question <ArrowRight size={14} />
                  </>
                )}
              </button>
              <small className="practice-advance-hint">
                {isLastTurn
                  ? "Wraps the round and shows your overall score."
                  : "Take your time — the next question waits until you click."}
              </small>
            </div>
          ) : null}

          {needsRetry && !isBusy ? (
            <button className="btn-primary" onClick={() => submitAnswer()}>
              <RotateCcw size={14} /> Retry evaluation
            </button>
          ) : null}

          {/* Past turns transcript — folded, quiet. Excludes the
              current turn whenever it's being displayed in full above
              (answering, retrying, or reading feedback). */}
          {(() => {
            const hideLatest = needsAnswer || needsRetry || showingFeedback;
            const past = hideLatest ? detail.turns.slice(0, -1) : detail.turns;
            if (past.length <= 0) return null;
            return (
              <details className="practice-transcript-fold">
                <summary>Earlier in this round ({past.length})</summary>
                <div className="practice-transcript">
                  {past.map((t) => (
                    <TurnView key={t.id} turn={t} />
                  ))}
                </div>
              </details>
            );
          })()}

          <div ref={chatBottomRef} />

          <footer className="practice-live-footer">
            <ArmedDeleteButton
              label="End session"
              onConfirm={() => abandon(detail.id)}
              className="btn-quiet"
            />
            <small className="practice-end-hint">You can still review it in History.</small>
          </footer>
        </>
      )}
    </div>
  );
}

function scoreToConfettiPieces(score: number) {
  const clamped = Math.max(0, Math.min(10, score));
  return Math.round(80 + clamped * 32);
}

function useWindowSize() {
  const getSize = () => ({
    width: typeof window === "undefined" ? 300 : window.innerWidth,
    height: typeof window === "undefined" ? 200 : window.innerHeight,
  });
  const [size, setSize] = useState(getSize);
  useEffect(() => {
    const onResize = () => setSize(getSize());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return size;
}

function TurnView({ turn }: { turn: Turn }) {
  return (
    <div className="practice-past-turn">
      <strong>Q{turn.turn_index + 1}</strong>
      <p className="practice-past-q">{turn.question}</p>
      {turn.answer ? (
        <>
          <span className="practice-past-label">You</span>
          <p>{turn.answer}</p>
        </>
      ) : null}
      {turn.score !== null && turn.score !== undefined ? (
        <>
          <span className="practice-past-label">
            {turn.score}/10
          </span>
          {turn.feedback ? <p>{turn.feedback}</p> : null}
          {turn.model_answer ? (
            <details>
              <summary>Model answer</summary>
              <p>{turn.model_answer}</p>
            </details>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
