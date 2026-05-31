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
to the **Profile**: `name`←repo, `description`←README, `tech`←language stats,
`urls`←repo URL. Folded in at profile-assembly time (no per-repo HITL — selection
already confirmed it), and its `github_repo` document id joins the **Profile
document set** so selection changes invalidate the profile cache. A github project
is an ordinary focus candidate for **Project Deep-Dive**; the future technical
round adds *code-level* grounding over the same repo's chunks.
_Avoid_: treating a github project like a `project_doc` — it skips the mapping loop.
