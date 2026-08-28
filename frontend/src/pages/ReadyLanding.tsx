import { ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { DocumentItem, JobItem, PrepStatus } from "../api";
import { Field, JobField, PenNote, SheetHead, shortDate } from "../components/ui";
import { firstSentence } from "../digest";
import { useAuth } from "../state/auth";

/**
 * The packet's cover, shown on Setup once prep is complete. Three things and
 * nothing else: the NEXT box (one line on what a round is, and the one
 * action), the role brief as the JD analysis read it, and - in a margin
 * beside the brief - how full the packet is (a four-cell tally: CV, job
 * description, supporting docs, repos) with the interviewer's own red-pen
 * nudge for whatever is missing. Manage owns the inventory itself.
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
  const navigate = useNavigate();

  // The payloads arrive loosely typed; read what agents/schemas.py promises
  // (JobAnalysis, CompanySnapshot) and skip the rest.
  const brief = (status?.job ?? job.parsed_json ?? {}) as {
    title?: unknown;
    seniority?: unknown;
    company_name?: unknown;
    must_have_skills?: unknown;
    nice_to_have_skills?: unknown;
    behavioral_signals?: unknown;
  };
  const company = status?.company ?? null;
  const missionText = (company?.snapshot as { mission?: unknown } | undefined)?.mission;
  const mission = isString(missionText) ? firstSentence(missionText, 160) : null;

  const seniority = isString(brief.seniority) && brief.seniority !== "unknown" ? capitalise(brief.seniority) : null;
  const companyName = isString(brief.company_name) ? brief.company_name : company?.company_name;
  const lede = [seniority, isString(brief.title) ? brief.title : "the role", companyName ? `at ${companyName}` : null]
    .filter(Boolean)
    .join(" ");
  const mustHave = strings(brief.must_have_skills).slice(0, 6);
  const niceToHave = strings(brief.nice_to_have_skills).slice(0, 4);
  const looksFor = strings(brief.behavioral_signals).slice(0, 6);

  // The tally: what is in the packet, in the order the intake asks for it.
  const have = [!!cv, true, techDocs.length > 0, repos.length > 0];
  const count = have.filter(Boolean).length;
  const onFile = [
    cv ? "CV" : "no CV",
    "job description",
    techDocs.length > 0 ? plural(techDocs.length, "supporting doc") : "no docs",
    repos.length > 0 ? plural(repos.length, "repo") : "no repos",
  ].join(" · ");

  // The interviewer nudges toward whatever would sharpen its questions.
  const nudges: { text: string; action: string; go: () => void }[] = [];
  if (repos.length === 0) {
    nudges.push({
      text: "Add your GitHub repos. In the deep-dive I ask about the code itself, not just what the CV says about it.",
      action: "Add repos",
      go: onManage,
    });
  }
  if (techDocs.length === 0) {
    nudges.push({
      text: "Add a project doc - an architecture note, a take-home, a write-up. I ground questions in it, not only in the CV.",
      action: "Add a doc",
      go: () => navigate("/setup?step=docs"),
    });
  }

  return (
    <div className="wizard">
      <SheetHead
        title="The packet"
        page={company?.updated_at ? `Prepped ${shortDate(company.updated_at)}` : "Ready to practice"}
      >
        <Field label="Candidate" value={user?.email} />
        <JobField />
      </SheetHead>

      <div className="box next">
        <span className="lbl">Next</span>
        <p>One topic at a time: the interviewer asks, follows up, then scores it and shows a model answer.</p>
        <button className="btn-primary" type="button" onClick={onStart}>
          Start a round <ArrowRight size={14} />
        </button>
      </div>

      <div className="spread">
        <div className="box brief">
          <span className="lbl">Role brief</span>
          <p className="lede">{lede}</p>
          {mission ? <p className="company">{mission}</p> : null}
          {mustHave.length > 0 ? (
            <div className="row">
              <span className="k">Must have</span>
              <p>{mustHave.join(" · ")}</p>
            </div>
          ) : null}
          {niceToHave.length > 0 ? (
            <div className="row">
              <span className="k">Nice to have</span>
              <p>{niceToHave.join(" · ")}</p>
            </div>
          ) : null}
          {looksFor.length > 0 ? (
            <div className="row">
              <span className="k">Looks for</span>
              <p>{looksFor.join(" · ")}</p>
            </div>
          ) : null}
        </div>

        <aside className="margin" aria-label="How full the packet is">
          <div className="tally">
            <span className="k">
              Packet · {count} of {have.length}
            </span>
            <div className="cells4" role="img" aria-label={`${count} of ${have.length} in the packet`}>
              {have.map((on, i) => (
                <i key={i} className={on ? undefined : "empty"} />
              ))}
            </div>
            <span className="hint">
              {onFile} ·{" "}
              <button type="button" className="btn-quiet" onClick={onManage}>
                Manage
              </button>
            </span>
          </div>
          {nudges.map((n) => (
            <div key={n.action} className="nudge">
              <PenNote kind="nudge" text={n.text} />
              <button type="button" className="btn-quiet" onClick={n.go}>
                {n.action}
              </button>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}

const isString = (v: unknown): v is string => typeof v === "string" && v.trim() !== "";
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter(isString) : []);

function plural(n: number, noun: string) {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

function capitalise(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
