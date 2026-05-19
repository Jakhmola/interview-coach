const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

export class ApiError extends Error {
  status: number;
  detail: string;
  /** Phase 22: when the backend returns a structured 409 body
   * (currently only ``{code, blocking_session_ids}`` from delete
   * routes), the parsed object lands here. ``detail`` keeps the
   * stringified form so existing string-keyed lookups still work. */
  detailObject: Record<string, unknown> | null;

  constructor(status: number, detail: string, detailObject: Record<string, unknown> | null = null) {
    super(`${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
    this.detailObject = detailObject;
  }
}

/**
 * Raised on any 401. A top-level handler in App.tsx listens for these,
 * clears auth state, and routes to /login. Subclass of ApiError so
 * existing `instanceof ApiError` catches still work.
 */
export class AuthExpiredError extends ApiError {
  constructor() {
    super(401, "auth_expired");
  }
}

export type User = {
  id: string;
  email: string;
  created_at?: string;
};

export type AuthResponse = {
  access_token: string;
  user: User;
};

export type EmbeddingStatus = "ready" | "pending" | "failed" | "n_a";

export type DocumentItem = {
  id: string;
  user_id: string;
  kind: "cv" | "project_doc";
  filename: string;
  content_type: string;
  byte_size: number;
  char_count: number;
  created_at: string;
  project_title?: string | null;
  embedding_status?: EmbeddingStatus;
};

export type DocumentDetail = DocumentItem & {
  raw_text: string;
  parsed_json?: Record<string, unknown> | null;
};

export type DocIntakeSuggestion = {
  mapping_kind: "highlight" | "experience" | "project";
  experience_idx?: number | null;
  highlight_idx?: number | null;
  confidence: number;
  reason: string;
};

export type DocIntakeExtracted = {
  tech_stack: string[];
  description?: string | null;
  urls: string[];
};

export type ProfileHighlight = {
  highlight_idx: number;
  text: string;
};

export type ProfileExperience = {
  experience_idx: number;
  company: string;
  role: string;
  highlights: ProfileHighlight[];
};

/** Payload of a ``mapping_suggestion`` SSE event from /sessions/prepare.
 * The FE renders this inline in Stage 4 of the wizard and POSTs the
 * user's decision to /sessions/prepare/resume. */
export type MappingSuggestion = {
  document_id: string;
  title: string;
  preview: string;
  extracted: DocIntakeExtracted;
  suggestions: DocIntakeSuggestion[];
  experiences: ProfileExperience[];
  /** How many unmapped docs remain in this prep run, including this one. */
  remaining: number;
};

export type MappingRow = {
  mapping_kind: "highlight" | "experience" | "project";
  experience_idx?: number | null;
  highlight_idx?: number | null;
  project_idx?: number | null;
};

export type JobItem = {
  id: string;
  user_id: string;
  source: "pasted" | "url";
  source_url?: string | null;
  char_count: number;
  preview: string;
  created_at: string;
  /** Phase 22: surfaced on the list endpoint so Manage + the active-job
   * dropdown can render "role @ company" instead of a generic
   * "Pasted JD" label. Null until ``job_analyzer`` has run for this
   * row (i.e. ``/prepare`` has been kicked off and reached at least
   * the analyzer node). */
  parsed_json?: Record<string, unknown> | null;
};

export type JobDetail = JobItem & {
  raw_text: string;
  parsed_json?: Record<string, unknown> | null;
};

export type PrepStatus = {
  job_id: string;
  has_cv: boolean;
  profile_ready: boolean;
  job_analyzed: boolean;
  company_researched: boolean;
  can_start: boolean;
  missing: string[];
  /** Phase 22: project_docs the user uploaded but hasn't mapped yet.
   * The wizard's work-driven auto-prep treats a non-zero count as
   * "needs prep" without waiting for a manual Continue click. */
  unmapped_project_doc_count: number;
  profile?: Record<string, unknown> | null;
  job?: Record<string, unknown> | null;
  company?: {
    company_name: string;
    snapshot: Record<string, unknown>;
    source_urls: string[];
    updated_at: string;
  } | null;
};

export type RoundType = "resume_walkthrough" | "behavioral_star";
export type SessionStatus = "active" | "complete" | "abandoned";

export type Session = {
  id: string;
  user_id: string;
  job_id: string;
  round_type: RoundType;
  status: SessionStatus;
  n_questions: number;
  created_at: string;
};

export type Turn = {
  id: string;
  session_id: string;
  turn_index: number;
  question: string;
  anchors_json: string[];
  answer?: string | null;
  score?: number | null;
  feedback?: string | null;
  model_answer?: string | null;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
};

export type SessionDetail = Session & {
  turns: Turn[];
};

export type SseFrame = {
  event: string;
  data: unknown;
};

type FetchInit = RequestInit & { token?: string | null };

async function unwrap<T>(response: Response): Promise<T> {
  if (response.ok) {
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  if (response.status === 401) {
    // Fire a global event so App-level can route to /login without each
    // call site needing to know about it. Page-level catches still get
    // the typed AuthExpiredError to handle cleanup.
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("auth-expired"));
    }
    throw new AuthExpiredError();
  }

  const text = await response.text();
  let detail = text;
  let detailObject: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(text) as { detail?: unknown };
    if (typeof parsed.detail === "string") {
      detail = parsed.detail;
    } else if (parsed.detail !== null && typeof parsed.detail === "object") {
      // Phase 22: structured 409 body. Surface the `code` field as the
      // string detail so existing ``codeFrom`` consumers still match
      // the right ``ERRORS`` entry, but keep the full object so
      // callers that need the metadata (e.g. blocking_session_ids)
      // can reach into ``detailObject``.
      detailObject = parsed.detail as Record<string, unknown>;
      const code = (detailObject as { code?: unknown }).code;
      detail = typeof code === "string" ? code : JSON.stringify(parsed.detail);
    } else {
      detail = JSON.stringify(parsed.detail);
    }
  } catch {
    detail = text || response.statusText;
  }
  throw new ApiError(response.status, detail, detailObject);
}

async function apiFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.token) {
    headers.set("Authorization", `Bearer ${init.token}`);
  }
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  return unwrap<T>(response);
}

export const api = {
  healthz: () => apiFetch<{ status: string; version: string }>("/healthz"),
  register: (email: string, password: string) =>
    apiFetch<AuthResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  login: (email: string, password: string) =>
    apiFetch<AuthResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  me: (token: string) => apiFetch<User>("/auth/me", { token }),
  uploadDocument: (
    token: string,
    kind: DocumentItem["kind"],
    file: File,
  ) => {
    const body = new FormData();
    body.set("kind", kind);
    body.set("file", file);
    return apiFetch<DocumentDetail>("/documents", { method: "POST", token, body });
  },
  listDocuments: (token: string) => apiFetch<DocumentItem[]>("/documents", { token }),
  getDocument: (token: string, id: string) => apiFetch<DocumentDetail>(`/documents/${id}`, { token }),
  deleteDocument: (token: string, id: string) =>
    apiFetch<void>(`/documents/${id}`, { method: "DELETE", token }),
  /** Phase 22: re-schedule embedding for a CV (or already-mapped
   * project_doc). 202 Accepted; the FE polls listDocuments to discover
   * when the chunks land via ``embedding_status``. */
  retryEmbed: (token: string, id: string) =>
    apiFetch<void>(`/documents/${id}/embed`, { method: "POST", token }),
  /** Phase 22: kick off an out-of-graph remap for a project_doc. The
   * response payload is the same shape the prep_graph emits as
   * ``mapping_suggestion``, so MappingModal consumes one schema. */
  startRemap: (token: string, id: string) =>
    apiFetch<MappingSuggestion>(`/documents/${id}/remap`, { method: "POST", token }),
  /** Phase 22: finish an out-of-graph remap. ``apply`` mutates the
   * profile; ``skip`` leaves the doc unmapped (no DB writes). */
  confirmRemap: (
    token: string,
    id: string,
    body:
      | { action: "apply"; rows: MappingRow[]; title: string; extracted: DocIntakeExtracted }
      | { action: "skip" },
  ) =>
    apiFetch<DocumentDetail>(`/documents/${id}/remap/confirm`, {
      method: "POST",
      token,
      body: JSON.stringify(body),
    }),
  submitJobText: (token: string, text: string) =>
    apiFetch<JobDetail>("/jobs", { method: "POST", token, body: JSON.stringify({ text }) }),
  submitJobUrl: (token: string, url: string) =>
    apiFetch<JobDetail>("/jobs", { method: "POST", token, body: JSON.stringify({ url }) }),
  listJobs: (token: string) => apiFetch<JobItem[]>("/jobs", { token }),
  getJob: (token: string, id: string) => apiFetch<JobDetail>(`/jobs/${id}`, { token }),
  /** Phase 22: re-analyze JD. Replaces ``raw_text``, clears parsed
   * analysis + company snapshot. Next ``/prepare`` re-runs both. */
  patchJob: (token: string, id: string, body: { text?: string; url?: string }) =>
    apiFetch<JobDetail>(`/jobs/${id}`, { method: "PATCH", token, body: JSON.stringify(body) }),
  deleteJob: (token: string, id: string) => apiFetch<void>(`/jobs/${id}`, { method: "DELETE", token }),
  prepStatus: (token: string, jobId: string) =>
    apiFetch<PrepStatus>(`/sessions/prepare/status?job_id=${encodeURIComponent(jobId)}`, { token }),
  createSession: (token: string, job_id: string, round_type: RoundType, n_questions: number) =>
    apiFetch<Session>("/sessions", {
      method: "POST",
      token,
      body: JSON.stringify({ job_id, round_type, n_questions }),
    }),
  listSessions: (token: string) => apiFetch<Session[]>("/sessions", { token }),
  getSession: (token: string, id: string) => apiFetch<SessionDetail>(`/sessions/${id}`, { token }),
  abandonSession: (token: string, id: string) =>
    apiFetch<Session>(`/sessions/${id}/abandon`, { method: "POST", token }),
  /** Phase 22: wipe everything the user owns; keep the user row + token.
   * ``confirm_email`` must equal the current user's email (case-insensitive). */
  resetAccount: (token: string, confirmEmail: string) =>
    apiFetch<void>("/auth/me/reset", {
      method: "POST",
      token,
      body: JSON.stringify({ confirm_email: confirmEmail }),
    }),
};

export function parseSseText(text: string): SseFrame[] {
  const frames: SseFrame[] = [];
  let event = "message";
  const dataLines: string[] = [];

  const flush = () => {
    if (dataLines.length === 0) {
      event = "message";
      return;
    }
    const payload = dataLines.join("\n");
    let data: unknown = payload;
    try {
      data = JSON.parse(payload);
    } catch {
      data = payload;
    }
    frames.push({ event, data });
    event = "message";
    dataLines.length = 0;
  };

  for (const line of text.split(/\r?\n/)) {
    if (line === "") {
      flush();
    } else if (line.startsWith(":")) {
      continue;
    } else if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  flush();
  return frames;
}

async function streamPost(
  path: string,
  token: string,
  body: unknown,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    // AbortError on the handshake — caller initiated cleanup. Don't surface.
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    // Other fetch failures (CORS, DNS, etc.) — surface as a stream-interrupted
    // event so the caller can render a translated message rather than blowing up.
    onFrame({ event: "error", data: { code: "stream_interrupted" } });
    return;
  }
  if (!response.ok) {
    await unwrap(response);
    return;
  }
  if (!response.body) {
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const splitAt = buffer.lastIndexOf("\n\n");
      if (splitAt === -1) {
        continue;
      }
      const ready = buffer.slice(0, splitAt + 2);
      buffer = buffer.slice(splitAt + 2);
      for (const frame of parseSseText(ready)) {
        onFrame(frame);
      }
    }
    buffer += decoder.decode();
    for (const frame of parseSseText(buffer)) {
      onFrame(frame);
    }
  } catch (err) {
    // Mid-stream disconnect or abort. AbortError → caller is unmounting,
    // stay silent. Anything else → surface as a recoverable error event.
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    onFrame({ event: "error", data: { code: "stream_interrupted" } });
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // already released
    }
  }
}

export function prepareSessionStream(
  token: string,
  jobId: string,
  forceRefresh: boolean,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
) {
  return streamPost(
    "/sessions/prepare",
    token,
    { job_id: jobId, force_refresh: forceRefresh },
    onFrame,
    signal,
  );
}

/** Resume prep_graph after the user confirms or skips a project_doc
 * mapping. The backend threads the body into the paused
 * ``await_mapping_confirm`` interrupt and the graph advances to the
 * next unmapped doc (or to job_analyzer if none remain). */
export function prepareSessionResumeStream(
  token: string,
  body: {
    job_id: string;
    action: "apply" | "skip";
    rows?: MappingRow[];
    title?: string;
    extracted?: DocIntakeExtracted;
  },
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
) {
  return streamPost("/sessions/prepare/resume", token, body, onFrame, signal);
}

export function nextQuestionStream(
  token: string,
  sessionId: string,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
) {
  return streamPost(`/sessions/${sessionId}/next_question`, token, {}, onFrame, signal);
}

export function answerStream(
  token: string,
  sessionId: string,
  answer: string,
  onFrame: (frame: SseFrame) => void,
  signal?: AbortSignal,
) {
  return streamPost(`/sessions/${sessionId}/answer`, token, { answer }, onFrame, signal);
}
