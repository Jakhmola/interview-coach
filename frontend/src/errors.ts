/**
 * Single source of truth for translating backend error codes into
 * user-facing messages. Every `<ErrorBanner>` and `show_error(...)` call
 * site should route through `translate()` so users never see raw codes
 * like `ProfileMissing` or `company_snapshot_missing`.
 *
 * Unknown codes fall through to a generic message that still embeds the
 * code so server logs stay greppable.
 */

import { ApiError } from "./api";

type ErrorEntry = { message: string; hint?: string };

export const ERRORS: Record<string, ErrorEntry> = {
  // Prep / readiness
  profile_missing: {
    message: "Your profile isn't built yet.",
    hint: "Upload your CV and run 'Process setup'.",
  },
  ProfileMissing: {
    message: "Your profile is still being built.",
    hint: "This can take up to a minute or two after CV upload.",
  },
  "No profile yet": {
    message: "Your profile is still being built.",
    hint: "This can take up to a minute or two after CV upload.",
  },
  "upload your CV and build a profile before adding project docs": {
    message: "Your profile is still being built.",
    hint: "This can take up to a minute or two after CV upload — the mapping will open as soon as it's ready.",
  },
  "apply_mapping needs a profile; upload your CV first": {
    message: "Your profile is still being built.",
    hint: "Wait a moment, then try saving the mapping again.",
  },
  job_not_analyzed: {
    message: "This JD hasn't been analyzed yet.",
    hint: "Open Setup and click 'Process setup' for this JD.",
  },
  company_snapshot_missing: {
    message: "Company info hasn't been researched yet.",
    hint: "Click 'Refresh company info' on the JD.",
  },
  no_documents: { message: "Upload a CV first." },
  NoDocumentsError: { message: "Upload a CV first." },
  CompanyNameMissing: {
    message: "We couldn't extract the company name from the JD.",
    hint: "Open Manage → Re-analyze JD to paste a clearer version, or replace the JD entirely.",
  },
  NoSearchHits: {
    message: "No public info found about this company.",
    hint: "Try 'Refresh company info'; if it still fails, the JD's company name may be off — Re-analyze from Manage.",
  },
  NoUsablePages: {
    message: "Found search results but couldn't read them.",
    hint: "Click 'Refresh company info' to retry.",
  },

  // Sessions
  session_not_found: {
    message: "We couldn't find that session — it may have been deleted.",
  },
  session_status_abandoned: {
    message: "This session was ended.",
    hint: "Start a new round from the Interview page.",
  },
  session_status_complete: {
    message: "This session is already complete.",
  },
  no_active_turn: { message: "Generate a question first." },
  empty_answer: { message: "Type something before submitting." },
  max_turns_reached: { message: "You've answered all the questions in this session." },
  previous_turn_unanswered: {
    message: "Finish the current question before asking another.",
  },

  // Documents / jobs
  job_not_found: { message: "We couldn't find that JD — it may have been deleted." },
  "Document not found": {
    message: "We couldn't find that document — it may have been deleted.",
  },
  "Job not found": { message: "We couldn't find that JD — it may have been deleted." },
  job_in_use: {
    message: "This JD has an active session.",
    hint: "End the session before deleting.",
  },
  cv_in_use: {
    message: "You have an active session.",
    hint: "End it before replacing your CV.",
  },
  "rebuild only applies to CV": {
    message: "Only CVs can have their profile rebuilt.",
  },

  // Streaming / auth
  stream_interrupted: {
    message: "Connection dropped mid-response.",
    hint: "Try again — your progress is saved.",
  },
  auth_expired: { message: "Your session expired. Please log in again." },
};

export function translate(code: string): ErrorEntry {
  if (code in ERRORS) {
    return ERRORS[code];
  }
  return { message: `Something went wrong. (code: ${code})` };
}

/**
 * Pulls a code string from anything an error path might hand us:
 * an `ApiError` (uses `.detail`), an SSE error frame object (looks for
 * `code` then `detail`), or a raw string.
 */
export function codeFrom(err: unknown): string {
  if (err instanceof ApiError) {
    return err.detail;
  }
  if (typeof err === "string") {
    return err;
  }
  if (typeof err === "object" && err !== null) {
    const payload = err as { code?: unknown; detail?: unknown };
    if (typeof payload.code === "string") {
      return payload.code;
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
  }
  return "unknown_error";
}
