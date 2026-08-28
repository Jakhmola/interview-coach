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
  paper-under: "#d8d4c9"
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
  warn: "#a8650f"
  desk-1: "#3a3a3e"
  desk-2: "#1f1f22"
  on-desk: "#cfcdc7"
  on-desk-2: "#8e8d89"
  on-desk-3: "#ffffff"
  night-paper: "#2b2d32"
  night-paper-under: "#1f2125"
  night-ink: "#ece9e2"
  night-pen: "#ef6a55"
  night-hl: "#ffd84a"
  night-warn: "#ffb257"
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
    fontSize: "21px"
    fontWeight: 500
    lineHeight: 1.35
  page-title:
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
  meta:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  hint:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0.06em"
  typed:
    fontFamily: "Courier Prime, Courier New, Courier, monospace"
    fontSize: "15.5px"
    fontWeight: 400
    lineHeight: "26px"
  pen:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 500
    lineHeight: 1.35
    fontVariation: "wdth 78"
  label:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "10.5px"
    fontWeight: 700
    lineHeight: 1.3
    letterSpacing: "0.14em"
    textTransform: "uppercase"
  label-inverted:
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
    backgroundColor: "{colors.paper-under}"
    textColor: "{colors.ink-2}"
    typography: "{typography.control}"
    rounded: "{rounded.tab}"
    padding: "0 22px"
    height: "28px"
  tab-active:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    height: "32px"
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

The world is flat and material. Depth exists once, between the desk and the sheet (and again for a slip laid on top of the packet); nothing on the paper itself floats. There are no rounded cards, no tinted panels, no gradients on the sheet. Structure is drawn with rules, boxes and inverted labels, the way a printed form does it. Density is that of a real scorecard: 15px body, 10.5px caps labels, 14px gaps, one sheet per screen - and the sheet takes most of the desk (`min(1600px, 100%)`), so on a 1920px display the paper covers five sixths of the width with a strip of desk either side.

Two paper stocks exist and the reader chooses: day (off-white on charcoal) and night (slate pages, light ink on a near-black desk). The night stock keeps every relationship, only the pigments change; the grain is dropped because dark paper does not photocopy.

**Key Characteristics:**
- Paper sheet on a desk: `--paper` with SVG grain and a two-layer shadow, on a radial charcoal gradient.
- Three voices, three faces: Archivo caps (form), Courier Prime (candidate), Archivo wdth 78 red (interviewer).
- Zero radius on the sheet; the only curves are the 3px top corners of the index tabs.
- Highlighter (`--hl`) is a state, not a decoration: current turn, selection, score.
- Red (`--pen`) belongs to the interviewer and to armed destructive controls; nothing else is red.
- Motion is the hand at work: typewriter status, pulsing pen dot, blinking caret, the stamp, the highlighter drawn across "Your turn", the pen drawing the margin arrow, confetti in packet colours - and the paper moving: the reply box files itself as a response, a closed topic folds into the index, the next sheet is pulled from the packet (view transitions).

## Colors

Two neutral families (desk and paper), one ink ramp, and three pigments that each own a job: red pen, yellow highlighter, green ok.

### Primary
- **Red Pen** (`--pen`): the interviewer's voice. Margin notes, inline remarks (PROBE / NUDGE / CLARIFY), agenda checklist, "Rated n/10", the pen label variant of a boxed field, the caret in every text field, the pulsing dot while the model thinks. Also the armed state of a destructive control and the running / failed task dot. Night stock lifts it to a warmer `#ef6a55` so it still reads as ballpoint on slate.
- **Pen Wash** (`--pen-soft`): the only tint. Error banner fill behind a 1.5px pen border.

### Secondary
- **Highlighter** (`--hl`): marks the live thing. "Your turn", the chosen round-type option, the current job in a menu, the active wizard step, the scored 1-10 cell, text selection, focus rings, the success banner. Always paired with `--hl-ink` (ink stays dark on yellow in both stocks).
- **Orange Highlighter** (`--hl-2`): the warn variant of a mark and the degraded task dot.

