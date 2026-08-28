import { useState, type CSSProperties, type ReactNode } from "react";
import { Moon, Sun } from "lucide-react";

import { MoveKind } from "../api";
import { codeFrom, translate } from "../errors";
import { moveLabels } from "../jobLabel";
import { ActiveJobChip } from "./ActiveJobChip";

export function StatusPill({
  tone,
  children,
}: {
  tone: "neutral" | "good" | "warn" | "bad" | "info";
  children: ReactNode;
}) {
  return <span className={`status-pill status-${tone}`}>{children}</span>;
}

/**
 * Translated-error renderer. Pass either a backend code/ApiError/SSE
 * error frame via `error`, or a raw `code` string when you already have
 * it. Returns null when there's no error so callers can render
 * `<ErrorBanner error={maybeErr} />` unconditionally.
 */
export function ErrorBanner({
  error,
  code,
}: {
  error?: unknown;
  code?: string | null;
}) {
  if (!error && !code) {
    return null;
  }
  const resolved = code ?? codeFrom(error);
  const { message, hint } = translate(resolved);
  return (
    <div className="error-banner" role="alert">
      <strong>{message}</strong>
      {hint ? <span className="error-banner-hint">{hint}</span> : null}
    </div>
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <p>{body}</p>
    </div>
  );
}

/** The form's title rule plus its row of underlined fields. */
export function SheetHead({
  title,
  page,
  children,
}: {
  title: string;
  page?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <>
      <header className="sheet-head">
        <h1>{title}</h1>
        {page ? <span className="page">{page}</span> : null}
      </header>
      {children ? <div className="fields">{children}</div> : null}
    </>
  );
}

/** One underlined form field: caps label over a value. */
export function Field({
  label,
  value,
  empty = "-",
  title,
  wrap,
}: {
  label: string;
  value?: ReactNode;
  empty?: string;
  title?: string;
  /** Let a long value run to a second line instead of truncating. */
  wrap?: boolean;
}) {
  const isEmpty = value === null || value === undefined || value === "";
  return (
    <div className="f">
      <span className="cap">{label}</span>
      <span className={`v${isEmpty ? " empty" : ""}${wrap ? " wrap" : ""}`} title={title}>
        {isEmpty ? empty : value}
      </span>
    </div>
  );
}

/** The active job as a form field with the switcher slip underneath. */
export function JobField() {
  return (
    <div className="f wide">
      <span className="cap">Role / company</span>
      <ActiveJobChip />
    </div>
  );
}

/** 1-10 rating cells; the scored one is highlighter-filled. While `scoring`,
 * the pen runs along the row (each cell's `--i` staggers the sweep). */
export function RatingCells({
  score,
  mini,
  label,
  scoring,
}: {
  score: number | null | undefined;
  mini?: boolean;
  label?: string;
  scoring?: boolean;
}) {
  const unscored = score === null || score === undefined;
  return (
    <div
      className={`cells${mini ? " mini" : ""}${scoring ? " scoring" : ""}`}
      role="img"
      aria-label={label ?? (scoring ? "Scoring" : unscored ? "Not scored yet" : `Scored ${score} out of 10`)}
    >
      {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
        <span
          key={n}
          className={`cell${score === n ? " on" : ""}`}
          aria-hidden="true"
          style={scoring ? ({ "--i": n - 1 } as CSSProperties) : undefined}
        >
          {n}
        </span>
      ))}
    </div>
  );
}

/** Paper stock switch: light (off-white on charcoal) / dark (slate night). */
export function StockToggle() {
  const [stock, setStock] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  const next = stock === "dark" ? "light" : "dark";
  const toggle = () => {
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("stock", next);
    } catch {
      /* private mode: the choice just doesn't persist */
    }
    setStock(next);
  };
  return (
    <button
      type="button"
      className="desk-btn"
      onClick={toggle}
      aria-label={`Switch to ${next === "dark" ? "night" : "day"} paper`}
      title={`Switch to ${next === "dark" ? "night" : "day"} paper`}
    >
      {stock === "dark" ? <Sun /> : <Moon />}
      <span>{stock === "dark" ? "Day" : "Night"}</span>
    </button>
  );
}

/** The interviewer's note in red pen: a caps key behind a hand-drawn arrow,
 * then the words; `typing` blinks a caret while the text is still arriving. */
export function PenNote({ kind, text, typing }: { kind: MoveKind; text: string; typing?: boolean }) {
  return (
    <div className={`note${typing ? " stream-in" : ""}`} aria-live={typing ? "polite" : undefined}>
      <div className="k">
        <svg viewBox="0 0 34 18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M32 9C24 9 16 4 3 9" pathLength={1} />
          <path d="M8 5 3 9l5 4" pathLength={1} />
        </svg>
        {moveLabels[kind]}
      </div>
      <p>
        {text}
        {typing ? <span className="cursor-blink" /> : null}
      </p>
    </div>
  );
}

export function Staples() {
  return (
    <>
      <i className="staple a" aria-hidden="true" />
      <i className="staple b" aria-hidden="true" />
    </>
  );
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

/** "28 Aug" - a date as the form would pencil it. */
export function shortDate(value: string) {
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function shortId(id: string) {
  return `${id.slice(0, 8)}...`;
}
