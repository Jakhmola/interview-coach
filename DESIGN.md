---
name: Interview Coach
description: The interview rendered as the hiring packet the panel fills in - a paper scorecard on a charcoal desk, typed transcript, red-pen margin notes.
colors:
  # Day stock (`:root`). Night stock (`:root[data-theme="dark"]`) remaps every
  # token below; see Colors for the night values and .impeccable/design.json
  # colorMeta.<token>.night for the machine-readable pairs.
  paper: "#f5f2ea"
  paper-2: "#ebe7dd"
  paper-3: "#e2ded3"
  ink: "#1b1b1f"
  ink-2: "#4a4a4f"
  ink-3: "#6a6a70"
  rule: "#c8c6bf"
  rule-2: "#8f8d87"
  pen: "#c8321f"
  pen-soft: "rgba(200, 50, 31, 0.12)"
  hl: "#ffe94d"
  hl-2: "#ffb257"
  hl-ink: "#1b1b1f"
  ok: "#2f7d4f"
  ok-soft: "rgba(47, 125, 79, 0.14)"
  desk-1: "#3a3a3e"
  desk-2: "#1f1f22"
  on-desk: "#cfcdc7"
  on-desk-2: "#8e8d89"
  night-paper: "#2b2d32"
  night-ink: "#ece9e2"
  night-pen: "#ef6a55"
  night-hl: "#ffd84a"
  night-desk-1: "#1a1a1d"
  night-desk-2: "#0d0d10"
typography:
  display:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "24px"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.02em"
    textTransform: "uppercase"
  headline:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "20px"
    fontWeight: 500
    lineHeight: 1.35
  title:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "14.5px"
    fontWeight: 500
    lineHeight: 1.3
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.45
  typed:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "15.5px"
    fontWeight: 400
    lineHeight: "26px"
  pen:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "15.5px"
    fontWeight: 500
    lineHeight: 1.35
    fontVariation: "wdth 78"
  label:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "10px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "0.14em"
    textTransform: "uppercase"
  control:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "11px"
    fontWeight: 700
    letterSpacing: "0.16em"
    textTransform: "uppercase"
rounded:
  none: "0"
  tab: "3px 3px 0 0"
  desk-btn: "3px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "14px"
  2xl: "18px"
  3xl: "22px"
  gutter: "28px"
  sheet-x: "56px"
  sheet-margin: "76px"
components:
  button-secondary:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.none}"
    padding: "0 18px"
    height: "38px"
  button-secondary-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.control}"
    rounded: "{rounded.none}"
    padding: "0 18px"
    height: "38px"
  button-primary-hover:
    backgroundColor: "{colors.ink-2}"
    textColor: "{colors.paper}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.none}"
    padding: "0 10px"
    height: "34px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    typography: "{typography.control}"
    padding: "0 10px"
    height: "34px"
  button-armed:
    backgroundColor: "{colors.pen}"
    textColor: "#ffffff"
    typography: "{typography.control}"
    rounded: "{rounded.none}"
    padding: "0 18px"
    height: "38px"
  tab:
    backgroundColor: "{colors.paper-2}"
    textColor: "{colors.ink}"
    typography: "{typography.control}"
    rounded: "{rounded.tab}"
    padding: "0 22px"
    height: "30px"
  tab-active:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  box-label:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    typography: "{typography.label}"
    padding: "3px 8px"
  box-label-pen:
    backgroundColor: "{colors.pen}"
    textColor: "#ffffff"
  rating-cell:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.none}"
    size: "30px"
  rating-cell-on:
    backgroundColor: "{colors.hl}"
    textColor: "{colors.hl-ink}"
  mark:
    backgroundColor: "{colors.hl}"
    textColor: "{colors.hl-ink}"
    padding: "1px 4px"
  status-pill:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    padding: "0 7px"
    height: "20px"
  textarea-typed:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.typed}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
  error-banner:
    backgroundColor: "{colors.pen-soft}"
    textColor: "{colors.ink}"
    padding: "10px 14px"
  success-banner:
    backgroundColor: "{colors.hl}"
    textColor: "{colors.hl-ink}"
    padding: "8px 12px"
