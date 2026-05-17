import type { ReactNode } from "react";

import { codeFrom, translate } from "../errors";

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

export function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function shortId(id: string) {
  return `${id.slice(0, 8)}...`;
}
