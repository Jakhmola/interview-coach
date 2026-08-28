/**
 * Phase 22 - single helper for the JD display label.
 *
 * The wizard, Manage list, ActiveJobChip dropdown, and ReadyLanding
 * all need to render the same thing for a given job, otherwise the
 * dropdown selection looks like a no-op (every row says "Pasted JD"
 * with no way to tell them apart). One helper, one truth.
 *
 * Preference order:
 *   1. ``role @ company``  - both extracted by ``job_analyzer``.
 *   2. ``role``           - analyzer got a title but no company name.
 *   3. ``company``        - analyzer got a company but no title.
 *   4. ``source_url`` minus scheme - for URL-pulled JDs pre-analysis.
 *   5. first ~80 chars of the text preview, in quotes.
 *   6. ``"Pasted JD"`` - last-resort fallback.
 *
 * ``parsed_json`` arrives as ``unknown`` because the backend is loose
 * about the shape on the wire; we read role/company defensively.
 */

import { JobItem } from "./api";

const URL_LABEL_CHARS = 70;
const TEXT_LABEL_CHARS = 80;

type Parsed = { title?: string | null; company_name?: string | null };

function parsedOf(job: { parsed_json?: Record<string, unknown> | null }): Parsed {
  const p = job.parsed_json as Parsed | null | undefined;
  return p ?? {};
}

export function jobLabel(job: JobItem): string {
  const { title, company_name } = parsedOf(job);
  const role = typeof title === "string" ? title.trim() : "";
  const company = typeof company_name === "string" ? company_name.trim() : "";
  if (role && company) return `${role} @ ${company}`;
  if (role) return role;
  if (company) return company;
  if (job.source_url) return job.source_url.replace(/^https?:\/\//, "").slice(0, URL_LABEL_CHARS);
  if (job.preview) {
    const snippet = job.preview.replace(/\s+/g, " ").trim().slice(0, TEXT_LABEL_CHARS);
    if (snippet) return `"${snippet}…"`;
  }
  return "Pasted JD";
}

export function jobSubtitle(job: JobItem): string | null {
  const { title, company_name } = parsedOf(job);
  const role = typeof title === "string" ? title.trim() : "";
  const company = typeof company_name === "string" ? company_name.trim() : "";
  // If the primary label is role@company, the subtitle shows the source
  // hint (URL host or "pasted"). If the primary label fell back to the
  // URL/preview, there's nothing useful to repeat.
  if (!(role || company)) return null;
  if (job.source_url) return job.source_url.replace(/^https?:\/\//, "");
  return "pasted";
}

/** A thread's focus label as form copy: CV-highlight labels arrive wrapped in quotes. */
export function topicLabel(label: string | null | undefined): string | undefined {
  const t = (label ?? "").trim().replace(/^["\u201c]+|["\u201d]+$/g, "").trim();
  return t || undefined;
}

/**
 * The scorecard's TOPIC field: the highlight's headline clause, the way an
 * interviewer would write the topic on the form ("Built an LLM-powered
 * conversational agent with retrieval over product docs"), not the whole
 * CV bullet with its metrics. Lists and History keep the full label.
 */
export function topicTitle(label: string | null | undefined): string | undefined {
  const full = topicLabel(label);
  if (!full) return undefined;
  const clause = full.split(/[;:]|\s[-\u2013\u2014]\s/)[0].trim();
  return clause || full;
}