---

# Design System: Interview Coach

## Overview

**Creative North Star: "The Hiring Packet"**

The app is the document that decides the candidate's fate, being filled in live. Every screen is a photocopied form on a charcoal desk: index tabs cut from the sheet's top edge, a 1px margin rule, two staples, boxed fields with inverted corner labels, a form title under a 2px rule. Three voices share the sheet and never blur: the form speaks in Archivo caps, the candidate speaks in Courier Prime on 26px rulings, and the interviewer speaks in red ballpoint (Archivo condensed to 78% width, tilted -1.2deg in the margin). Yellow highlighter is the only other colour, and it always means "this is what matters right now": whose turn it is, which option is chosen, which cell got the score.

The world is flat and material. Depth exists once, between the desk and the sheet (and again for a slip laid on top of the packet); nothing on the paper itself floats. There are no rounded cards, no tinted panels, no gradients on the sheet. Structure is drawn with rules, boxes and inverted labels, the way a printed form does it. Density is that of a real scorecard: 15px body, 10px caps labels, 14px gaps, one sheet per screen.

Two paper stocks exist and the reader chooses: day (off-white on charcoal) and night (slate pages, light ink on a near-black desk). The night stock keeps every relationship, only the pigments change; the grain is dropped because dark paper does not photocopy.

**Key Characteristics:**
- Paper sheet on a desk: `--paper` with SVG grain and a two-layer shadow, on a radial charcoal gradient.
- Three voices, three faces: Archivo caps (form), Courier Prime (candidate), Archivo wdth 78 red (interviewer).
- Zero radius on the sheet; the only curves are the 3px top corners of the index tabs.
- Highlighter (`--hl`) is a state, not a decoration: current turn, selection, score.
- Red (`--pen`) belongs to the interviewer and to armed destructive controls; nothing else is red.
- Motion is the hand at work: typewriter status, pulsing pen dot, blinking caret, the stamp, confetti in packet colours.

## Colors

Two neutral families (desk and paper), one ink ramp, and three pigments that each own a job: red pen, yellow highlighter, green ok.

### Primary
- **Red Pen** (`--pen`): the interviewer's voice. Margin notes, inline remarks (PROBE / NUDGE / CLARIFY), agenda checklist, "Rated n/10", the pen label variant of a boxed field, the caret in every text field, the pulsing dot while the model thinks. Also the armed state of a destructive control and the running / failed task dot. Night stock lifts it to a warmer `#ef6a55` so it still reads as ballpoint on slate.
- **Pen Wash** (`--pen-soft`): the only tint. Error banner fill behind a 1.5px pen border.

### Secondary
- **Highlighter** (`--hl`): marks the live thing. "Your turn", the chosen round-type option, the current job in a menu, the active wizard step, the scored 1-10 cell, text selection, focus rings, the success banner. Always paired with `--hl-ink` (ink stays dark on yellow in both stocks).
- **Orange Highlighter** (`--hl-2`): the warn variant of a mark, the degraded task dot, and the warn status pill on night stock.

### Tertiary
- **Ok Green** (`--ok`): the good status pill and the checkmark icon in a wizard note. Nothing else.

### Neutral
- **Paper** (`--paper`, `-2`, `-3`): the sheet, resting index tabs (`paper-2`), hover fill on rows and menu items (`paper-2`). Day `#f5f2ea`, night `#2b2d32`.
- **Ink** (`--ink`, `-2`, `-3`): text, every structural border (1px boxes, 2px title rule, 1.5px button outlines, 13px checkboxes), primary button fill, active tab fill, inverted labels. `ink-2` is secondary text and caps labels; `ink-3` is placeholders, locked tabs, the colophon. Day `#1b1b1f`, night `#ece9e2`.
- **Rule** (`--rule`, `--rule-2`): the light lines. `rule` draws the 60px margin rule, the 26px typed rulings, list-row dividers, footer rules. `rule-2` is the dashed empty-state border, chips, and unselected wizard dots.
- **Desk** (`--desk-1`, `--desk-2`, `--on-desk`, `--on-desk-2`): the surface under the sheet (radial gradient) and the muted lettering that sits on it (who is logged in, day/night, log out).

