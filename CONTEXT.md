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
