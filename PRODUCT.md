# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Three audiences, all confirmed by the owner (2026-08-28):

1. **The owner, prepping for their own interviews.** Runs the stack locally on
   their machine, in the days and evenings before real interviews, on a laptop
   screen. The primary daily user.
2. **Other candidates who clone and self-host it.** They meet the app cold, with
   no walkthrough, after `make up`.
3. **Reviewers (recruiters and engineers) judging it as a portfolio piece.**
   They see the README, the demo gif, and a quick click-through.

The job in every case: rehearse for one named role, get scored per topic, and
see what a strong answer would have looked like - against the candidate's own
CV, project docs, and repos, not a generic question bank.

## Product Purpose

Personalized interview practice grounded in the candidate's real material.
Upload CV and project docs, link GitHub, paste a job description, and a local
LLM runs a real back-and-forth interview: it asks tailored questions, probes the
answers, then scores each topic and shows a model answer. Success is a candidate
finishing a round with a per-topic score, feedback, and model answer they can
revisit in History.

## Positioning

Three mechanisms lead together (owner-confirmed); none is subordinate:

1. **Grounded in your real work.** CV, supporting docs, and a bounded slice of
   the candidate's own GitHub source are embedded and retrieved at
   question-generation time. Deep-dive rounds ask about code the profile never
   quotes.
2. **A real interviewer, not a quiz.** The interviewer works one topic (thread)
   at a time: it asks, then chooses at runtime to probe, clarify, or nudge, and
   grades the whole exchange once before advancing.
3. **Private, on your own GPU.** Qwen3-8B on a local llama.cpp container.
   Nothing leaves the box except optional Tavily web search.

## Operating Context

- **Journey:** register/log in -> Setup wizard (CV -> GitHub handle -> job
  description by text or URL -> supporting docs -> prep) -> Practice (pick round
  type and topic count -> live threaded conversation -> per-topic score,
  feedback, model answer -> round complete) -> History (past sessions grouped by
  job, full transcripts). A Manage page edits the inventory (CV, JDs, docs,
  repos) and holds the account reset.
- **Prep** is a long, streamed, multi-node job (profile, GitHub ingestion, doc
  mapping, JD analysis, company research) with per-node reasons (cached, stale,
  degraded, skipped). It pauses twice for the human: doc-mapping confirmation
  and repo selection, both currently modals.
- **Turn-taking is explicit.** Every interviewer utterance streams token by
  token; after a move that needs an answer the stream ends and waits for the
  candidate. Evaluation streams score, then feedback, then the model answer.
- **Latency is real.** Cold start is 30-60 s while the model loads; the first
  call is slow. Every LLM step is seconds, not milliseconds.
- **Active job** is a global: readiness and navigation gating hang off it;
  multiple jobs can exist and be switched.
- Current IA is a left sidebar (Setup / Practice / History) with an active-job
  chip; this is incumbent, not binding.

## Capabilities and Constraints

- Round types: **Experience deep-dive** (grounded in docs + repos), **Technical
  challenge** (forward-looking domain problems, no grounding), **Behavioral /
  STAR** (no grounding). A round is 1-10 topics.
- Interviewer moves: `question` (opens a thread), `probe`, `clarify`, `nudge`,
  `advance` (closes the thread and fires evaluation). `wrap` ends the session.
  Clarify and nudge are never scored as answers.
- Evaluation per thread: score out of 10, feedback, model answer. Session
  average is shown at the end.
- Auth: multi-user email + password (JWT). Session expiry routes to login.
- Errors arrive as backend codes translated to user-facing copy in
  `frontend/src/errors.ts`.
- Practice and History are locked until the active job's prep reports ready.
- **Not built - never design for it:** voice input / speech-to-text, a
  multi-segment full mock interview, a system-design round or web grounding for
  technical rounds, hosted deployment, cloud-LLM switching, Markdown ingestion.

## Brand Commitments

- Name: **Interview Coach**. Tagline in README: "Personalized AI interview
  practice."
- No logo asset exists; the current mark is a Lucide `Sparkles` glyph
  placeholder. No binding palette or typeface. The incumbent "Copper Aquamarine
  Dream" warm-dark look is explicitly released for replacement (owner:
  "Nothing - all fair game", 2026-08-28). Copy, IA, and theme may change if the
  chosen direction earns it; flows and features stay.
- Voice in product: plain, direct, slightly playful ("Opening the studio...",
  nav says "Practice" not "Interview").

## Evidence on Hand

- `docs/demo.gif` - a real 30 s recording of the current UI (setup wizard, prep,
  round start, live feedback).
- README mermaid diagrams of the flow and architecture.
- **Absent, never fabricate:** sample CVs or JDs in the repo, testimonials,
  customers, benchmarks, pricing, hosted URLs.

## Product Principles

1. **The candidate's own material is the star.** Wherever it is true, show what
   a question was grounded in.
2. **Whose move it is must always be obvious**, and what the interviewer is
   doing (asking, probing, clarifying, nudging, scoring) is named, not implied.
3. **Long local work is honest.** Progress names what is happening and why
   (cached, rebuilt, degraded) instead of hiding behind a spinner.
4. **Private by construction.** Nothing in the interface implies a cloud
   account, a plan, or data leaving the machine.
5. **Cold-visitor legible.** A reviewer or a new self-hoster must grasp what
   this is on the first screen with no walkthrough.

## Accessibility & Inclusion

No product-specific requirement was established. Baseline expected: keyboard-
operable forms and modals, visible focus, `prefers-reduced-motion` respected
(the incumbent stylesheet already honors it).