### Named Rules
**The Red Is The Interviewer Rule.** `--pen` is reserved for the interviewer's voice and for the armed state of a destructive control. Errors use it because an error is the interviewer's note too. Never use red for emphasis, links, or decoration; a resting destructive control is ink, and turns red only once armed.

**The Highlighter Is A State Rule.** `--hl` fills exactly the thing that is live: the current turn, the chosen option, the scored cell, the focused field's ring. It never fills a button, a card, or a heading.

**The Two-Stock Rule.** Every colour is a custom property on `:root` and remapped on `:root[data-theme="dark"]`. No component may hard-code a pigment; the stock is set before first paint from `localStorage.stock` (fallback `prefers-color-scheme`) and toggled by the desk button. Exceptions that exist in the build: `#fff` on pen-filled controls, the staple greys, and the modal backdrop.

## Typography

**Display / Form Font:** Archivo variable (wght 100-900, wdth 62-125), self-hosted, with Helvetica Neue / Arial fallback.
**Typed Font:** Courier Prime 400 / 400 italic / 700, self-hosted, with Courier New fallback.
**Label Font:** Archivo (same family, caps and tracking do the work).

**Character:** A printed form and a typewriter. Archivo does the form's lettering in tight, tracked caps and the plain reading text; Courier Prime is only what the candidate typed; the interviewer writes in Archivo squeezed to 78% width so it reads as a narrower, faster hand. No italic display, no serif, no system UI face.

### Hierarchy
- **Display** (700, 24px, caps, 0.02em; 20px under 720px): the form title under its 2px rule ("Interview Scorecard", the login brand).
- **Headline** (500, 20px, lh 1.35, max 56ch; 17px under 720px): the question in its box; page titles on Setup, History, Manage, the completion title, the modal title (700 caps there).
- **Title** (500, 14.5px): values in underlined header fields, list-row primary text.
- **Body** (400, 15px, lh 1.45): reading text; 13-13.5px in `--ink-2` for meta and hints; max 62-80ch on prose.
- **Typed** (Courier Prime 400, 15.5px on a 26px line): the candidate's responses, the textarea they type in, model answers (14.5/24), typewriter status lines (14px), history transcripts (13.5/22).
- **Pen** (Archivo 500, wdth 78, 15.5px, lh 1.35, `--pen`): margin notes and inline remarks; 15px in remarks and previous-topic exchanges; keyed by an 11px/10px caps label ("PROBE", "NUDGE", "AGENDA").
- **Label** (700, 10px, 0.14em, caps, `--ink-2`): field labels, section heads (11px), previous-topic keys, model-answer summary; 9.5px inverted on the box label and status pill.
- **Control** (700, 11px, 0.16em, caps): buttons; tabs use 0.14em; quiet buttons 0.1em; desk buttons 0.1em.

### Named Rules
**The Three Voices Rule.** Courier Prime is only ever the candidate's words (and the model answer as a would-be transcript). Red condensed Archivo is only ever the interviewer. Everything else is the form and is set in Archivo at normal width. A voice never borrows another's face.

**The Caps Label Rule.** Every label is 9.5-11px Archivo 700, uppercase, tracked 0.12-0.16em, in `--ink-2` (or inverted on ink). Labels sit above or in the top-left corner of the thing they name. They are never placed above a heading as a kicker.

## Layout

One sheet per screen, centred on the desk. The desk pads `44px 24px 56px` (36/12/40 under 980px), the desk bar (tabs left, tools right) is `min(1180px, 100%)` wide, and the sheet is the same width with `min-height: calc(100vh - 140px)`. Sheet padding is `34px 56px 40px 76px`: the 76px left keeps content right of the 60px margin rule (48/34 under 980px, 32/22 under 720px). Login uses a narrower `min(760px, 100%)` cover sheet with the same margin and tabs.

The sheet is a vertical flex column with an 18px gap; inside it, sections are grids with 12-14px gaps and open with a 1px ink top rule. The scorecard header is an auto-fit grid of underlined fields (`minmax(150px, 1fr)`, gap 14px 22px); on the live scorecard it becomes four fixed columns (candidate / role / topic / no.) and collapses to one under 720px.

