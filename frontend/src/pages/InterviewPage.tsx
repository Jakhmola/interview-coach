import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Loader2, Play, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import Confetti from "react-confetti";

import {
  JobItem,
  MoveKind,
  RoundType,
  Session,
  SessionDetail,
  SseFrame,
  Thread,
  api,
  messageStream,
} from "../api";
import { ArmedDeleteButton } from "../components/ArmedDeleteButton";
import { LoadingStatus } from "../components/LoadingStatus";
import { ErrorBanner } from "../components/ui";
import { codeFrom } from "../errors";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";

const roundLabels: Record<RoundType, string> = {
  experience_deep_dive: "Experience deep-dive",
  technical_challenge: "Technical challenge",
  behavioral_star: "Behavioral / STAR",
};

const roundDescriptions: Record<RoundType, string> = {
  experience_deep_dive:
    "Drill into what you've actually built — CV highlights, project docs, and your GitHub repos. Repo-backed projects get implementation-level questions.",
  technical_challenge:
    "Forward-looking problems on the role's must-have skills, scaled to seniority — from fundamentals through system design. Tests whether you can do the work.",
  behavioral_star:
    "STAR-format questions on how you work with people — conflict, ownership, ambiguity — grounded in the role's signals and the company's values.",
};

// Phase 34: the interviewer no longer asks one fixed question per turn — it
// works a topic (a Thread) one move at a time, deciding at runtime whether to
// probe, clarify, nudge, or advance. So the page is a chat transcript, and
// each `/message` round-trip streams the interviewer's next move(s) (and, when
// a topic closes, its single evaluation + possibly the next topic's question).

type LiveMove = {
  type: "move";
  key: string;
  kind: MoveKind;
  threadIndex: number;
  text: string;
};

type EvalPhase = "evaluating" | "feedback" | "model_answer" | "done";

type LiveEval = {
  type: "eval";
  key: string;
  threadIndex: number;
  score: number | null;
  feedback: string;
  modelAnswer: string;
  phase: EvalPhase;
};

type LiveItem = LiveMove | LiveEval;

function updateLast<T extends LiveItem["type"]>(
  items: LiveItem[],
  type: T,
  patch: (item: Extract<LiveItem, { type: T }>) => Extract<LiveItem, { type: T }>,
): LiveItem[] {
  for (let i = items.length - 1; i >= 0; i--) {
    if (items[i].type === type) {
      const next = items.slice();
      next[i] = patch(items[i] as Extract<LiveItem, { type: T }>);
      return next;
    }
  }
  return items;
}

const moveLabels: Record<MoveKind, string> = {
  question: "Question",
  probe: "Follow-up",
  clarify: "Clarify",
  nudge: "Nudge",
};

