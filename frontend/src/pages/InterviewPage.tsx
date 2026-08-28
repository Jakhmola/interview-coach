import { FormEvent, KeyboardEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, ChevronRight, Play, RotateCcw } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  DocumentItem,
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
import { ErrorBanner, Field, JobField, PenNote, RatingCells, SheetHead } from "../components/ui";
import { codeFrom } from "../errors";
import { moveLabels, roundLabels, topicLabel, topicTitle, verdict } from "../jobLabel";
import { useStreamAbort } from "../hooks/useStreamAbort";
import { useActiveJob } from "../state/activeJob";
import { useAuth } from "../state/auth";
import { viewTransition } from "../viewTransition";

const roundDescriptions: Record<RoundType, string> = {
  experience_deep_dive:
    "Drill into what you've actually built: CV highlights, project docs, and your GitHub repos. Repo-backed projects get implementation-level questions.",
  technical_challenge:
    "Forward-looking problems on the role's must-have skills, scaled to seniority, from fundamentals through system design. Tests whether you can do the work.",
  behavioral_star:
    "STAR-format questions on how you work with people: conflict, ownership, ambiguity. Grounded in the role's signals and the company's values.",
};

// Phase 34: the interviewer works a topic (a Thread) one move at a time,
// deciding at runtime whether to probe, clarify, nudge, or advance. The page
// renders the scorecard for the open topic; each `/message` round-trip
// streams the interviewer's next move(s) (and, when a topic closes, its
// evaluation + possibly the next topic's question).

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