The live scorecard is two columns: `minmax(0, 1fr) 250px` with a `14px 28px` gap. The question box and the main column (responses, remarks, reply box, actions, rating row) occupy the left; the right margin (latest note, agenda, grounded-in) spans both rows and never pushes the form down. Under 980px the margin drops below the main column and the note loses its tilt.

Spacing rhythm: 4, 6, 8, 10, 12, 14, 16, 18, 22, 28. Row lists (jobs, docs, sessions, previous topics) use 10px vertical padding with a `--rule` divider and no side padding; hovered rows tint `--paper-2`. Modals are a `min(720px, 100%)` slip with `28px 32px 24px` padding over a 55% black backdrop.

Breakpoints: 980px (single-column scorecard, tighter sheet) and 720px (stacked desk bar, one-column fields, smaller cells and title). Minimum width 320px.

## Elevation & Depth

Depth is physical and happens exactly twice: the sheet sits on the desk, and a slip (menu, modal) sits on the sheet. On the paper itself everything is flat; hierarchy is drawn with 1px and 2px ink rules, boxes and inverted labels, never with shadow or tint. Day stock adds SVG feTurbulence grain at 5% alpha to the sheet; night stock has none.

### Shadow Vocabulary
- **Sheet** (`--sheet-shadow`: `0 22px 44px rgba(0,0,0,.4), 0 2px 4px rgba(0,0,0,.25)`; night `.6 / .4`): the sheet and the login cover sheet on the desk.
- **Slip** (`--slip-shadow`: `0 18px 40px rgba(0,0,0,.45), 0 2px 4px rgba(0,0,0,.3)`; night `.7 / .5`): the active-job menu and the mapping modal.
- **Focus ring** (`0 0 0 2px var(--hl)` on textareas, selects and the reply box; `0 2px 0 var(--hl)` under underlined inputs): the highlighter around the live field.

### Named Rules
**The Paper Is Flat Rule.** Nothing on the sheet casts a shadow. If an element needs to stand out, box it in 1px ink, give it an inverted label, or highlight it. Shadows exist only for paper on the desk and slips on the paper.

## Shapes

Square. Every box, field, button, cell, checkbox, chip, banner and modal has `border-radius: 0`. The only curves in the world are the 3px top corners of the index tabs (and the 3px desk button that mirrors them off-sheet), round radio buttons, and the round 8px pen dot. Borders are ink: 1px for boxes, rules, cells and tabs; 1.5px for buttons, checkboxes, range thumbs and pen-bordered cards; 2px for the form title rule. Light structure (dividers, dashed empty states, chips) uses `--rule` / `--rule-2`. Inverted labels overlap the box's top-left corner by 1px so they read as printed on the border. Nothing is clipped, blurred, or gradiented on the sheet.

## Components

### Buttons
Form lettering on a printed outline; the hover is an ink fill, not a colour change.
- **Shape:** square (`0`), 38px tall, 1.5px ink outline, `0 18px` padding, 11px 700 caps 0.16em, 10px gap to a 13px lucide icon.
- **Primary:** ink fill, paper text; hover shifts to `--ink-2`.
- **Secondary:** paper fill, ink text; hover inverts to ink fill.
- **Ghost:** transparent, no outline, 34px tall, `0 10px`; hover draws the ink outline.
- **Quiet:** transparent, `--ink-2`, underlined, 0.1em tracking; hover to ink. Used for "back" and inline secondary actions.
- **Destructive:** resting state is a ghost in `--ink-2` (hover: ink outline). Armed state (`.armed`) fills `--pen`, white text, 1s opacity pulse; a consequence span drops to 12px sentence case. The morph is the warning; there is no red at rest. The typed-confirmation button in Manage is pen-filled from the start because the confirmation input is the arming step.
- **Disabled:** 45% opacity, `not-allowed` cursor.