export function InterviewPage() {
  const { token } = useAuth();
  const { activeJobId, activeJob } = useActiveJob();
  const messageAbort = useStreamAbort();
  const windowSize = useWindowSize();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [roundType, setRoundType] = useState<RoundType>("experience_deep_dive");
  const [nQuestions, setNQuestions] = useState(5);
  const [answer, setAnswer] = useState("");
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
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

  const overallScore = useMemo(() => scoreThreads(detail?.threads), [detail?.threads]);

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
  }, [detail?.threads, liveItems, pendingAnswer]);

  useEffect(() => {
    if (!detail || detail.status !== "complete" || overallScore === null) return;
    if (celebratedSessionsRef.current.has(detail.id)) return;
    celebratedSessionsRef.current.add(detail.id);
    setCelebrationPieces(scoreToConfettiPieces(overallScore));
  }, [detail, overallScore]);

  // The single conversational round-trip. `message === null` opens the session
  // (first thread); otherwise it's the candidate's answer. The response streams
  // the interviewer's next move(s) as an action envelope; we overlay them as
  // `liveItems`, then refetch the authoritative session detail and drop the
  // overlay (the persisted threads now carry everything).
  const sendMessage = async (sessionId: string, message: string | null) => {
    if (!token) return;
    setIsBusy(true);
    setError(null);
    setLiveItems([]);
    const signal = messageAbort.fresh();
    try {
      await messageStream(token, sessionId, message, handleFrame, signal);
      setDetail(await api.getSession(token, sessionId));
    } catch (err) {
      setError(codeFrom(err));
    } finally {
      setLiveItems([]);
      setPendingAnswer(null);
      setIsBusy(false);
    }
  };

  const handleFrame = (frame: SseFrame) => {
    switch (frame.event) {
      case "move": {
        const d = frame.data as { kind: MoveKind; thread_index: number };
        setLiveItems((items) => [
          ...items,
          {
            type: "move",
            key: `m${items.length}`,
            kind: d.kind,
            threadIndex: d.thread_index,
            text: "",
          },
        ]);
        break;
      }
      case "token": {
        if (typeof frame.data !== "string") break;
        const t = frame.data;
        setLiveItems((items) => updateLast(items, "move", (m) => ({ ...m, text: m.text + t })));
        break;
      }
      case "evaluation": {
        const d = frame.data as { thread_index: number };
        setLiveItems((items) => [
          ...items,
          {
            type: "eval",
            key: `e${items.length}`,
            threadIndex: d.thread_index,
            score: null,
            feedback: "",
            modelAnswer: "",
            phase: "evaluating",
          },
        ]);
        break;
      }
      case "score": {
        // Phase 34: the score rides the wire as a bare integer.
        const s = Number(frame.data);
        setLiveItems((items) =>
          updateLast(items, "eval", (e) => ({
            ...e,
            score: s,
            phase: e.phase === "evaluating" ? "feedback" : e.phase,
          })),
        );
        break;
      }
      case "feedback_token": {
        if (typeof frame.data !== "string") break;
        const t = frame.data;
        setLiveItems((items) =>
          updateLast(items, "eval", (e) => ({ ...e, feedback: e.feedback + t, phase: "feedback" })),
        );
        break;
      }
      case "model_answer_token": {
        if (typeof frame.data !== "string") break;
        const t = frame.data;
        setLiveItems((items) =>
          updateLast(items, "eval", (e) => ({
            ...e,
            modelAnswer: e.modelAnswer + t,
            phase: "model_answer",
          })),
        );
        break;
      }
      case "model_answer_error": {
        setLiveItems((items) =>
          updateLast(items, "eval", (e) => ({
            ...e,
            modelAnswer: "Model answer unavailable for this topic.",
          })),
        );
        break;
      }
      case "evaluation_done": {
        setLiveItems((items) => updateLast(items, "eval", (e) => ({ ...e, phase: "done" })));
        break;
      }
      case "error":
        setError(codeFrom(frame.data));
        break;
      // "move_done" / "wrap" need no transcript change — the refetch reflects them.
      default:
        break;
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
      // Empty body opens the first thread + streams the opening question.
      await sendMessage(session.id, null);
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

  const openThread = useMemo(
    () => detail?.threads.find((t) => t.status === "open") ?? null,
    [detail?.threads],
  );
  const lastMessage = openThread?.messages.at(-1) ?? null;
  const awaitingAnswer =
    detail?.status === "active" && !!openThread && lastMessage?.role === "interviewer";
  const needsBegin =
    detail?.status === "active" && (detail?.threads.length ?? 0) === 0;
  const composerOpen =
    awaitingAnswer && !isBusy && liveItems.length === 0 && pendingAnswer === null;

  const submitAnswer = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!token || !detail) return;
    const text = answer.trim();
    if (!text) {
      setError("empty_message");
      return;
    }
    setAnswer("");
    setPendingAnswer(text);
    await sendMessage(detail.id, text);
  };

  // ───── Empty / setup states ─────

  const parsed = activeJob?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined;
  const role = parsed?.title;
  const company = parsed?.company_name;
  const jobLabel = role && company ? `${role} @ ${company}` : role || company || "Active job";

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
              Topics <strong>{nQuestions}</strong>
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
            {isBusy ? "Opening the conversation…" : "Start round"}
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
                    · {s.n_questions} topics
                  </span>
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    );
  }

  // ───── Completed / abandoned ─────

  if (detail.status !== "active") {
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

        <ErrorBanner code={error} />

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
              {detail.threads.map((t) => (
                <ThreadReview key={t.id} thread={t} />
              ))}
            </div>
          </details>
        </div>
      </div>
    );
  }

  // ───── Live round (chat) ─────

  const closedThreads = detail.threads.filter((t) => t.status === "closed");
  const topicNum = Math.min(Math.max(detail.threads.length, 1), detail.n_questions);
  const activeNodes = buildActiveTopic(openThread, pendingAnswer, liveItems);
  // Busy with nothing rendered yet → the interviewer is composing its move.
  const showThinking = isBusy && liveItems.length === 0;

  return (
    <div className="practice-live">
      <header className="practice-live-header">
        <span className="practice-live-meta">
          {roundLabels[detail.round_type]} · topic {topicNum}/{detail.n_questions}
        </span>
        <span className="practice-live-meta">{jobLabel}</span>
      </header>

      <ErrorBanner code={error} />

      {/* The active topic lives in its own card; closed topics fall to the
          collapsed history below (new topic = fresh card at the top). */}
      {activeNodes.length > 0 || showThinking ? (
        <div className="practice-chat">
          {activeNodes}

          {showThinking ? (
            <div className="practice-loading">
              <Loader2 size={16} className="spin" />
              <LoadingStatus
                active
                messages={[
                  "Reading your answer",
                  "Deciding the sharpest next move",
                  "Grounding it in your profile",
                ]}
                fallback="Thinking"
              />
            </div>
          ) : null}

          <div ref={chatBottomRef} />
        </div>
      ) : null}

      {needsBegin && !isBusy ? (
        <button className="btn-primary" onClick={() => sendMessage(detail.id, null)}>
          <Play size={14} /> Begin
        </button>
      ) : null}

      {composerOpen ? (
        <form className="practice-composer" onSubmit={submitAnswer}>
          <textarea
            rows={6}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Answer as you would in the interview…"
            autoFocus
          />
          <div className="practice-composer-actions">
            <button className="btn-primary" type="submit">
              Send <ArrowRight size={14} />
            </button>
          </div>
        </form>
      ) : null}

      {closedThreads.length > 0 ? (
        <section className="practice-history">
          <h2 className="practice-history-title">Previous topics</h2>
          {closedThreads.map((t) => (
            <ClosedTopicRow key={t.id} thread={t} />
          ))}
        </section>
      ) : null}

      <footer className="practice-live-footer">
        <ArmedDeleteButton
          label="End session"
          onConfirm={() => abandon(detail.id)}
          className="btn-quiet"
        />
        <small className="practice-end-hint">You can still review it in History.</small>
      </footer>
    </div>
  );
}

