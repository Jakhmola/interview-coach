"""Generate the round-completion animation variants as static HTML on the app's
own stylesheet (frontend/src/styles.css), so they can be played, recorded and
compared. `python3 gen.py` writes v1..v4.html (+ decision.html). Open any
variant with `#night` for the night stock; the desk bar carries a mock-only
Replay button.

The sheet is the real completed scorecard (three topics stitched from the
review account's three completed single-topic rounds: 8, 6, 5 -> 6.3). The
variants differ only in what happens in the first ~2.5 s after the sheet
lands. Today's react-confetti is the thing they replace."""
from pathlib import Path

HERE = Path(__file__).parent

TOPICS = [
    (8, "Designed a modular prompt system for a keyboard assistant: reusable tone, guideline and output-format blocks; 70% of prompt structure shared across features."),
    (6, "Introduced parallel generation across three prompt variants per request, reducing perceived latency to a single LLM call; owned the evaluation harness."),
    (5, "Built an LLM-powered conversational agent with retrieval over product docs; cut escalation rate 30% and doubled campaign CTR during seasonal launches."),
]
AVG = sum(s for s, _ in TOPICS) / len(TOPICS)  # 6.33
AVG_CELL = round(AVG)  # 6


def icon(paths: str, size: int = 14) -> str:
    return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'


I_ROTATE = icon('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>', 13)
I_MOON = icon('<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>')
I_OUT = icon('<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>')
I_REPLAY = icon('<polygon points="6 3 20 12 6 21 6 3"/>')


def cells(score: int, mini: bool = False, extra: str = "") -> str:
    inner = "".join(
        f'<span class="cell{" on" if n == score else ""}" style="--i:{n - 1}" aria-hidden="true">{n}</span>'
        for n in range(1, 11)
    )
    return f'<div class="cells{" mini" if mini else ""}{extra}" role="img" aria-label="Scored {score} out of 10">{inner}</div>'


def topics(row_extra: str = "") -> str:
    rows = "".join(
        f'<details class="prev-row" style="--r:{i}"><summary><span class="t">Topic {i + 1}</span><span class="focus">{label}</span>{cells(score, mini=True, extra=row_extra)}</summary></details>'
        for i, (score, label) in enumerate(TOPICS)
    )
    return f'<section class="prev" aria-label="Topics in this round"><span class="cap">Topics</span>{rows}</section>'


HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>if (location.hash === "#night") document.documentElement.dataset.theme = "dark";</script>
<link rel="stylesheet" href="completion.css">
<style>
/* mock only: the sheet is the animation's stage; Replay restarts it */
.sheet { position: relative; }
{extra}
</style>
</head>
<body>
<div class="desk">
  <div class="desk-bar">
    <nav class="tabs"><a class="tab" href="#">Setup</a><a class="tab active" href="#">Practice</a><a class="tab" href="#">History</a></nav>
    <div class="desk-tools"><button class="desk-btn replay" type="button">{replay}<span>Replay</span></button><span class="who">packet-review@gmail.com</span><button class="desk-btn"><span>{moon}Night</span></button><button class="desk-btn"><span>{out}Log out</span></button></div>
  </div>
  <main class="sheet">
    <i class="staple a"></i><i class="staple b"></i>
    <div class="practice-live">
      <header class="sheet-head"><h1>Interview scorecard</h1><span class="page">Experience deep-dive · Complete</span></header>
      <div class="fields">
        <div class="f"><span class="cap">Candidate</span><span class="v">packet-review@gmail.com</span></div>
        <div class="f wide"><span class="cap">Role / company</span><span class="v">GenAI Engineer <span class="co">@ Northwind Labs</span></span></div>
        <div class="f"><span class="cap">Date</span><span class="v">28 Aug 2026</span></div>
        <div class="f"><span class="cap">Topics</span><span class="v">3 of 3 scored</span></div>
      </div>
      <div class="practice-done">
"""

FOOT = """
      </div>
    </div>
    <footer class="sheet-foot">Interview Coach · runs on your machine</footer>
  </main>
</div>
<script>
const sheet = document.querySelector(".sheet");
function play() { sheet.classList.remove("play"); void sheet.offsetWidth; sheet.classList.add("play"); }
window.__play = play;
document.querySelector(".replay").addEventListener("click", play);
setTimeout(play, 300);
</script>
</body>
</html>
"""

# Round-complete block shared by every variant, with hooks the variants dress.
def done(title_html: str, score_html: str, after_score: str = "", before: str = "", topics_extra: str = "") -> str:
    return f"""{before}<h1 class="practice-done-title">{title_html}</h1>