### Index Tabs
Cut from the sheet's top edge. 30px tall, `0 22px`, `--paper-2` fill, 1px ink border with no bottom, `3px 3px 0 0`, 11px 700 caps 0.14em. Active: ink fill, paper text. Locked: `--ink-3` text, `not-allowed`. Hover: `--paper`. The row is indented 76px to align with the sheet's content edge; under 720px it scrolls horizontally.

### Desk Tools
Off-sheet controls (who, day/night, log out) in `--on-desk`, 11px 700 caps 0.1em, 26px tall, 3px radius, transparent 1px border that shows in `--on-desk-2` on hover. 14px lucide icon.

### Underlined Field
Header field on the scorecard: 10px caps label in `--ink-2`, 3px gap, 14.5px 500 value with a 1px ink underline, single line with ellipsis (`.wrap` clamps to 3 lines). Empty value is `--ink-3` at 400. The active-job field is this shape as a button; its menu is a slip with `--paper-2` hover rows and a highlighter-filled current row.

### Boxed Field
1px ink box, `22px 20px 16px` padding, with an inverted label (`--ink` fill, `--paper` text, 9.5px 700 caps 0.14em, `3px 8px`) overlapping the top-left corner at -1px. Variants: `.q` (question, 20px 500, max 56ch), `.r` (a filed response, typed text on `--rule` 26px rulings, max 80ch), `.reply` (padding 0, the textarea inside; `focus-within` draws the 2px highlighter ring). The round-type fieldset on the start screen is the same box with a `<legend>` as label.

### Inputs / Fields
- **Text input:** bare 1px ink underline, transparent, `7px 0`, 15px; focus adds a 2px highlighter underline. Placeholder `--ink-3`.
- **Textarea:** 1px ink box on `--paper`, 26px rulings drawn with a repeating gradient in `--rule` (scrolls with content), `8px 12px`, min 120px; `.typed` switches to Courier Prime 15.5px. Focus: 2px highlighter ring.
- **Select:** same box as textarea, no native chevron, 32px right padding for a lucide caret.
- **Checkbox / radio:** 13px, 1.5px ink border, paper fill; checked draws a 7px ink square (or dot). Radio is round.
- **Range:** 1px ink track, 14x22 paper thumb with 1.5px ink border; focus fills the thumb with highlighter.
- **Dropzone:** 1.5px dashed `--rule-2`, `26px 20px`, centred; hover / focus-within goes ink border on `--paper-2`.
- **Error / disabled:** disabled at 55% opacity; errors are a banner, not a field state.
- **Caret:** `--pen` in every field.

### Rating Cells
Ten 30px squares (mini: 22px; 26px under 720px) with 1px ink borders, 4px apart, 12px 600 tabular numerals in `--ink-2`. The scored cell fills `--hl` with `--hl-ink` and lands with the stamp animation (260ms, `cubic-bezier(.16,1,.3,1)`, from `scale(1.35) rotate(-6deg)` at 40% opacity). Exposed as one `role="img"` with an aria-label.

### Red-Pen Note and Remark
- **Margin note** (`.note`): `--pen`, rotated -1.2deg from its top-left (upright under 980px and for `.static` notes), 11px caps key with a 34x18 hand-drawn arrow SVG, then pen text at 15.5px. Agenda is a `.checklist.pen`: 12px hollow squares in currentColor, filled with an inset paper ring when done.
- **Inline remark** (`.remark`): an earlier interviewer move written on the form between responses; `82px minmax(0,1fr)` grid, 10px caps key, 15px pen text max 64ch, indented 20px.
- Both stream in with a 220ms 4px rise (`.stream-in`) and end with a 2px blinking caret while text is arriving.

### Marks and Banners
- **Mark** (`mark`, `.mark`): highlighter fill, `--hl-ink`, `1px 4px`, 600. `.warn` uses `--hl-2`. "Your turn" is a mark.
- **Success banner:** highlighter fill, 13.5px 500, `8px 12px`.
- **Error banner:** 1.5px pen border on `--pen-soft`, 14px pen strong line plus 13px `--ink-2` hint.
- **Empty state:** 1px dashed `--rule-2`, `22px 20px`, `--ink-2` with a 16px ink strong line.