### Tertiary
- **Ok Green** (`--ok`): the good status pill and the checkmark icon in a wizard note. Nothing else.
- **Warn Amber** (`--warn`): the warn status pill only. Day `#a8650f` (amber ink that holds 4.5:1 on paper); night stock lifts it to the orange highlighter `#ffb257`.

### Neutral
- **Paper** (`--paper`, `-2`, `-3`): the sheet, resting index tabs (`paper-2`), hover fill on rows and menu items (`paper-2`). Day `#f5f2ea`, night `#2b2d32`.
- **Ink** (`--ink`, `-2`, `-3`): text, every structural border (1px boxes, 2px title rule, 1.5px button outlines, 13px checkboxes), primary button fill, active tab fill, inverted labels. `ink-2` is secondary text and caps labels; `ink-3` is placeholders, locked tabs, the colophon. Day `#1b1b1f`, night `#ece9e2`.
- **Rule** (`--rule`, `--rule-2`): the light lines. `rule` draws the 60px margin rule, the 26px typed rulings, list-row dividers, footer rules. `rule-2` is the dashed empty-state border, chips, and unselected wizard dots.
- **Desk** (`--desk-1`, `--desk-2`, `--on-desk`, `--on-desk-2`, `--on-desk-3`): the surface under the sheet (radial gradient) and the muted lettering that sits on it (who is logged in, day/night, log out); `on-desk-3` is that lettering at full brightness on hover.

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
- **Headline** (500, 21px, lh 1.35; 17px under 720px): the question in its box, filling the box's width. **Page title** (500, 20px): page titles on Setup, History, Manage, the completion title, the modal title (700 caps there).
- **Title** (500, 14.5px): values in underlined header fields, list-row primary text.
- **Body** (400, 15px, lh 1.45): reading text. Standalone prose (login pitch, wizard blurbs) keeps a 52-66ch measure; anything inside a box, a record or a row fills its container - a boxed field with an empty right half reads as wasted paper. **Meta** (13-13.5px, `--ink-2`): row metadata, the "Your turn" line, page meta (12.5px). **Hint** (12px, 0.06em, `--ink-2`): "Ctrl + Enter submits", "scored when the topic closes".
- **Typed** (Courier Prime 400, 15.5px on a 26px line): the candidate's responses, the textarea they type in, model answers (14.5/24), typewriter status lines (14px), exchange transcripts (14/22), the History model answer (13.5/22).
- **Pen** (Archivo 500, wdth 78, 16px, lh 1.35, `--pen`): margin notes; 15.5px in inline remarks and the agenda, 15px in exchange turns; keyed by an 11px/10px caps label ("PROBE", "NUDGE", "AGENDA").
- **Label** (700, 10.5px, 0.14em, caps, `--ink-2`): field labels, section heads (11px), previous-topic keys, model-answer summary, the brief's row keys (10px); 10px inverted on the box label and status pill.
- **Control** (700, 11px, 0.16em, caps): buttons; tabs use 0.14em; quiet buttons 0.1em; desk buttons 0.1em.

### Named Rules
**The Three Voices Rule.** Courier Prime is only ever the candidate's words (and the model answer as a would-be transcript). Red condensed Archivo is only ever the interviewer. Everything else is the form and is set in Archivo at normal width. A voice never borrows another's face.

**The Caps Label Rule.** Every label is 9.5-11px Archivo 700, uppercase, tracked 0.12-0.16em, in `--ink-2` (or inverted on ink). Labels sit above or in the top-left corner of the thing they name. They are never placed above a heading as a kicker.

## Layout