<div class="practice-done-score"><span class="cap">Average</span>{score_html}<span class="hint">{AVG:.1f} / 10 over {len(TOPICS)} topics</span>{after_score}</div>
<p class="practice-done-hint">Filed to <a href="#">History</a>. Start another round whenever you're ready.</p>
<div><button type="button" class="btn-primary">{I_ROTATE} Start another round</button></div>
{topics(topics_extra)}"""


# Ink that did not take everywhere: a noise alpha mask for the rubber stamp.
STAMP_MASK = "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' seed='11'/%3E%3CfeColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 7 0 0 0 -2.3'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")"

# ── V1 · The stamp ──────────────────────────────────────────────────────────
V1_CSS = f"""
.practice-done-title {{ display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }}
.stamp {{
  display: inline-grid; justify-items: center; gap: 2px;
  padding: 8px 16px 7px; border: 3px double var(--ok); color: var(--ok);
  font-size: 17px; font-weight: 700; letter-spacing: 0.18em; text-transform: uppercase; line-height: 1.1;
  transform: rotate(-6deg); opacity: 0.92; mix-blend-mode: multiply;
  -webkit-mask-image: {STAMP_MASK}; mask-image: {STAMP_MASK};
}}
.stamp small {{ font-size: 10px; letter-spacing: 0.16em; }}
:root[data-theme="dark"] .stamp {{ mix-blend-mode: screen; }}
.sheet .stamp {{ opacity: 0; }}
.play .stamp {{ animation: slam 320ms cubic-bezier(0.16, 1, 0.3, 1) 420ms both; }}
.play.sheet {{ animation: jolt 180ms ease-out 640ms; }}
@keyframes slam {{
  from {{ transform: rotate(-6deg) scale(2); opacity: 0; }}
  55% {{ transform: rotate(-6deg) scale(0.96); opacity: 0.96; }}
  to {{ transform: rotate(-6deg) scale(1); opacity: 0.9; }}
}}
@keyframes jolt {{ 35% {{ transform: translateY(2px); }} }}
"""
V1 = done(
    'Round complete. <span class="stamp" aria-hidden="true"><b>Complete</b><small>28 Aug 2026</small></span>',
    cells(AVG_CELL),
)

# ── V2 · The pen signs off ──────────────────────────────────────────────────
V2_CSS = """
.practice-done-title { display: flex; align-items: center; gap: 12px; }
.tick { width: 26px; height: 22px; color: var(--pen); overflow: visible; }
.tick path { stroke-dasharray: 1; stroke-dashoffset: 1; }
.play .tick path { animation: draw 240ms cubic-bezier(0.4, 0, 0.2, 1) 980ms forwards; }
.ring-wrap { position: relative; display: inline-block; }
.ring { position: absolute; left: 0; top: 0; width: 336px; height: 30px; overflow: visible; color: var(--pen); pointer-events: none; }
.ring path { stroke-dasharray: 1; stroke-dashoffset: 1; }
.play .ring path { animation: draw 560ms cubic-bezier(0.4, 0, 0.2, 1) 380ms forwards; }
@keyframes draw { to { stroke-dashoffset: 0; } }
.practice-done-score .note { margin-left: 26px; opacity: 0; }
.play .practice-done-score .note { animation: rise 220ms cubic-bezier(0.16, 1, 0.3, 1) 1240ms both; }
@keyframes rise { from { opacity: 0; transform: rotate(-1.2deg) translateY(4px); } to { opacity: 1; transform: rotate(-1.2deg); } }
"""
# The on-cell sits at 5 * 34 px; the loop is drawn around it a little too big, the way a hand does it.
_cx = (AVG_CELL - 1) * 34 + 15
V2_RING = (
    f'<svg class="ring" viewBox="0 0 336 30" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    f'<path pathLength="1" d="M{_cx + 14} -3 C{_cx - 14} -11 {_cx - 30} 8 {_cx - 24} 24 C{_cx - 16} 42 {_cx + 24} 40 {_cx + 27} 17 C{_cx + 29} 3 {_cx + 18} -6 {_cx + 4} -5"/>'
    f"</svg>"
)
V2_NOTE = (
    '<div class="note" aria-live="polite"><div class="k">'
    '<svg viewBox="0 0 34 18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M32 9C24 9 16 4 3 9"/><path d="M8 5 3 9l5 4"/></svg>'
    "Verdict</div><p>Solid round.</p></div>"
)
V2 = done(
    'Round complete. <svg class="tick" viewBox="0 0 26 22" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path pathLength="1" d="M3 12 10 19 23 4"/></svg>',
    f'<span class="ring-wrap">{cells(AVG_CELL)}{V2_RING}</span>',
    after_score=V2_NOTE,
)

# ── V3 · The highlighter runs down the sheet ────────────────────────────────
V3_CSS = f"""
.practice-done-title mark {{ background: linear-gradient(var(--hl), var(--hl)) left / 0 100% no-repeat; padding: 1px 4px; margin: 0 -4px; }}
.play .practice-done-title mark {{ animation: swipe 380ms cubic-bezier(0.16, 1, 0.3, 1) 300ms both; }}
@keyframes swipe {{ to {{ background-size: 100% 100%; }} }}
.avg {{ position: relative; }}
.avg .sweep {{ position: absolute; left: 0; top: 0; width: 30px; height: 30px; background: var(--hl); opacity: 0; z-index: 0; }}
.avg .cell {{ position: relative; z-index: 1; }}
.play .avg .sweep {{ animation: run 720ms steps({AVG_CELL - 1}, jump-end) 700ms both, off 1ms linear 1430ms forwards; }}
@keyframes run {{ from {{ transform: translateX(0); opacity: 1; }} to {{ transform: translateX({(AVG_CELL - 1) * 34}px); opacity: 1; }} }}
@keyframes off {{ to {{ opacity: 0; }} }}
.sheet .cell.on {{ background: transparent; color: var(--ink-2); animation: none; }}
.play .avg .cell.on {{ animation: land 260ms cubic-bezier(0.16, 1, 0.3, 1) 1420ms both; }}
.play .prev-row .cell.on {{ animation: land 260ms cubic-bezier(0.16, 1, 0.3, 1) calc(1800ms + var(--r) * 170ms) both; }}
@keyframes land {{
  from {{ background: transparent; color: var(--ink-2); transform: scale(1.35) rotate(-6deg); opacity: 0.4; }}
  30% {{ background: var(--hl); color: var(--hl-ink); }}
  to {{ background: var(--hl); color: var(--hl-ink); transform: none; opacity: 1; }}
}}
"""
# the sweep square sits inside the .cells box, behind the first cell
_avg = cells(AVG_CELL, extra=" avg").replace(
    f'aria-label="Scored {AVG_CELL} out of 10">',
    f'aria-label="Scored {AVG_CELL} out of 10"><i class="sweep" aria-hidden="true"></i>',
    1,
)
V3 = done("<mark>Round complete.</mark>", _avg)

# ── V4 · Hole punch ─────────────────────────────────────────────────────────
import random

random.seed(4)
_COLORS = ["var(--ink)", "var(--hl)", "var(--pen)", "var(--hl-2)", "var(--paper-3)", "var(--ink)", "var(--hl)"]
_chads = "".join(
    f'<i style="--x:{random.uniform(2, 98):.1f}%;--d:{random.uniform(0, 0.9):.2f}s;--t:{random.uniform(1.6, 2.4):.2f}s;--c:{random.choice(_COLORS)};--r:{random.randint(-300, 300)}deg;--s:{random.randint(8, 13)}px;--dx:{random.randint(-70, 70)}px"></i>'
    for _ in range(72)
)
V4_CSS = """
.chads { position: absolute; inset: 0; overflow: hidden; pointer-events: none; z-index: 5; }
.chads i { position: absolute; top: -14px; left: var(--x); width: var(--s); height: var(--s); border-radius: 50%; background: var(--c); opacity: 0; }
.play .chads i { animation: fall var(--t) cubic-bezier(0.3, 0.1, 0.6, 1) var(--d) both; }
@keyframes fall {
  0% { transform: translate(0, 0) rotate(0); opacity: 0; }
  6% { opacity: 1; }
  88% { opacity: 1; }
  100% { transform: translate(var(--dx), calc(100vh - 80px)) rotateX(70deg) rotate(var(--r)); opacity: 0; }
}
"""
V4 = done("Round complete.", cells(AVG_CELL), before=f'<div class="chads" aria-hidden="true">{_chads}</div>')

VARIANTS = {
    "v1": ("V1 · The stamp", V1_CSS, V1),
    "v2": ("V2 · The pen signs off", V2_CSS, V2),
    "v3": ("V3 · The highlighter runs down the sheet", V3_CSS, V3),
    "v4": ("V4 · Hole punch", V4_CSS, V4),
}

# The app's stylesheet with its self-hosted faces pointed at
# frontend/public/fonts so a file:// mock renders in Archivo / Courier Prime.
app_css = (HERE / "../../../frontend/src/styles.css").read_text()
(HERE / "completion.css").write_text(app_css.replace('url("/fonts/', 'url("../../../frontend/public/fonts/'))

for slug, (title, css, body) in VARIANTS.items():
    page = (
        HEAD.replace("{title}", title).replace("{extra}", css).replace("{replay}", I_REPLAY).replace("{moon}", I_MOON).replace("{out}", I_OUT)
        + body
        + FOOT
    )
    (HERE / f"{slug}.html").write_text(page)
    print("wrote", slug + ".html")

NOTES = {
    "v1": "The panel's rubber stamp. 420 ms after the sheet lands, COMPLETE with the date slams onto the title line in the History stamp's green (double rule, ink that did not take everywhere, -6°, 320 ms from twice its size with a slight overshoot), and the sheet jolts 2 px under it. The stamp stays on the sheet as the record's mark - the same COMPLETE / ABANDONED stamp History uses, so an abandoned round gets the red one. One authored moment, ~0.6 s, no particles. Reduced motion: the stamp is simply there.",
    "v2": "The interviewer signs off. The red pen draws a loose ring around the average's scored cell (560 ms), ticks the title (240 ms), then a margin note in the interviewer's hand rises in - VERDICT: 'Solid round.' (banded by score: Strong / Solid / Keep at it - copy to confirm). The only variant that adds words; it is the interviewer's voice closing the packet, and it lasts ~1.4 s. Reduced motion: ring, tick and note are drawn already.",
    "v3": "The highlighter runs down the sheet. 'Round complete.' is painted left to right (380 ms, the 'Your turn' swipe), the pen runs cell to cell along the AVERAGE row and lands on the score with the stamp the live scorecard uses, then each topic's score lands in turn, 170 ms apart. Nothing new is added to the sheet: every motif already exists in the round, and the completion replays them once, top to bottom, in ~2.3 s. The quietest. Reduced motion: every mark is on from the start.",
    "v4": "The hole punch, if a shower is wanted after all. Seventy paper chads in the packet's own pigments (ink, highlighter, pen, orange, paper) drop from the sheet's top edge across its width and tumble off the bottom over ~2.4 s - today's confetti, but the packet's own scraps and CSS only (no react-confetti, no canvas, no resize listener). Reduced motion: nothing falls.",
}
DECISION = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Round complete · pick the moment</title>
<style>body{margin:0;background:#1f1f22;color:#cfcdc7;font:14px/1.5 Archivo,Helvetica Neue,Arial,sans-serif;padding:28px}h1{font-size:18px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;margin:0 0 6px}p{max-width:90ch;margin:0 0 18px}section{margin:0 0 44px}h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 8px}figure{margin:0 0 10px}img{max-width:100%;display:block;box-shadow:0 22px 44px rgba(0,0,0,.4)}a{color:#ffe94d}.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}iframe{width:100%;height:620px;border:0;background:#1f1f22;box-shadow:0 22px 44px rgba(0,0,0,.4)}.live{margin-top:12px}small{color:#8e8d89}</style></head><body>
<h1>Round complete · four moments to replace the confetti</h1>
<p>The same completed scorecard (three real topics from the review account: 8, 6, 5 - average 6.3) with four different first three seconds. Each row shows a recording of the moment in day and night stock, then the variant itself, live: press <b>Replay</b> on its desk bar to run it again, or open the .html at any width (add #night). Every variant honours prefers-reduced-motion by showing the end state without movement.</p>
{sections}
</body></html>"""
sections = "".join(
    f'<section><h2>{VARIANTS[s][0]}</h2><p>{NOTES[s]}</p><div class="row"><figure><img src="{s}-day.gif" alt="{s} day"></figure><figure><img src="{s}-night.gif" alt="{s} night"></figure></div><div class="live"><iframe src="{s}.html" title="{VARIANTS[s][0]} live" loading="lazy"></iframe></div><p><a href="{s}.html">{s}.html</a> · <a href="{s}.html#night">night</a></p></section>'
    for s in VARIANTS
)
(HERE / "decision.html").write_text(DECISION.replace("{sections}", sections))
print("wrote decision.html")
