---
version: 1
slug: "frontend-src-pages-interviewpage-tsx"
primary_target: "frontend/src/pages/InterviewPage.tsx"
related_targets: ["frontend/src/pages/SetupPage.tsx","frontend/src/pages/HistoryPage.tsx","frontend/src/pages/ManagePage.tsx","frontend/src/pages/LoginPage.tsx","frontend/src/components/AppShell.tsx","frontend/src/styles.css"]
---

# Practice (/interview) - surface brief

Scope: the live interview round, its start screen, its completed state, and (by extension) the whole app shell: Setup, History, Manage, Login inherit this world. Visitor mode: Operate (the candidate is answering an interview); Login is the one Persuade surface (cover sheet with the pitch).

Audience and job: a candidate rehearsing for one named role; reads a question, writes a long answer, gets probed, gets scored. Action: submit a response; end a session (two-click). Proof/content on the page: the real question, the candidate's own typed words, the interviewer's move (probe/clarify/nudge) as a red-pen note, the thread's agenda (`anchors_json`) and sources (`focus_document_ids` -> document filenames), the 1-10 rating and feedback, previous topics with ratings.
Constraints: every LLM output streams token by token; the stream ends and waits for the candidate; local latency is seconds; nothing may imply cloud or accounts-for-sale; fonts self-hosted.

Chosen direction: **The Hiring Packet** (seed 6228d936, IMPECCABLE'S PICK over the roll's Checkride). Approved comp: `.impeccable/mocks/decision/model-pick.png` (HTML mock, `model-pick.html`). Paper stocks, user-chosen toggle: light `.impeccable/mocks/stock/offwhite-charcoal.html`, dark `.impeccable/mocks/stock/dark-slate-night.html`.
Memorable moment: the rating cell stamping in highlighter when a topic closes, next to the interviewer's red-pen notes in the margin.

## Fidelity inventory (from the approved comp; sampled values are the mock's own tokens)

| Ingredient | Record | Medium |
|---|---|---|
| Ground (desk) | radial gradient #3a3a3e -> #1f1f22 (dark stock #1a1a1d -> #0d0d10) | CSS |
| Sheet | #f5f2ea (dark #2b2d32), 0 radius, shadow 0 22px 44px rgba(0,0,0,.4) + 0 2px 4px rgba(0,0,0,.25); grain: SVG feTurbulence at 5% alpha (light only) | CSS + inline SVG data URI |
| Margin rule | 1px #c8c6bf at 60px from the sheet's left edge | CSS pseudo-element |
| Staples | two 16x3 gray strips rotated -45deg, top-left | CSS |
| Index tabs | 30px tall, 1px ink border, radius 3px 3px 0 0, Archivo 700 11px caps .14em; active = ink fill, paper text; locked = ink-3 text | CSS |
| Form title rule | 2px ink; title Archivo 700 24px caps .02em; page meta 12px ink-2 | CSS |
| Underlined fields | caps label 10px .14em ink-2 over 14.5px 500 value; 1px ink underline | CSS |
| Boxed field | 1px ink border; inverted label (ink bg, paper text, 9.5px caps .14em) overlapping the top-left corner | CSS |
| Question | Archivo 500 20px, lh 1.35, max 56ch | CSS |
| Candidate response | Courier Prime 15.5px on 26px ruling (#c8c6bf) | CSS |
| Red pen | #c8321f (dark #ef6a55); Archivo at 78% width, 15.5px 500; note block rotated -1.2deg; hand-drawn arrow SVG | CSS + inline SVG |
| Highlighter | #ffe94d (dark #ffd84a) with #1b1b1f text; marks "Your turn" and the scored cell | CSS |
| Rating cells | 30x30 (mini 22x22), 1px ink, 12px 600; scored cell highlighter + stamp animation 260ms | CSS |
| Buttons | 1.5px ink outline, caps 11px .16em; primary = ink fill; quiet = underlined text; armed = pen fill | CSS |
| Icons | lucide-react, 13-16px, stroke 1.8 | icon library |
| Loading | typewriter status (react-type-animation) + pulsing pen dot | existing lib + CSS |
| Completion | react-confetti in packet colors (#ffe94d #c8321f #f5f2ea #1b1b1f #ffb257) | existing lib |
| Type faces | Archivo variable (wght 100-900, wdth 62-125) roman + italic; Courier Prime 400/700/italic; `frontend/public/fonts/` | self-hosted woff2 |

No raster assets are produced or shipped; every material is CSS/SVG. Nothing to embed provenance into.

Unresolved: whether to surface the interviewer's agenda to the candidate at all (currently shown; it is the thread's real anchors and helps coverage, but it reveals what the probes will target).
