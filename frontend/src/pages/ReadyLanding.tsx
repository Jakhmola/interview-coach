import { type ReactNode } from "react";
import { ArrowRight } from "lucide-react";

import { DocumentItem, JobItem, PrepStatus } from "../api";
import { Field, JobField, SheetHead } from "../components/ui";
import { useAuth } from "../state/auth";

/**
 * The packet's cover, shown on Setup once prep is complete: the one action,
 * what is on file in a line (Manage owns the inventory), then the brief the
 * interviewer works from - the role as the JD analysis read it, the company
 * notes from research, the candidate as the profile builder read the CV.
 * Interim: the owner has asked for something far more concise here; the
 * replacement is being chosen from .impeccable/mocks/landing/decision.html.
 */
export function ReadyLanding({
  status,
  job,
  cv,
  techDocs,
  repos,
  onStart,
  onManage,
}: {
  status: PrepStatus | null;
  job: JobItem;
  cv: DocumentItem | undefined;
  techDocs: DocumentItem[];
  repos: DocumentItem[];
  onStart: () => void;
  onManage: () => void;
}) {
  const { user } = useAuth();

  // The payloads arrive loosely typed; read what the schemas promise and skip
  // the rest (agents/schemas.py: JobAnalysis, CompanySnapshot, Profile).
  const brief = (status?.job ?? job.parsed_json ?? {}) as {
    seniority?: unknown;
    must_have_skills?: unknown;
    nice_to_have_skills?: unknown;
    responsibilities?: unknown;
    behavioral_signals?: unknown;
  };
  const company = status?.company ?? null;
  const snapshot = (company?.snapshot ?? {}) as {
    mission?: unknown;
    products?: unknown;
    recent_news?: unknown;
    values_and_signals?: unknown;
  };
  const profile = (status?.profile ?? null) as {
    summary?: unknown;
    skills?: unknown;
    experiences?: unknown;
    projects?: unknown;
  } | null;

  const seniority = typeof brief.seniority === "string" && brief.seniority !== "unknown" ? brief.seniority : null;
  const experiences = asObjects(profile?.experiences).map((e) =>
    [
      [e.role, e.company].filter(isString).join(" · "),
      [e.start, e.end].filter(isString).join(" - "),
    ]
      .filter(Boolean)
      .join(", "),
  );
  const projects = asObjects(profile?.projects)
    .map((p) => p.name)
    .filter(isString);

  const onFile = [
    cv ? cv.filename : "no CV",
    count(techDocs.length, "supporting doc"),
    count(repos.length, "repo"),
  ].join(" · ");

  return (
    <div className="wizard">
      <SheetHead title="Candidate intake" page="Complete · ready to practice">
        <Field label="Candidate" value={user?.email} />
        <JobField />
      </SheetHead>

      <div className="ready-actions">
        <button className="btn-primary" type="button" onClick={onStart}>
          Start a practice round <ArrowRight size={14} />
        </button>
        <span className="ready-onfile">
          <span>On file: {onFile}</span>
          <button type="button" className="btn-quiet" onClick={onManage}>
            Manage
          </button>
        </span>
      </div>

      <div className="brief">
        <section className="section" aria-label="Role brief">
          <h2>Role brief</h2>
          {seniority ? <BriefRow k="Seniority">{capitalise(seniority)}</BriefRow> : null}
          {/* Requirements come back as sentences, not skill names, so they
              read as lines; the duties as an unticked checklist. */}
          <Lines k="Must have" items={strings(brief.must_have_skills).slice(0, 6)} />
          <Lines k="Nice to have" items={strings(brief.nice_to_have_skills).slice(0, 4)} />
          <Lines k="Duties" items={strings(brief.responsibilities).slice(0, 5)} boxes />
          <Chips k="Signals" items={strings(brief.behavioral_signals)} />
        </section>

        <section className="section" aria-label="Company notes">
          <h2>Company notes</h2>
          {company ? (
            <>
              {isString(snapshot.mission) ? <p className="brief-lede clamp">{snapshot.mission}</p> : null}
              <Chips k="Products" items={strings(snapshot.products)} />
              <Lines k="Recently" items={strings(snapshot.recent_news).slice(0, 3)} />
              <Chips k="Values" items={strings(snapshot.values_and_signals)} />
              <span className="hint">
                {count(company.source_urls.length, "source")} · {shortDate(company.updated_at)}
              </span>
            </>
          ) : (
            <p className="muted">
              Research came up empty, so questions will be less company-specific. Re-analyze the JD
              from Manage to retry.
            </p>
          )}
        </section>

        <section className="section" aria-label="Candidate profile">
          <h2>Candidate profile</h2>
          {profile ? (
            <>
              {isString(profile.summary) ? <p className="brief-lede clamp">{profile.summary}</p> : null}
              <Chips k="Skills" items={strings(profile.skills)} max={14} />
              <Lines k="Experience" items={experiences.slice(0, 5)} />
              <Lines k="Projects" items={projects.slice(0, 5)} />
            </>
          ) : (
            <p className="muted">Profile not built yet.</p>
          )}
        </section>
      </div>

    </div>
  );
}

/** One line of a brief: a caps key and whatever it names. */
function BriefRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="brief-row">
      <span className="k">{k}</span>
      <div>{children}</div>
    </div>
  );
}

function Chips({ k, items, max = 10 }: { k: string; items: string[]; max?: number }) {
  if (items.length === 0) return null;
  const shown = items.slice(0, max);
  return (
    <BriefRow k={k}>
      <div className="chips">
        {shown.map((t) => (
          <span key={t} className="chip">
            {t}
          </span>
        ))}
        {items.length > shown.length ? <span className="hint">+{items.length - shown.length} more</span> : null}
      </div>
    </BriefRow>
  );
}

function Lines({ k, items, boxes }: { k: string; items: string[]; boxes?: boolean }) {
  if (items.length === 0) return null;
  return (
    <BriefRow k={k}>
      <ul className={boxes ? "checklist" : "lines"}>
        {items.map((t) => (
          <li key={t}>
            {boxes ? <i aria-hidden="true" /> : null}
            {t}
          </li>
        ))}
      </ul>
    </BriefRow>
  );
}

const isString = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter(isString) : []);
const asObjects = (v: unknown): Record<string, unknown>[] =>
  Array.isArray(v) ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object") : [];

function count(n: number, noun: string) {
  return n === 0 ? `no ${noun}s` : `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function capitalise(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function shortDate(iso: string) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
