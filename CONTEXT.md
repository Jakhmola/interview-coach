# Domain glossary

The project's shared vocabulary. Agent skills use these terms exactly — in issue
titles, plans, test names, and module names — and avoid the listed synonyms.
Terms are added lazily, as decisions resolve them (see `docs/agents/domain.md`).

## Prep flow

**Prep run**
One execution of `prep_graph` for a `(user, job)` pair, keyed by the
`prep:{user_id}:{job_id}` checkpoint thread:
`profile_builder → doc-mapping loop → job_analyzer → company_researcher`.

**Profile document set**
The set of document ids that *contribute to a profile*: the CV plus every
`project_doc` whose mapping has been confirmed. This — not the user's full
upload list — is the canonical cache key for the profile. Computed in exactly
one place (`repos.current_profile_doc_ids`).
_Avoid_: "documents list" / "the user's documents" when you mean this set — the
full upload list flips the key the moment a `project_doc` lands on disk, before
its mapping is applied.

**Skip verdict**
A prep node's decision to **skip** (reuse a cached output) or **run**
(recompute), carrying a typed **cache reason**. A verdict is a pure decision;
emitting the corresponding stream event is the node's job, not the verdict's.
_Avoid_: passing bare reason strings around — the reason is a field on the
verdict.

**Cache reason**
The typed reason on a skip verdict.
- skip: `cached` · `already_analyzed` · `no_unmapped_project_docs`
- run:  `missing` · `stale` · `forced` · `degraded`

**Node outcome**
How a prep node's *run* turned out — one of `ok` or `degraded`. Distinct from
the **skip verdict**, which is the decision of *whether* to run: a node that
skipped has no outcome (it never ran). The **cache reason** says *why* a node ran
or skipped; the outcome says *how* the run finished.
_Avoid_: conflating "outcome" with "reason"; using a bare `degraded` boolean when
you mean the typed outcome.

**Degraded snapshot**
A placeholder company snapshot persisted when company research soft-fails
(`CompanyNameMissing`, `NoSearchHits`, `NoUsablePages`); tagged with
`_degraded` in its JSON. A degraded snapshot is **stale**, not a cache hit — the
next prep run re-attempts research rather than serving the placeholder. The term
surfaces in two faithful-but-distinct places: as a **run** cache reason (the
*prior* snapshot was degraded, so this prep re-attempts) and as a node
**outcome** (*this* run produced the placeholder).

## GitHub ingestion

**GitHub handle**
The user's GitHub username — the canonical entry point for repo discovery. Either
**extracted** from the CV (a `github.com/<handle>` link) or **supplied** by the
user, then **verified** on its own wizard card (a `GET /users/{handle}` existence
check) *before* setup runs — failing fast on a typo. One handle per user;
resolving it lists that account's public repos. The handle is verified at the
card; the repo *selection* HITL happens later, inside the prep graph.
_Avoid_: "GitHub URL" / "repo link" when you mean the account handle — a handle
discovers the whole public set; a repo link names one repo.

**Selected repo**
A public repository the user has **chosen** (from the discovered list) to ingest.
Discovery lists *all* public repos; only selected repos are scraped and embedded.
Selection is itself the **inclusion HITL** — distinct from the doc-mapping loop's
*routing* HITL: a repo is never ambiguous about where it goes (it always becomes
one standalone **github project**), so the only human decision is *whether* to
include it, not *how* to route it.
_Avoid_: "the user's repos" when you mean this chosen subset.

**GitHub project**
The standalone `ProjectItem` (`source='github'`) a **selected repo** contributes
to the **Profile**: `name`←repo, `description`/`tech`/`key_features`/`architecture`
←LLM-extracted (code-free) from README + manifests + tree, `urls`←repo URL. No
`role` (a public repo doesn't state one). `tech` is capped to the 10 most
influential. Folded in at profile-assembly time (no per-repo HITL — selection
already confirmed it), and its `github_repo` document id joins the **Profile
document set** so selection changes invalidate the profile cache.
_Avoid_: treating a github project like a `project_doc` — it skips the mapping loop.

**Extraction vs grounding split** (one ingest feeds two layers)
The **profile/ProjectItem** is built **code-free** (README + manifests + tree) — a
deliberate cap that keeps the extraction LLM inside its 8192 ctx. **Grounding** is
richer: the stored/embedded `raw_text` adds a *bounded, ranked slice of source
files* (`select_source_files`: ≤10 files / ~300 KB, dominant-language-first, no
vendored/test/generated, **token-gated** — skipped without a `GITHUB_TOKEN`). So
**Project Deep-Dive** and the future technical round (P33) retrieve code-level
detail the profile never quotes. `select_source_files` is the deliberate
re-introduction of a source fetcher, scoped to grounding only.
_Avoid_: feeding source code into the ProjectItem extraction — that's the cap.

## Interview loop

**Thread**
One topic in an interview session — a *root question* plus the interviewer's
follow-up moves on the same focus and the candidate's answers to them. The
thread is the unit that gets **evaluated**: one score, feedback, and model
answer over the whole topic conversation, produced when the thread closes. A
session runs `n_questions` threads, one per topic.
_Avoid_: "turn" for a whole topic — a turn was the old single
question-answer-score row; a thread is a multi-message conversation scored once.

**Message**
One utterance in a thread, tagged with its **role** (interviewer or candidate)
and, for the interviewer, its **move**. A thread's ordered messages are its
transcript.

**Interviewer move**
What the interviewer does at a step, chosen by reading the thread so far:
**question** (opens the thread on a fresh focus), **probe** (a deeper follow-up
targeting an anchor the cumulative answer hasn't covered), **clarify**
(re-explain the question when the candidate's message was a meta-question about
it, not an answer), **nudge** (a hint that steers a stuck or off-track candidate
toward a better answer), **advance** (close the thread — firing its evaluation —
and open the next topic).
_Avoid_: scoring a **clarify** or **nudge** as if it were an answer — they are
interviewer help; a nudge also signals the candidate needed it.

**Wrap**
The session terminal, reached when the interviewer chooses **advance** and the
topic budget (`n_questions`) is spent. Not an interviewer move — the budget ends
the session, the interviewer never does.
_Avoid_: modeling wrap as a move the interviewer can pick.