One sheet per screen, centred on the desk and taking most of it. The desk pads `28px 32px 40px` (36/12/40 under 980px), the desk bar (tabs left, tools right) is `min(1600px, 100%)` wide, and the sheet is the same width with `min-height: calc(100vh - 98px)`, so on a laptop the sheet's foot lands at the bottom of the first screen. Sheet padding is `30px 56px 36px 76px`: the 76px left keeps content right of the 60px margin rule (48/34 under 980px, 32/22 under 720px). Login uses a narrower `min(760px, 100%)` cover sheet with the same margin and tabs, centred vertically on the desk; it says what this is once - the brand rule with its mark, one headline (21px 500) and one sentence in `--ink-2` - then the form, then a single quiet button that switches to the other tab ("New here? Create an account" / "Already registered? Log in"). No proof list, no second privacy line: the mark already says "runs on your machine".

The sheet is a vertical flex column with a 16px gap; inside it, sections are grids with 12-14px gaps and open with a 1px ink top rule. The scorecard header is an auto-fit grid of underlined fields (`minmax(150px, 1fr)`, gap 14px 22px); on the live scorecard it becomes four fixed columns (candidate / role / topic / no.) and collapses to one under 720px. The topic number is stated once, in the NO. field; the title's meta line names only the round type.

The live scorecard is two columns: `minmax(0, 1fr) minmax(340px, 400px)` with a `14px 40px` gap. The question box and the main column (responses, remarks, reply box, actions, rating row) occupy the left; the right margin (latest note, agenda, grounded-in) spans both rows and never pushes the form down. Two things stay at hand while a long transcript scrolls: the reply box (`form.composer`) is `position: sticky; bottom: 0` on paper with a 12px paper fade above it, and on desktops at least 981px wide and 780px tall the margin is `sticky; top: 24px`. Under 980px the margin drops below the main column and the note loses its tilt.

The ready landing (Setup, once prep is complete) is the packet's cover, titled THE PACKET with "Prepped <date>" as its page meta (the company research's timestamp, the last node of prep). Three things and nothing else. The header names the candidate and the role once (the ROLE / COMPANY field is the job switcher; no second role heading, no CV field). Then the **NEXT box** (`.box.next`): a boxed field with an inverted NEXT label holding one 21px line on what a round is ("One topic at a time: the interviewer asks, follows up, then scores it and shows a model answer.") and the one action, Start a round, so the action owns the sheet and a cold visitor reads what the button does before pressing it. Then `.spread`, `minmax(0, 1fr) minmax(280px, 340px)` with a `14px 40px` gap: on the left the **Role brief** (`.box.brief`) as the JD analysis read it - a 16px lede (seniority, title, "at <company>"), the first sentence of the company's mission in 13.5px `--ink-2`, then `.row`s (`104px minmax(0, 1fr)`, 14px, a 10px caps key) for Must have, Nice to have and Looks for, each a " · " run (capped at 6, 4 and 6 items); on the right a **margin** (`.margin`) with the packet **tally** (`.tally`: an 11px caps "PACKET · 3 OF 4", four 26px cells - CV, job description, supporting docs, repos - ink-filled with an inset paper ring when on file, dashed `--rule-2` when not, and a 12.5px hint naming them with a quiet Manage) and, for each of docs and repos that is missing, the interviewer's own **nudge**: a `.note` in red pen keyed NUDGE ("Add your GitHub repos. In the deep-dive I ask about the code itself, not just what the CV says about it.") with a pen-coloured quiet Add repos / Add a doc under it. At 4 of 4 the margin is the tally alone. The inventory itself lives on Manage. Under 980px the margin drops under the brief; under 720px the NEXT box stacks and the brief's keys sit above their runs.

History records (`.record`) use the same width: a head row with the topic number, the full topic label (wrapping, never truncated by the UI) and mini rating cells; then `minmax(0, 3fr) minmax(0, 2fr)` with the exchange on the left and the interviewer's verdict on the right. Collapsed, the exchange shows the question and the candidate's first answer (`.clamp`, 3 lines) and the verdict its first 4 lines; "Full exchange · n turns" opens every turn and the model answer, "Show less" folds it back. One column under 980px.

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
- **Destructive:** resting state is a ghost in `--ink-2` (hover: ink outline). Armed state (`.armed`) fills `--pen`, white text, 1s opacity pulse; a consequence span drops to 12px sentence case. The morph is the warning; there is no red at rest. The typed-confirmation button in Manage is pen-filled from the start because the confirmation input is the arming step; its hover mixes the pen 20% toward ink (`color-mix`), the same "toward ink" shift every filled button makes.
- **Disabled:** 45% opacity, `not-allowed` cursor.