### Status Pill (rubber stamp)
20px tall, `0 7px`, 1px border in currentColor, 9.5px 700 caps 0.12em. Colour is the state: `--ok` good, `--pen` bad, ink info, amber warn (`--hl-2` on night stock). Never filled.

### Chips
1px `--rule-2` outline, `1px 6px`, 11px 600 0.04em in `--ink-2`, square. Used for repo languages and wizard tags; no selected state.

### Lists and Rows
Job, document, session, previous-topic and manage rows: 10px vertical padding, `--rule` bottom divider, no side padding, `--paper-2` on hover. Selected rows (current job, chosen round type) fill highlighter and gain 10px side padding. Previous topics are `<details>` rows with a `82px 1fr auto` summary carrying the caps key, the truncated focus, and mini rating cells.

### Modal (slip)
`min(720px, 100%)` paper slip with grain and `--slip-shadow`, `28px 32px 24px`, title under a 2px ink rule, 30px square ink-outlined close button (hover inverts), footer over a 1px ink rule. Backdrop `rgba(0,0,0,.55)`. The count/context line sits under the title as meta, never above it.

### Loading and Progress
- **Thinking:** `.practice-loading` - an 8px `--pen` dot pulsing at 1s next to a Courier Prime typewriter status (react-type-animation), 13.5px `--ink-2`.
- **Streaming:** `.cursor-blink`, 2px x 1em in currentColor, `steps(2)` at 1s.
- **Task rows:** 13px square dots: hollow pending, ink-filled done, pen-bordered pulsing running, pen-filled failed, `--hl-2` degraded.
- **Wizard steps:** 26px outlined pills with a 9px square; done = ink, active = highlighter fill.
- **Completion:** react-confetti, fixed full-screen, `pointer-events: none`, in packet colours `#ffe94d #c8321f #f5f2ea #1b1b1f #ffb257`, piece count scaled to the score.
- All animation collapses to 0.01ms under `prefers-reduced-motion: reduce`.

### Colophon
The sheet ends with `.sheet-foot`: right-aligned 12px `--ink-3`, "Interview Coach · runs on your machine", pushed to the bottom by `margin-top: auto`.

## Do's and Don'ts

### Do:
- **Do** put every screen on one sheet: `--paper` with `--grain`, `--sheet-shadow`, the 60px margin rule and two staples, tabs cut from the top edge.
- **Do** draw structure with ink rules and boxes (1px box, 2px title rule, 1.5px button outline) and name things with 10px caps labels or inverted corner labels.
- **Do** set the candidate's words in Courier Prime 15.5px on 26px `--rule` rulings, and the interviewer's in `--pen` Archivo at 78% width.
- **Do** use `--hl` for the live thing only: current turn, chosen option, scored cell, focus ring.
- **Do** keep destructive controls ink at rest and morph them to `--pen` with a pulse only when armed.
- **Do** define any new colour on both stocks (`:root` and `:root[data-theme="dark"]`) and pair `--hl` with `--hl-ink`.
- **Do** render icons from lucide at 12-16px (13px inside buttons, 14px in desk tools), stroke 1.8, in currentColor.
- **Do** honour `prefers-reduced-motion`; motion is limited to the stamp, the pen dot, the caret, the 220ms stream-in, and completion confetti.

### Don't:
- **Don't** round corners on the sheet. The only radius is `3px 3px 0 0` on index tabs and `3px` on desk buttons.
- **Don't** cast shadows on the paper. Shadows belong to the sheet on the desk and slips on the sheet.
- **Don't** use `--pen` for emphasis, links, headings or decoration; red is the interviewer and the armed state.
- **Don't** fill buttons, cards or headings with highlighter, and don't tint panels with any colour but `--paper-2` on hover.
- **Don't** set anything but the candidate's words (and the model answer) in Courier Prime, and don't use a serif, an italic display, or a system UI face.
- **Don't** place a caps label above a heading as a kicker or eyebrow; labels name a field or a section and sit on the thing they name (the modal's meta line sits under its title).
- **Don't** hard-code a pigment in a component; the stock toggle must remap it.
- **Don't** fetch fonts or assets from outside the box; faces are self-hosted woff2 under `/fonts/`.
