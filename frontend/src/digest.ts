/**
 * What a file is, read off its own text and off what prep made of it - the
 * lines Manage's index and reading pane show instead of filenames and
 * character counts.
 */

/** A `Profile` (agents/schemas.py) as it arrives: loosely typed, lowercased. */
export type ProfileLite = {
  summary?: unknown;
  skills?: unknown;
  experiences?: unknown;
  education?: unknown;
};

type ExperienceLite = {
  company?: unknown;
  role?: unknown;
  start?: unknown;
  end?: unknown;
  highlights?: unknown;
};

const isString = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter(isString) : []);
const records = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object") : [];

// The profile builder lowercases names. Short tokens that are initialisms in
// a CV get their capitals back; everything else is capitalised word by word.
// ponytail: a word list, not a dictionary - extend it when one bites.
const INITIALISMS = new Set([
  "ai", "ml", "ux", "ui", "qa", "hr", "ci", "cd", "iit", "aws", "gcp", "llm", "nlp", "api", "sre",
  "gpu", "cpu", "sql", "mit", "ibm", "sap", "ceo", "cto", "cfo", "llc", "inc", "ltd", "plc", "phd",
  "bsc", "msc", "mba", "rag", "ocr", "etl", "ios", "usa", "uk",
]);
const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "by", "de", "for", "in", "of", "on", "or", "the", "to"]);

/** "senior machine learning engineer, bobble ai" -> "Senior Machine Learning Engineer, Bobble AI". */
export function titleCase(s: string): string {
  return s.replace(/[A-Za-z][A-Za-z.'-]*/g, (word, offset: number) => {
    const lower = word.toLowerCase();
    if (INITIALISMS.has(lower)) return lower.toUpperCase();
    if (offset > 0 && SMALL_WORDS.has(lower)) return lower;
    return lower.charAt(0).toUpperCase() + lower.slice(1);
  });
}

const lines = (text: string): string[] =>
  text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean);

/** The first line of a text - a CV's name, a doc's title. */
export function firstLine(text: string): string {
  return lines(text)[0] ?? "";
}

/** The first sentence of a paragraph, capped. */
export function firstSentence(text: string, cap = 220): string {
  const t = text.trim();
  const m = t.match(/^.*?[.!?](?=\s|$)/);
  const s = (m ? m[0] : t).trim();
  return s.length > cap ? `${s.slice(0, cap - 1).trimEnd()}…` : s;
}

/** The first `n` sentences of a paragraph - a pitch cut to its lead. */
export function leadOf(text: string, n = 2): string {
  const t = text.trim();
  const m = t.match(new RegExp(`^(?:.*?[.!?](?=\\s|$)\\s*){1,${n}}`));
  return (m ? m[0] : t).trim();
}

/** How a text opens, after its title line: the first sentence of its first paragraph. */
export function openingOf(text: string, cap = 220): string {
  const rest = lines(text).slice(1);
  const para = rest.find((l) => l.length > 48) ?? rest[0] ?? "";
  return firstSentence(para, cap);
}

/** A document's headings: short lines after the title with no end punctuation, commas or digits. */
export function sectionsOf(text: string, max = 6): string[] {
  return lines(text)
    .slice(1)
    .filter((l) => l.length <= 48 && !/[.:;,!?]$/.test(l) && !/[,\d]/.test(l) && !/^[-*•]/.test(l))
    .filter((l) => l.split(/\s+/).length <= 6)
    .slice(0, max);
}

/** "2022 - present" from a loosely typed experience. */
function span(e: ExperienceLite): string | null {
  const start = isString(e.start) ? e.start : null;
  const end = isString(e.end) ? e.end : null;
  if (!start && !end) return null;
  return [start, end ?? "present"].filter(Boolean).join(" - ");
}

/** The roles in a profile, newest first as the CV lists them: "Role, Company (2022 - present)". */
export function rolesOf(profile: ProfileLite | null | undefined): string[] {
  return records(profile?.experiences).flatMap((e) => {
    const role = isString(e.role) ? titleCase(e.role) : null;
    const company = isString(e.company) ? titleCase(e.company) : null;
    if (!role && !company) return [];
    const when = span(e);
    return [`${[role, company].filter(Boolean).join(", ")}${when ? ` (${when})` : ""}`];
  });
}

/** The current role as one line: "Senior Machine Learning Engineer · Bobble AI". */
export function currentRoleOf(profile: ProfileLite | null | undefined): string | null {
  const e = records(profile?.experiences)[0];
  if (!e) return null;
  const bits = [isString(e.role) ? titleCase(e.role) : null, isString(e.company) ? titleCase(e.company) : null];
  return bits.filter(Boolean).join(" · ") || null;
}

/** "M.tech Computer Science, IIT Madras, 2019" per education row. */
export function educationOf(profile: ProfileLite | null | undefined): string[] {
  return records(profile?.education).flatMap((e) => {
    const degree = isString(e.degree) ? titleCase(e.degree) : null;
    const school = isString(e.school) ? titleCase(e.school) : null;
    const end = isString(e.end) ? e.end : null;
    const line = [degree, school, end].filter(Boolean).join(", ");
    return line ? [line] : [];
  });
}

export function skillsOf(profile: ProfileLite | null | undefined): string[] {
  return strings(profile?.skills);
}

export function summaryOf(profile: ProfileLite | null | undefined): string | null {
  return isString(profile?.summary) ? profile.summary.trim() : null;
}

/** The company whose CV highlight a supporting doc was filed under, if any. */
export function filedUnder(profile: ProfileLite | null | undefined, docId: string): string | null {
  for (const e of records(profile?.experiences)) {
    for (const h of records(e.highlights)) {
      if (strings(h.source_document_ids).includes(docId)) {
        return isString(e.company) ? titleCase(e.company) : null;
      }
    }
  }
  return null;
}