### Index Tabs
Cut from the sheets' top edges, and where you are is the tab that is cut from the top sheet: the active tab is `--paper` with the sheet's grain, `--ink` text, 32px tall, and meets the sheet with no seam. The other tabs belong to the sheets underneath - `--paper-under` fill (a shade darker than the sheet on both stocks), `--ink-2` text, 28px tall, so they sit lower and read as behind. All: `0 22px`, 1px ink border with no bottom, `3px 3px 0 0`, 11px 700 caps 0.14em. Locked: `--ink-3` text, `not-allowed`. Hover: `--paper-2` and ink text. The row is `align-items: flex-end`, indented 76px to align with the sheet's content edge; under 720px it scrolls horizontally. The login cover sheet's Log in / Register tabs are the same `.tab`s, positioned above the card (`.auth-card .tabs`).

### Desk Tools
Off-sheet controls (who, day/night, log out) in `--on-desk`, 11px 700 caps 0.1em, 26px tall, 3px radius, transparent 1px border that shows in `--on-desk-2` on hover. 14px lucide icon.

### Underlined Field
Header field on the scorecard: 10px caps label in `--ink-2`, 3px gap, 14.5px 500 value with a 1px ink underline, single line with ellipsis (`.wrap` clamps to 3 lines). Empty value is `--ink-3` at 400. The active-job field is this shape as a button; its menu is a slip with `--paper-2` hover rows, a highlighter-filled current row and, under a `--rule`, a "New job description" row (12.5px `--ink-2`, lucide plus) that opens the intake at the JD step - so a new role is one click from wherever the field is.

### Boxed Field
1px ink box, `22px 20px 16px` padding, with an inverted label (`--ink` fill, `--paper` text, 10px 700 caps 0.14em, `3px 8px`) overlapping the top-left corner at -1px. Whatever is in a box fills it: no measure caps on the question, a response, the assessment or the model answer. Variants: `.q` (question, 21px 500), `.r` (a filed response, typed text on `--rule` 26px rulings, filling the box ruling for ruling exactly as the reply box did), `.reply` (padding 0, the textarea inside grows with its content via `field-sizing: content` up to 44vh; `focus-within` draws the 2px highlighter ring), `.assessment` (pen-labelled; rating row, feedback, model answer). The round-type fieldset on the start screen is the same box with a `<legend>` as label.

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
Ten 30px squares (mini: 22px; 26px under 720px) with 1px ink borders, 4px apart, 12px 600 tabular numerals in `--ink-2`. The row appears only where there is a score or one being decided: in the interviewer's assessment (arriving with it), on the completed round, on previous-topic rows and History records - never as an empty placeholder under the reply box. While the score is being decided (`.cells.scoring`) the pen runs along the row: each cell tints highlighter for a beat, staggered 110ms per cell (`--i`), on a 1.6s loop. The scored cell then fills `--hl` with `--hl-ink` and lands with the stamp animation (260ms, `cubic-bezier(.16,1,.3,1)`, from `scale(1.35) rotate(-6deg)` at 40% opacity). Exposed as one `role="img"` with an aria-label ("Scoring" while the sweep runs).

### Red-Pen Note and Remark
- **Margin note** (`.note`, the shared `PenNote` in `components/ui.tsx`): `--pen`, rotated -1.2deg from its top-left (upright under 980px and for `.static` notes), 11px caps key with a 34x18 hand-drawn arrow SVG, then pen text at 16px. It is the interviewer's voice wherever it appears: the live scorecard's margin, and the packet cover's nudge toward a missing doc or repo. Agenda is a `.checklist.pen`: 12px hollow squares in currentColor, filled with an inset paper ring when done.
- **Inline remark** (`.remark`): an earlier interviewer move written on the form between responses; `82px minmax(0,1fr)` grid, 10px caps key, 15.5px pen text across the column, indented 20px.
- Both stream in with a 220ms 4px rise (`.stream-in`) and end with a 2px blinking caret while text is arriving; while the note streams, the arrow draws itself (`pathLength` 1, dash offset 1 to 0 over 420ms).