// --- transcript rendering ---------------------------------------------------

// Only the CURRENT topic renders in the active card: the open thread's
// persisted messages, the candidate's just-sent answer (not yet persisted),
// and the live stream overlay. Closed topics render in the history below.
function buildActiveTopic(
  openThread: Thread | null,
  pendingAnswer: string | null,
  liveItems: LiveItem[],
): ReactNode[] {
  const nodes: ReactNode[] = [];
  if (openThread) {
    for (const m of openThread.messages) {
      if (m.role === "candidate") {
        nodes.push(<AnswerBubble key={m.id} text={m.text} />);
      } else {
        nodes.push(
          <MoveBubble
            key={m.id}
            kind={m.kind ?? "question"}
            num={openThread.thread_index + 1}
            text={m.text}
          />,
        );
      }
    }
  }
  if (pendingAnswer !== null) {
    nodes.push(<AnswerBubble key="pending" text={pendingAnswer} />);
  }
  for (const item of liveItems) {
    if (item.type === "move") {
      nodes.push(
        <MoveBubble key={item.key} kind={item.kind} num={item.threadIndex + 1} text={item.text} typing />,
      );
    } else {
      nodes.push(
        <EvalCard
          key={item.key}
          score={item.score}
          feedback={item.feedback}
          modelAnswer={item.modelAnswer}
          phase={item.phase}
        />,
      );
    }
  }
  return nodes;
}