export function InterviewPage() {
  const { token, user } = useAuth();
  const { activeJobId, activeJob } = useActiveJob();
  const messageAbort = useStreamAbort();
  const navigate = useNavigate();
  // The open round is the URL (`/interview/:sessionId`): starting or resuming
  // one pushes it, so Back is the start screen, where the round stays listed.
  const { sessionId } = useParams();
  const activeId = sessionId ?? null;
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [docs, setDocs] = useState<DocumentItem[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [roundType, setRoundType] = useState<RoundType>("experience_deep_dive");
  const [nQuestions, setNQuestions] = useState(5);
  const [answer, setAnswer] = useState("");
  const [liveItems, setLiveItems] = useState<LiveItem[]>([]);
  const [pendingAnswer, setPendingAnswer] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // True while the round's closing marks (stamp, highlighter, verdict) are
  // being made for the first time this visit; a revisit shows them still.
  const [stamped, setStamped] = useState(false);
  // The scored topic kept on the sheet, assessment and all, until the
  // candidate turns the page; the next question is written behind it.
  const [heldIndex, setHeldIndex] = useState<number | null>(null);
  // The thread the current round-trip was sent for; a streamed move for any
  // other thread is the next topic opening.
  const openIndexRef = useRef(-1);
  const bottomRef = useRef<HTMLDivElement>(null);
  const stampedSessionsRef = useRef<Set<string>>(new Set());

  const activeSessions = useMemo(
    () =>
      sessions.filter(
        (session) => session.status === "active" && session.job_id === activeJobId,
      ),
    [sessions, activeJobId],
  );

  const overallScore = useMemo(() => scoreThreads(detail?.threads), [detail?.threads]);

  // Settle the sheet on the persisted round. The first time a round arrives
  // closed, its closing marks play - in the same render, so the stamp never
  // shows still for a frame before it lands. A round that closes on this
  // sheet (`hold`) keeps its last assessment readable instead; the stamp
  // waits for the candidate to ask for the scorecard (turnPage).
  const settle = (next: SessionDetail, hold = false) => {
    setDetail(next);
    if (next.status !== "active" && !stampedSessionsRef.current.has(next.id)) {
      const last = next.threads.filter((t) => t.status === "closed").at(-1);
      if (hold && last) {
        setHeldIndex(last.thread_index);
        return;
      }
      stampedSessionsRef.current.add(next.id);
      setStamped(true);
    }
  };

  // The candidate turns the page: the scored topic folds into the index and
  // the next question - or the closing stamp - reads from the top of the sheet.
  const turnPage = () => {
    const closing = detail !== null && detail.status !== "active";
    void viewTransition(() => {
      setHeldIndex(null);
      if (closing && !stampedSessionsRef.current.has(detail.id)) {
        stampedSessionsRef.current.add(detail.id);
        setStamped(true);
      }
    }).updateCallbackDone.then(() => window.scrollTo({ top: 0, behavior: "smooth" }));
  };

  const refresh = async () => {
    if (!token) return;
    const [nextJobs, nextSessions, nextDocs] = await Promise.all([
      api.listJobs(token),
      api.listSessions(token),
      api.listDocuments(token).catch(() => [] as DocumentItem[]),
    ]);
    setJobs(nextJobs);
    setSessions(nextSessions);
    setDocs(nextDocs);
    if (activeId) {
      settle(await api.getSession(token, activeId));
    }
  };

  useEffect(() => {
    // A different (or no) round in the URL: drop the last one's sheet and
    // its overlay before the new one loads, so nothing stale shows.
    messageAbort.abort();
    setDetail(null);
    setStamped(false);
    setHeldIndex(null);
    setLiveItems([]);
    setPendingAnswer(null);
    setIsBusy(false);
    refresh().catch((err: unknown) => setError(codeFrom(err)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, activeId]);

  useEffect(() => {
    // Follow the conversation down the sheet - except when the page has just
    // turned to a new topic, which reads from the top.
    const turned = liveItems.some((i) => i.type === "move" && i.threadIndex !== openIndexRef.current);
    if (turned) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [detail?.threads, liveItems, pendingAnswer]);

  // The single conversational round-trip. `message === null` opens the session
  // (first thread); otherwise it's the candidate's answer. The response streams
  // the interviewer's next move(s) as an action envelope; we overlay them as
  // `liveItems`, then refetch the authoritative session detail and drop the
  // overlay (the persisted threads now carry everything).
  //
  // Both ends of the round-trip are view transitions: `prepare` files the
  // typed answer as a response (the reply box morphs into it), and the
  // refetch settles the sheet on the persisted threads. A page turn - the
  // next topic opening - happens mid-stream, the moment its first frame
  // arrives (see handleFrame). The composer comes back only once the
  // refetch has settled, so it never gets morphed into.
  const sendMessage = async (sessionId: string, message: string | null, prepare?: () => void) => {
    if (!token) return;
    openIndexRef.current = detail?.threads.find((t) => t.status === "open")?.thread_index ?? -1;
    setError(null);
    const signal = messageAbort.fresh();
    await viewTransition(() => {
      prepare?.();
      setIsBusy(true);
      setLiveItems([]);
    }).updateCallbackDone;
    try {
      await messageStream(token, sessionId, message, handleFrame, signal);
      const next = await api.getSession(token, sessionId);
      await viewTransition(() => {
        settle(next, true);
        setLiveItems([]);
        setPendingAnswer(null);
      }).finished;
    } catch (err) {
      setError(codeFrom(err));
      setLiveItems([]);
      setPendingAnswer(null);
    } finally {
      setIsBusy(false);
    }
  };

  const handleFrame = (frame: SseFrame) => {
    switch (frame.event) {
      case "move": {
        const d = frame.data as { kind: MoveKind; thread_index: number };
        const apply = () =>
          setLiveItems((items) => [
            ...items,
            { type: "move", key: `m${items.length}`, kind: d.kind, threadIndex: d.thread_index, text: "" },
          ]);
        if (d.thread_index !== openIndexRef.current && openIndexRef.current < 0) {
          // The round's first question: its box comes onto the sheet.
          void viewTransition(apply).updateCallbackDone.then(() =>
            window.scrollTo({ top: 0, behavior: "smooth" }),
          );
        } else if (d.thread_index !== openIndexRef.current) {
          // The next topic is opening while the candidate reads the
          // assessment: keep this page and write the question behind it;
          // the candidate turns the page (turnPage) when ready.
          apply();
          setHeldIndex(openIndexRef.current);
        } else {
          apply();
        }
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
      // "move_done" / "wrap" need no transcript change: the refetch reflects them.
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
      navigate(`/interview/${session.id}`);
      settle(await api.getSession(token, session.id));
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
    navigate("/interview");
  };

  const openThread = useMemo(
    () => detail?.threads.find((t) => t.status === "open") ?? null,
    [detail?.threads],
  );
  const lastMessage = openThread?.messages.at(-1) ?? null;
  const awaitingAnswer =
    detail?.status === "active" && !!openThread && lastMessage?.role === "interviewer";
  const needsBegin = detail?.status === "active" && (detail?.threads.length ?? 0) === 0;
  const composerOpen =
    awaitingAnswer && !isBusy && liveItems.length === 0 && pendingAnswer === null && heldIndex === null;

  const submitAnswer = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!token || !detail) return;
    const text = answer.trim();
    if (!text) {
      setError("empty_message");
      return;
    }
    await sendMessage(detail.id, text, () => {
      setAnswer("");
      setPendingAnswer(text);
    });
  };

  const onComposerKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      void submitAnswer();
    }
  };

  // ───── Empty / setup states ─────

  const parsed = activeJob?.parsed_json as
    | { title?: string; company_name?: string }
    | null
    | undefined;
  const role = parsed?.title;
  const company = parsed?.company_name;
  const jobLabel = role && company ? `${role} @ ${company}` : role || company || "Active job";
  const candidate = user?.email;

  if (!activeJobId || jobs.length === 0) {
    return (
      <div className="practice-empty-wrap">
        <SheetHead title="Interview scorecard" page="No round open">
          <Field label="Candidate" value={candidate} />
          <JobField />
        </SheetHead>
        <div className="practice-empty">
          <h1 className="practice-empty-title">
            {jobs.length === 0 ? "No job description on file yet" : "Pick a job to practice for"}
          </h1>
          <p className="practice-empty-body">
            {jobs.length === 0 ? (
              <>
                <Link to="/setup">Set one up</Link> to start practicing.
              </>
            ) : (
              <>
                Use the role / company field above to switch, or add one in{" "}
                <Link to="/setup">Setup</Link>.
              </>
            )}
          </p>
        </div>
      </div>
    );
  }

  // ───── Start screen ─────

  if (!activeId || !detail) {
    return (
      <div className="practice-start">
        <SheetHead title="Interview scorecard" page="New round">
          <Field label="Candidate" value={candidate} />
          <JobField />
          <Field
            label="Date"
            value={new Date().toLocaleDateString(undefined, { dateStyle: "medium" })}
          />
        </SheetHead>

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
            <Play />
            {isBusy ? "Opening the conversation…" : "Start round"}
          </button>
        </form>

        {activeSessions.length > 0 ? (
          <div className="practice-resume">
            <span className="practice-resume-eyebrow">Rounds in progress</span>
            <div className="practice-resume-list">
              {activeSessions.map((s) => (
                <button
                  type="button"
                  key={s.id}
                  className="practice-resume-item"
                  onClick={() => navigate(`/interview/${s.id}`)}
                >
                  <span>Resume {roundLabels[s.round_type]}</span>
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

  if (detail.status !== "active" && heldIndex === null) {
    const scored = detail.threads.filter((t) => t.score !== null && t.score !== undefined);
    const complete = detail.status === "complete";
    const roundDate = new Date(detail.created_at).toLocaleDateString(undefined, { dateStyle: "medium" });
    const closing = complete ? verdict(detail.threads) : null;
    return (
      <div className="practice-live">
        <SheetHead
          title="Interview scorecard"
          page={`${roundLabels[detail.round_type]} · ${detail.status === "complete" ? "Complete" : "Abandoned"}`}
        >
          <Field label="Candidate" value={candidate} />
          <JobField />
          <Field label="Date" value={roundDate} />
          <Field label="Topics" value={`${scored.length} of ${detail.n_questions} scored`} />
        </SheetHead>

        <ErrorBanner code={error} />

        <div className={stamped ? "practice-done play" : "practice-done"}>
          <div className="done-head">
            <div className="done-main">
              <h1 className="practice-done-title">
                {complete ? <mark>Round complete.</mark> : "Round abandoned."}
              </h1>
              {overallScore !== null ? (
                <div className="practice-done-score">
                  <span className="cap">Average</span>
                  <RatingCells
                    score={Math.round(overallScore)}
                    label={`Average ${overallScore.toFixed(1)} out of 10`}
                  />
                  <span className="hint">
                    {overallScore.toFixed(1)} / 10 over {scored.length} topic{scored.length === 1 ? "" : "s"}
                  </span>
                  {closing ? <PenNote label="Verdict" text={closing} /> : null}
                </div>
              ) : null}
              <p className="practice-done-hint">
                Filed to <Link to="/history">History</Link>. Start another round whenever you're ready.
              </p>
              <div>
                <button type="button" className="btn-primary" onClick={() => navigate("/interview")}>
                  <RotateCcw /> Start another round
                </button>
              </div>
            </div>
            <span className={complete ? "stamp" : "stamp bad"} aria-hidden="true">
              <span>{complete ? "Complete" : "Abandoned"}</span>
              <small>{roundDate}</small>
            </span>
          </div>
          {detail.threads.length > 0 ? (
            <section className="prev" aria-label="Topics in this round">
              <span className="cap">Topics</span>
              {detail.threads.map((t) => (
                <PrevTopic key={t.id} thread={t} docs={docs} />
              ))}
            </section>
          ) : null}
        </div>
      </div>
    );
  }

  // ───── Live round: the scorecard ─────

  const showThinking = isBusy && liveItems.length === 0;
  const lastIsFollowUp = lastMessage?.role === "interviewer" && lastMessage.kind !== "question";

  // While the candidate reads a scored topic, that topic is the sheet - not
  // the one the interviewer has already opened behind it.
  const held = heldIndex === null ? null : (detail.threads.find((t) => t.thread_index === heldIndex) ?? null);
  const sheetThread = held ?? openThread;
  const { question, main, margin, closing, topicIndex } = buildScorecard({
    openThread: sheetThread,
    pendingAnswer,
    liveItems,
    docs,
    showThinking,
    held: held !== null,
  });
  // A topic scored mid-stream is already filed under Previous topics while
  // the next question streams; the refetch replaces it with the real row.
  // The held topic stays on the sheet, so it is not in the index yet.
  const prevThreads = [
    ...detail.threads.filter((t) => t.status === "closed" && t.thread_index !== heldIndex),
    ...(closing ? [closing] : []),
  ];
  const topicNum = Math.min(topicIndex + 1, detail.n_questions);

  return (
    <div className="practice-live">
      <SheetHead title="Interview scorecard" page={roundLabels[detail.round_type]}>
        <Field label="Candidate" value={candidate} />
        <JobField />
        <Field
          label="Topic"
          value={closing ? undefined : topicTitle(sheetThread?.focus_label)}
          empty="(opening the topic)"
          wrap
        />
        <Field label="No." value={`${topicNum} of ${detail.n_questions}`} />
      </SheetHead>

      <ErrorBanner code={error} />

      {needsBegin && !isBusy ? (
        <div>
          <button className="btn-primary" type="button" onClick={() => sendMessage(detail.id, null)}>
            <Play /> Begin
          </button>
        </div>
      ) : null}

      <div className="scorecard">
        {question}

        {/* DOM order is question -> margin -> main so the single-column
            (mobile) flow reads note and agenda before the responses; the
            desktop grid places the margin explicitly. */}
        <aside className="sc-margin" aria-label="Interviewer's margin">
          {margin}
        </aside>

        <div className="sc-main">
          {main}

          {composerOpen ? (
            <form className="composer stream-in" onSubmit={submitAnswer}>
              <p className="turn">
                <mark>Your turn</mark>{" "}
                {lastIsFollowUp
                  ? "respond to the interviewer's note in the margin."
                  : "answer the question above."}
              </p>
              <div className="box reply">
                <textarea
                  className="typed"
                  rows={4}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={onComposerKey}
                  placeholder="Answer as you would in the interview…"
                  aria-label="Your response"
                  autoFocus
                />
              </div>
              <div className="actions">
                <span className="hint">Ctrl + Enter submits</span>
                <button className="btn" type="submit">
                  Submit response <ArrowRight />
                </button>
              </div>
            </form>
          ) : null}

          {heldIndex !== null ? (
            <div className="composer stream-in">
              <p className="turn">
                <mark>Your turn</mark>{" "}
                {detail.status === "active"
                  ? "read the assessment, then turn the page."
                  : "read the last assessment, then see the scorecard."}
              </p>
              <div className="actions">
                <span className="hint">
                  {detail.status !== "active"
                    ? "Every topic is scored"
                    : isBusy
                      ? "The interviewer is writing the next question"
                      : "The next question is ready"}
                </span>
                <button className="btn-primary" type="button" onClick={turnPage}>
                  {detail.status === "active" ? "Next topic" : "See the scorecard"} <ArrowRight />
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div ref={bottomRef} />

      {prevThreads.length > 0 ? (
        <section className="prev" aria-label="Previous topics">
          <span className="cap">Previous topics</span>
          {prevThreads.map((t) => (
            <PrevTopic key={t.id} thread={t} docs={docs} />
          ))}
        </section>
      ) : null}

      <footer className="practice-live-footer">
        <ArmedDeleteButton
          label="End session"
          armedLabel="Click again to end"
          consequenceLabel="files what you have so far to History"
          onConfirm={() => abandon(detail.id)}
          className="btn-ghost danger"
        />
      </footer>
    </div>
  );
}

// --- scorecard rows ---------------------------------------------------------

type Utterance = {
  key: string;
  role: "interviewer" | "candidate";
  kind: MoveKind;
  text: string;
  typing?: boolean;
};

/**
 * Lays the open topic out as the scorecard: the question box, then the main
 * column (each typed response, the interviewer's earlier remarks written
 * inline between them, the live evaluation), and the margin (the
 * interviewer's latest note in red pen, level with the question as on the
 * comp, over the thread's agenda and sources). Live stream items overlay at
 * the end. A move for a *different* thread is the next topic opening: the
 * sheet turns - the topic just scored is returned as `closing`, a closed
 * thread built from the live evaluation so it files under Previous topics
 * (its assessment box folds into that row), and the new question streams
 * in the question box of an otherwise clean sheet. Its agenda and sources
 * arrive with the refetch.
 */
function buildScorecard({
  openThread,
  pendingAnswer,
  liveItems,
  docs,
  showThinking,
  held,
}: {
  openThread: Thread | null;
  pendingAnswer: string | null;
  liveItems: LiveItem[];
  docs: DocumentItem[];
  showThinking: boolean;
  /** The sheet is a scored topic the candidate is still reading: a move for
   * another thread is written behind it, not turned to. */
  held: boolean;
}): {
  question: ReactNode;
  main: ReactNode[];
  margin: ReactNode[];
  closing: Thread | null;
  topicIndex: number;
} {
  const main: ReactNode[] = [];
  const margin: ReactNode[] = [];

  const currentIndex = openThread?.thread_index ?? -1;
  const utterances: Utterance[] = [];
  if (openThread) {
    for (const m of openThread.messages) {
      utterances.push({ key: m.id, role: m.role, kind: m.kind ?? "question", text: m.text });
    }
  }
  if (pendingAnswer !== null) {
    utterances.push({ key: "pending", role: "candidate", kind: "question", text: pendingAnswer });
  }
  let evalItem: LiveEval | null = null;
  let nextQuestion: LiveMove | null = null;
  for (const item of liveItems) {
    if (item.type === "move") {
      if (item.threadIndex === currentIndex || (currentIndex === -1 && item.kind !== "question")) {
        utterances.push({ key: item.key, role: "interviewer", kind: item.kind, text: item.text, typing: true });
      } else if (!held) {
        nextQuestion = item;
      }
    } else {
      evalItem = item;
    }
  }

  if (nextQuestion) {
    const closing: Thread | null = openThread
      ? {
          ...openThread,
          status: "closed",
          score: evalItem?.score ?? openThread.score ?? null,
          feedback: evalItem?.feedback || openThread.feedback || null,
          model_answer: evalItem?.modelAnswer || openThread.model_answer || null,
          messages: [
            ...openThread.messages,
            // What this round-trip added to the topic and the refetch has not
            // persisted yet: the answer just filed and any move streamed for it.
            ...utterances
              .filter((u) => u.key === "pending" || u.typing)
              .map((u, i) => ({
                id: u.key,
                thread_id: openThread.id,
                seq: openThread.messages.length + i,
                role: u.role,
                kind: u.role === "candidate" ? null : u.kind,
                text: u.text,
                created_at: openThread.created_at,
              })),
          ],
        }
      : null;
    const question = (
      <div className="box q sc-question" key={nextQuestion.key} style={{ viewTransitionName: "question" }}>
        <span className="lbl">{nextQuestion.threadIndex + 1}. Question</span>
        <p>
          {nextQuestion.text}
          <span className="cursor-blink" />
        </p>
      </div>
    );
    return { question, main, margin, closing, topicIndex: nextQuestion.threadIndex };
  }

  // The question box + the margin's agenda and sources.
  const q = utterances.find((u) => u.role === "interviewer" && u.kind === "question");
  const question =
    q && openThread ? (
      <div className="box q sc-question" key={q.key} style={{ viewTransitionName: "question" }}>
        <span className="lbl">{openThread.thread_index + 1}. Question</span>
        <p>
          {q.text}
          {q.typing ? <span className="cursor-blink" /> : null}
        </p>
      </div>
    ) : null;

  // The interviewer's latest note lives in the margin; earlier ones are
  // written inline on the form, between the responses they answered.
  const rest = utterances.filter((u) => u !== q);
  const last = rest.at(-1);
  const latestNote = last && last.role === "interviewer" ? last : null;
  for (const u of rest) {
    if (u === latestNote) continue;
    if (u.role === "candidate") {
      // The just-submitted answer carries the reply box's name while the
      // interviewer is still thinking, so the box visibly files itself.
      const filing = u.key === "pending" && showThinking;
      main.push(
        <div
          key={u.key}
          className="box r"
          style={filing ? { viewTransitionName: "composer" } : undefined}
        >
          <span className="lbl">Candidate response</span>
          <p className="typed">{u.text}</p>
        </div>,
      );
    } else {
      main.push(<Remark key={u.key} kind={u.kind} text={u.text} />);
    }
  }

  if (evalItem) {
    main.push(<Assessment key={evalItem.key} item={evalItem} />);
  } else if (held && openThread && openThread.status === "closed") {
    // The refetch has filed the topic; its assessment stays on the sheet
    // as filed until the page turns.
    main.push(
      <Assessment
        key="filed"
        still
        item={{
          type: "eval",
          key: "filed",
          threadIndex: openThread.thread_index,
          score: openThread.score ?? null,
          feedback: openThread.feedback ?? "",
          modelAnswer: openThread.model_answer ?? "",
          phase: "done",
        }}
      />,
    );
  }

  if (showThinking) {
    main.push(
      <div key="thinking" className="practice-loading">
        <span className="pen-dot" aria-hidden="true" />
        <LoadingStatus
          active
          messages={["Reading your answer", "Deciding the sharpest next move", "Grounding it in your profile"]}
          fallback="Thinking"
        />
      </div>,
    );
  }

  if (latestNote) {
    margin.push(
      <PenNote key={latestNote.key} kind={latestNote.kind} text={latestNote.text} typing={latestNote.typing} />,
    );
  }
  if (openThread) {
    margin.push(<TopicMargin key="agenda" thread={openThread} docs={docs} />);
  }

  return { question, main, margin, closing: null, topicIndex: Math.max(currentIndex, 0) };
}

/** An earlier interviewer move, written on the form between two responses. */
function Remark({ kind, text }: { kind: MoveKind; text: string }) {
  return (
    <div className="remark">
      <span className="k">{moveLabels[kind]}</span>
      <p>{text}</p>
    </div>
  );
}

function docLabel(d: DocumentItem) {
  return d.kind === "github_repo" ? (d.project_title ?? d.filename) : d.filename;
}

/** Agenda (the thread's anchors) and sources (its focus documents), in the margin. */
function TopicMargin({ thread, docs }: { thread: Thread; docs: DocumentItem[] }) {
  const anchors = thread.anchors_json ?? [];
  const ids = new Set(thread.focus_document_ids ?? []);
  const sources = docs.filter((d) => ids.has(d.id));
  if (anchors.length === 0 && sources.length === 0) return null;
  return (
    <>
      {anchors.length > 0 ? (
        <div className="note static">
          <div className="k">Agenda</div>
          <ul className="checklist pen" style={{ marginTop: 6 }}>
            {anchors.map((a) => (
              <li key={a}>
                <i aria-hidden="true" />
                {a}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {sources.length > 0 ? (
        <div className="sources">
          <span className="k">Grounded in</span>
          <ul>
            {sources.map((d) => (
              <li key={d.id}>{docLabel(d)}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </>
  );
}

function Assessment({ item, still }: { item: LiveEval; still?: boolean }) {
  const { score, feedback, modelAnswer, phase } = item;
  return (
    <div
      className={still ? "box assessment" : "box assessment stream-in"}
      aria-live="polite"
      style={{ viewTransitionName: `topic-${item.threadIndex}` }}
    >
      <span className="lbl pen-lbl">Interviewer's assessment</span>
      <div className="rating-row">
        <span className="cap">Rating</span>
        <RatingCells score={score} scoring={score === null} />
        {score === null ? (
          <span className="practice-loading subtle">
            <span className="pen-dot" aria-hidden="true" />
            <LoadingStatus
              active
              messages={["Scoring your structure", "Checking evidence and specificity", "Drafting feedback"]}
              fallback="Scoring this topic"
            />
          </span>
        ) : null}
      </div>
      {feedback ? (
        <p className="feedback">
          {feedback}
          {phase === "feedback" ? <span className="cursor-blink" /> : null}
        </p>
      ) : null}
      {phase === "model_answer" || modelAnswer ? (
        <details open className="model-answer">
          <summary>
              <ChevronRight aria-hidden="true" />
              Model answer
            </summary>
          <p>
            {modelAnswer}
            {phase === "model_answer" ? <span className="cursor-blink" /> : null}
          </p>
        </details>
      ) : phase === "feedback" && score !== null ? (
        <div className="practice-loading subtle">
          <span className="pen-dot" aria-hidden="true" />
          <LoadingStatus
            active
            messages={["Preparing model answer", "Tuning it to the role", "Making the example sharper"]}
            fallback="Preparing model answer"
          />
        </div>
      ) : null}
    </div>
  );
}

/** A closed topic: one row of the packet index, expandable to the full exchange. */
function PrevTopic({ thread, docs }: { thread: Thread; docs: DocumentItem[] }) {
  const num = thread.thread_index + 1;
  const ids = new Set(thread.focus_document_ids ?? []);
  const sources = docs.filter((d) => ids.has(d.id));
  return (
    <details className="prev-row" style={{ viewTransitionName: `topic-${thread.thread_index}` }}>
      <summary>
        <span className="t">Topic {num}</span>
        <span className="focus">{topicLabel(thread.focus_label) ?? "Untitled topic"}</span>
        <RatingCells score={thread.score} mini />
      </summary>
      <div className="prev-body">
        {thread.messages.map((m) => {
          const isQ = m.role === "interviewer" && (m.kind ?? "question") === "question";
          const isFollowUp = m.role === "interviewer" && !isQ;
          return (
            <div key={m.id} className="exchange">
              <span className={`who${isFollowUp ? " pen" : ""}`}>
                {m.role === "candidate" ? "You" : isQ ? `Q${num}` : moveLabels[m.kind ?? "question"]}
              </span>
              <p className={m.role === "candidate" ? "typed" : isFollowUp ? "pen" : ""}>{m.text}</p>
            </div>
          );
        })}
        {thread.score !== null && thread.score !== undefined ? (
          <div className="exchange">
            <span className="who pen">Rated {thread.score}/10</span>
            <div>
              {thread.feedback ? <p>{thread.feedback}</p> : null}
              {thread.model_answer ? (
                <details className="model-answer">
                  <summary>
              <ChevronRight aria-hidden="true" />
              Model answer
            </summary>
                  <p>{thread.model_answer}</p>
                </details>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="exchange">
            <span className="who">Rating</span>
            <p className="muted">No evaluation recorded.</p>
          </div>
        )}
        {sources.length > 0 ? (
          <div className="exchange">
            <span className="who">Grounded in</span>
            <p className="muted">{sources.map(docLabel).join(" · ")}</p>
          </div>
        ) : null}
      </div>
    </details>
  );
}

// --- helpers ----------------------------------------------------------------

function scoreThreads(threads: Thread[] | undefined): number | null {
  const scored = threads?.filter((t) => t.score !== null && t.score !== undefined) ?? [];
  if (!scored.length) return null;
  return scored.reduce((total, t) => total + (t.score ?? 0), 0) / scored.length;
}