### Marks and Banners
- **Mark** (`mark`, `.mark`): highlighter fill, `--hl-ink`, `1px 4px`, 600. `.warn` uses `--hl-2`. "Your turn" is a mark.
- **Success banner:** highlighter fill, 13.5px 500, `8px 12px`.
- **Error banner:** 1.5px pen border on `--pen-soft`, 14px pen strong line plus 13px `--ink-2` hint.
- **Empty state:** 1px dashed `--rule-2`, `22px 20px`, `--ink-2` with a 16px ink strong line.

### Status Pill (rubber stamp)
20px tall, `0 7px`, 1px border in currentColor, 10px 700 caps 0.12em. Colour is the state: `--ok` good, `--pen` bad, ink info, amber warn (`--hl-2` on night stock). Never filled. On a History row the stamp is the session's fate: COMPLETE in `--ok`, ABANDONED in `--pen`, ACTIVE in ink.

### Chips
1px `--rule-2` outline, `1px 6px`, 11px 600 0.04em in `--ink-2`, square. Used for repo languages and wizard tags; no selected state.

### Lists and Rows
Job, document, session, previous-topic and manage rows: 10px vertical padding, `--rule` bottom divider, no side padding, `--paper-2` on hover. Selected rows (current job, chosen round type) fill highlighter and gain 10px side padding. A Manage row for a CV, JD or supporting doc carries the file's first line under its name (`.excerpt`: the API's 200-char `preview`, line breaks quoted as " / ", in quotes, 13px `--ink-2`, clamped to 2 lines), so a file is known by what it says, not by its name. Previous topics are `<details>` rows with a `82px 1fr auto` summary carrying the caps key, the truncated focus, and mini rating cells. A filed turn anywhere (previous topics, History records) is an `.exchange`: a `120px minmax(0,1fr)` grid, caps key (`--pen` for the interviewer's moves), then the words in the speaker's face. The live round's foot holds only the End session control (ghost, `--ink-2`); its consequence ("files what you have so far to History") is written only once the control is armed.

### Modal (slip)
`min(720px, 100%)` paper slip with grain and `--slip-shadow`, `28px 32px 24px`, title under a 2px ink rule, 30px square ink-outlined close button (hover inverts), footer over a 1px ink rule. Backdrop `rgba(0,0,0,.55)`. The count/context line sits under the title as meta, never above it.

### Loading and Progress
- **Thinking:** `.practice-loading` - an 8px `--pen` dot pulsing at 1s next to a Courier Prime typewriter status (react-type-animation), 13.5px `--ink-2`.
- **Streaming:** `.cursor-blink`, 2px x 1em in currentColor, `steps(2)` at 1s.
- **Task rows:** 13px square dots: hollow pending, ink-filled done, pen-bordered pulsing running, pen-filled failed, `--hl-2` degraded.
- **Wizard steps:** 26px outlined pills with a 9px square; done = ink, active = highlighter fill.
- **Completion:** react-confetti, fixed full-screen, `pointer-events: none`, in packet colours `#ffe94d #c8321f #f5f2ea #1b1b1f #ffb257`, piece count scaled to the score.
- All animation collapses to 0.01ms under `prefers-reduced-motion: reduce`.

### Motion: the paper moving
The stream-in, stamp, caret and pen dot are the hand at work; the paper itself moves through document view transitions (`frontend/src/viewTransition.ts`, a `startViewTransition` + `flushSync` helper that applies the update plainly where the API is missing or reduced motion is on). Groups ease over 320ms on `cubic-bezier(.16,1,.3,1)`, old/new snapshots crossfade in 200ms, the root in 140ms.
- **Filing a response:** the reply box (`view-transition-name: composer`) morphs into the just-submitted response, which carries the same name only while the interviewer is thinking.
- **Turning the page:** the page turns the moment the next topic's first frame arrives, not when its question has finished streaming. A streamed move for a thread other than the open one runs one view transition in which the scored topic's assessment box folds into its previous-topics row (they share `topic-<index>`; the row is built from the live evaluation until the refetch replaces it), the question box (`question`) clears for the new question to stream into, and the window scrolls to the top so the new topic reads from the head of the sheet. The refetch that follows changes nothing visible; the composer returns after it, rising in with `.stream-in`.
- **Pulling the next sheet:** a plain click on an index tab runs the navigation inside a transition tagged `<html data-vt="route">`: the old sheet (`sheet`) fades out in 120ms, the new one rises 10px in 280ms, and the ink fill slides from tab to tab (`active-tab`).
- **The highlighter swipe:** the "Your turn" mark is painted left-to-right over 380ms (`background-size` 0 to 100%) as the reply box opens.
- **The pen along the row:** while a topic is being scored, the assessment's rating cells arrive with the box and tint highlighter one after another (110ms stagger, 1.6s loop) until the score stamps its cell.
- Names are unique per snapshot by construction; a duplicate would abort the transition (Chrome logs it), so treat a console warning as a defect.

### Colophon
The sheet ends with `.sheet-foot`: right-aligned 12px `--ink-3`, "Interview Coach · runs on your machine", pushed to the bottom by `margin-top: auto`.

### Favicon
An inline SVG data URI in `index.html`: the packet on the desk at 32px - a `#1f1f22` desk, a `#f5f2ea` sheet with its `#1b1b1f` inverted label, two rulings and a `#ffe94d` highlighter mark. Pigments are literal because a favicon cannot read custom properties.

## Do's and Don'ts

### Do:
- **Do** put every screen on one sheet: `--paper` with `--grain`, `--sheet-shadow`, the 60px margin rule and two staples, tabs cut from the top edge.
- **Do** draw structure with ink rules and boxes (1px box, 2px title rule, 1.5px button outline) and name things with 10px caps labels or inverted corner labels.
- **Do** set the candidate's words in Courier Prime 15.5px on 26px `--rule` rulings, and the interviewer's in `--pen` Archivo at 78% width.
- **Do** use `--hl` for the live thing only: current turn, chosen option, scored cell, focus ring.
- **Do** keep destructive controls ink at rest and morph them to `--pen` with a pulse only when armed.
- **Do** define any new colour on both stocks (`:root` and `:root[data-theme="dark"]`) and pair `--hl` with `--hl-ink`.
- **Do** render icons from lucide at 12-16px (13px inside buttons, 14px in desk tools), stroke 1.8, in currentColor.
- **Do** honour `prefers-reduced-motion`; motion is limited to the stamp, the pen dot, the caret, the 220ms stream-in, the highlighter swipe, the arrow draw, the view transitions listed under Motion, and completion confetti. Reduced motion skips the view transitions entirely and collapses the rest.
- **Do** keep the reply box and the interviewer's clipboard sticky on desktop; whose move it is must survive a long transcript.

### Don't:
- **Don't** round corners on the sheet. The only radius is `3px 3px 0 0` on index tabs and `3px` on desk buttons.
- **Don't** cast shadows on the paper. Shadows belong to the sheet on the desk and slips on the sheet.
- **Don't** use `--pen` for emphasis, links, headings or decoration; red is the interviewer and the armed state.
- **Don't** fill buttons, cards or headings with highlighter, and don't tint panels with any colour but `--paper-2` on hover.
- **Don't** set anything but the candidate's words (and the model answer) in Courier Prime, and don't use a serif, an italic display, or a system UI face.
- **Don't** place a caps label above a heading as a kicker or eyebrow; labels name a field or a section and sit on the thing they name (the modal's meta line sits under its title).
- **Don't** hard-code a pigment in a component; the stock toggle must remap it.
- **Don't** fetch fonts or assets from outside the box; faces are self-hosted woff2 under `/fonts/`.