// A closed topic, collapsed into one row in the bottom "Previous topics" list.
function ClosedTopicRow({ thread }: { thread: Thread }) {
  const num = thread.thread_index + 1;
  return (
    <details className="practice-history-item">
      <summary className="practice-history-summary">
        <span className="practice-history-topic">Topic {num}</span>
        {thread.focus_label ? (
          <span className="practice-history-focus">{thread.focus_label}</span>
        ) : null}
        {thread.score !== null && thread.score !== undefined ? (
          <span className="practice-history-score">{thread.score}/10</span>
        ) : null}
      </summary>
      <div className="practice-history-body">
        {thread.messages.map((m) => (
          <div key={m.id} className="practice-history-msg">
            <span className="practice-history-role">
              {m.role === "candidate"
                ? "You"
                : m.kind === "question"
                  ? `Q${num}`
                  : moveLabels[m.kind ?? "question"]}
            </span>
            <p>{m.text}</p>
          </div>
        ))}
        {thread.feedback ? (
          <div className="practice-history-eval">
            <p>{thread.feedback}</p>
            {thread.model_answer ? (
              <details className="practice-history-model">
                <summary>Model answer</summary>
                <p>{thread.model_answer}</p>
              </details>
            ) : null}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function MoveBubble({
  kind,
  num,
  text,
  typing,
}: {
  kind: MoveKind;
  num: number;
  text: string;
  typing?: boolean;
}) {
  const label = kind === "question" ? `Q${num}` : moveLabels[kind];
  return (
    <article className={`practice-question${typing ? " stream-in" : ""}`} aria-live="polite">
      <span className="practice-question-num">{label}</span>
      <p>
        {text}
        {typing ? <span className="cursor-blink" /> : null}
      </p>
    </article>
  );
}

function AnswerBubble({ text }: { text: string }) {
  return (
    <article className="practice-your-answer">
      <span className="practice-your-answer-label">Your answer</span>
      <p>{text}</p>
    </article>
  );
}

function EvalCard({
  score,
  feedback,
  modelAnswer,
  phase,
}: {
  score: number | null;
  feedback: string;
  modelAnswer: string;
  phase: EvalPhase;
}) {
  if (phase === "evaluating" && !feedback && score === null) {
    return (
      <div className="practice-loading">
        <Loader2 size={16} className="spin" />
        <LoadingStatus
          active
          messages={["Scoring your structure", "Checking evidence and specificity", "Drafting feedback"]}
          fallback="Evaluating this topic"
        />
      </div>
    );
  }
  return (
    <article className="practice-feedback stream-in" aria-live="polite">
      <header>
        {score !== null ? (
          <span className="practice-feedback-score">
            <strong>{score}</strong>
            <span>/ 10</span>
          </span>
        ) : (
          <span className="practice-feedback-score loading">
            <Loader2 size={14} className="spin" /> Scoring…
          </span>
        )}
      </header>
      <p>
        {feedback}
        {phase === "feedback" ? <span className="cursor-blink" /> : null}
      </p>
      {phase === "model_answer" || modelAnswer ? (
        <details open className="stream-in">
          <summary>Model answer</summary>
          <p>
            {modelAnswer}
            {phase === "model_answer" ? <span className="cursor-blink" /> : null}
          </p>
        </details>
      ) : phase === "feedback" ? (
        <div className="practice-loading subtle">
          <Loader2 size={14} className="spin" />
          <LoadingStatus
            active
            messages={["Preparing model answer", "Tuning it to the role", "Making the example sharper"]}
            fallback="Preparing model answer"
          />
        </div>
      ) : null}
    </article>
  );
}

function ThreadReview({ thread }: { thread: Thread }) {
  return (
    <div className="practice-past-turn">
      <strong>Topic {thread.thread_index + 1}</strong>
      {thread.focus_label ? <p className="practice-past-focus">{thread.focus_label}</p> : null}
      {thread.messages.map((m) => (
        <div key={m.id}>
          <span className="practice-past-label">
            {m.role === "candidate" ? "You" : m.kind === "question" ? "Interviewer" : moveLabels[m.kind ?? "question"]}
          </span>
          <p className={m.role === "candidate" ? "" : "practice-past-q"}>{m.text}</p>
        </div>
      ))}
      {thread.status === "closed" && thread.score !== null && thread.score !== undefined ? (
        <>
          <span className="practice-past-label">{thread.score}/10</span>
          {thread.feedback ? <p>{thread.feedback}</p> : null}
          {thread.model_answer ? (
            <details>
              <summary>Model answer</summary>
              <p>{thread.model_answer}</p>
            </details>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

// --- helpers ----------------------------------------------------------------

function scoreThreads(threads: Thread[] | undefined): number | null {
  const scored = threads?.filter((t) => t.score !== null && t.score !== undefined) ?? [];
  if (!scored.length) return null;
  return scored.reduce((total, t) => total + (t.score ?? 0), 0) / scored.length;
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
