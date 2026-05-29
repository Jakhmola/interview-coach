# Merge to `main` through protected PRs, not local fast-forward

**Status:** accepted (Phase 31)

## Context & decision

Through Phase 30, the workflow merged each phase branch into `main` by **local
fast-forward + `git push origin main`** (master.md workflow steps 6–7). There was
no pull request and no automated gate: `main` stayed clean on developer
discipline alone.

Phase 31 introduces CI (GitHub Actions). For CI to actually *protect* `main`
rather than just decorate branches with advisory checks, the merge has to flow
through a **pull request** whose required status checks must be green. So we are
reversing the fast-forward decision: from now on a phase ships by pushing its
branch, opening a PR into `main`, and merging through GitHub once CI is green.

`main` is configured with branch protection:

- require a pull request before merging,
- require status checks (`backend`, `frontend`) to pass,
- **require approvals: 0** — deliberately, see below,
- administrators included (the gate applies to everyone, including the repo owner).

## Considered options

- **Keep local fast-forward, run CI advisory-only** (`on: push` to branches).
  Rejected. CI would report red/green but nothing would stop a broken merge into
  `main`; the guard we want would be cosmetic.
- **PRs + require ≥1 approval** (full team-style gate). Rejected for now. This is
  a single-maintainer repo (owner + Claude). GitHub does not let an author
  approve their own PR, so a required approval with no second account would lock
  the only maintainer out of merging. Revisit if a human collaborator joins.
- **PRs + protected `main`, 0 required approvals (chosen).** Enforces "no merge
  without green CI" without the self-approval deadlock. The owner reviews their
  own work as before, but the *automated* gate is now mandatory.

## The surprising bit (why this ADR exists)

Phases 0–30 explicitly fast-forwarded `main` and pushed directly; a future reader
diffing the workflow will see that reversed and wonder why the ceremony appeared.
The reason is the protection model, not process for its own sake: an *advisory*
check is worth little, so we accept one extra step (open a PR, merge the button)
to make `main` un-break-able by CI-failing code.

Two non-obvious consequences worth recording:

1. **`require approvals: 0` is intentional, not an oversight.** It avoids the
   single-maintainer self-approval trap. Do not "tighten" it to 1 without first
   adding a second account or collaborator.
2. **Bootstrapping order matters.** A status-check context can only be marked
   *required* once GitHub has seen it run at least once. So the very first CI
   workflow is merged via its own PR (checks run on that PR), and branch
   protection is applied *after* that merge — not before.

## Consequences

- master.md workflow steps 6–7 change from "fast-forward `main` + push" to "push
  branch → open PR → merge when CI green (+ owner approval as desired)".
- With administrators included, even the owner no longer pushes straight to
  `main`; all changes transit a PR.
- The change composes with existing tooling: `/code-review`, `/babysit` (watch a
  PR to green), and inline PR review comments.
- If a human collaborator ever joins, revisit the `require approvals` setting.
