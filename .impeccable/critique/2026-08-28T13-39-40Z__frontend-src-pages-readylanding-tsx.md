---
target: the Setup ready landing
total_score: 25
max_score: 40
na_heuristics: 
p0_count: 2
p1_count: 2
timestamp: 2026-08-28T13-39-40Z
slug: frontend-src-pages-readylanding-tsx
---
Method: dual-agent (A: design review sub-agent · B: detector/browser sub-agent)

## Design Health Score

| # | Heuristic | Score | Key issue |
|---|---|---|---|
| 1 | Visibility of system status | 3 | "Complete · ready to practice" is clear, but nothing says when prep ran. |
| 2 | Match system / real world | 3 | "Candidate intake" titles a packet that is already complete. |
| 3 | User control and freedom | 3 | Switcher and Manage one click away; no way to fold a brief read ten times. |
| 4 | Consistency and standards | 3 | Sentence-length must-haves in 1px boxes read as disabled inputs. |
| 5 | Error prevention | 3 | Little to get wrong. |
| 6 | Recognition rather than recall | 2 | ~40 items visible, none changes what Start does. |
| 7 | Flexibility and efficiency | 2 | The one action has no accelerator; every visit re-renders the full brief. |
| 8 | Aesthetic and minimalist design | 1 | Three ragged columns under one 38px button. |
| 9 | Error recovery | 3 | Empty-research copy names the retry route; "Profile not built yet." does not. |
| 10 | Help and documentation | 2 | Nothing says what a round is before the button. |
| **Total** | | **25/40** | Acceptable |

## Design specificity
The frame is authored (staple, margin rule, index tabs, caps keys, the ROLE / COMPANY field doubling as the switcher). The content under it is a generic schema-to-columns dump that any SaaS overview tab could carry. Detector: 0 findings across ReadyLanding.tsx, SetupPage.tsx, ui.tsx, styles.css; console clean at 1440, 1920 and 390; no clipping or overflow; ink-2 7.9:1 and ink-3 4.8:1 on day paper.

## Priority issues
- [P0] The brief does not belong on the cover. Three rejections in a row name density. Fix: one action, one grounding line, at most one compact block.
- [P0] "Rounds for this role" shipped past a verbatim no. Fix: removed.
- [P1] The action does not own the sheet: a 38px button in a 100vh sheet. Fix: a boxed field with an inverted NEXT label and one line saying what a round is.
- [P1] `.ready-actions` split: the on-file line sat ~900px from the button. Fix: keep context under or beside the box.
- [P2] Fake affordances: duties as unticked checkboxes, requirement sentences in chip boxes. Fix: plain lines.

## Copy
"Candidate intake" -> "The packet" (ready state); "Complete · ready to practice" -> "Prepped 28 Aug"; "Start a practice round" -> "Start a round"; "On file:" -> "Grounded in"; "Must have / Nice to have / Signals / Duties" only if a brief survives, as "Must have / Nice to have / Looks for".

## Cognitive load
5 of 8 fail on the three-column version: progressive disclosure, single dominant action, scannable copy, no redundancy ("ready" said three times), defaults reducing decisions.
