"""Generate the ready-landing variants as static HTML on the app's own
stylesheet (frontend/src/styles.css) with the review account's real data, so
they can be screenshotted and compared. `python3 gen.py` writes v1..v3.html
(+ decision.html). Open any variant with `#night` for the night stock."""
from pathlib import Path

HERE = Path(__file__).parent

ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>'
CARET = '<svg class="active-job-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>if (location.hash === "#night") document.documentElement.dataset.theme = "dark";</script>
<link rel="stylesheet" href="landing.css">
<style>
{extra}
</style>
</head>
<body>
<div class="desk">
  <div class="desk-bar">
    <nav class="tabs"><a class="tab active" href="#">Setup</a><a class="tab" href="#">Practice</a><a class="tab" href="#">History</a></nav>
    <div class="desk-tools"><span class="who">packet-review@gmail.com</span><button class="desk-btn"><span>Night</span></button><button class="desk-btn"><span>Log out</span></button></div>
  </div>
  <main class="sheet">
    <i class="staple a"></i><i class="staple b"></i>
    <div class="wizard">
      <header class="sheet-head"><h1>The packet</h1><span class="page">Prepped 28 Aug</span></header>
      <div class="fields">
        <div class="f"><span class="cap">Candidate</span><span class="v">packet-review@gmail.com</span></div>
        <div class="f wide"><span class="cap">Role / company</span><button class="active-job-pill"><span class="active-job-value"><span class="active-job-role">GenAI Engineer</span><span class="active-job-company">Northwind Labs</span></span>{caret}</button></div>
      </div>
"""

FOOT = """
    </div>
    <footer class="sheet-foot">Interview Coach · runs on your machine</footer>
  </main>
</div>
</body>
</html>
"""

START = f'<button class="btn-primary" type="button">Start a round {ARROW}</button>'
NEXT_CSS = """
.box.next { display: flex; align-items: center; justify-content: space-between; gap: 28px; padding: 26px 20px 20px; }
.box.next p { margin: 0; font-size: 21px; font-weight: 500; line-height: 1.35; text-wrap: pretty; }
.box.next .btn-primary { flex: none; }
@media (max-width: 720px) { .box.next { flex-direction: column; align-items: flex-start; } .box.next p { font-size: 17px; } }
"""
NEXT = f'''
<div class="box next"><span class="lbl">Next</span>
  <p>One topic at a time: the interviewer asks, follows up, then scores it and shows a model answer.</p>
  {START}
</div>
'''


# ---------------------------------------------------------------------------
# V1 - Stamped cover: the completed intake as five one-line rows, and a stamp.
V1_CSS = NEXT_CSS + """
.ready-actions { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; padding-top: 2px; }
.cover { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 22px 56px; align-items: center; border-top: 1px solid var(--ink); padding-top: 4px; }
.intake { list-style: none; margin: 0; padding: 0; display: grid; }
.intake li { display: grid; grid-template-columns: 12px 140px minmax(0, 1fr); gap: 14px; align-items: center; padding: 9px 0; border-bottom: 1px solid var(--rule); font-size: 14.5px; }
.intake li i { width: 12px; height: 12px; border: 1.5px solid var(--ink); display: inline-block; }
.intake li.done i { background: var(--ink); box-shadow: inset 0 0 0 2px var(--paper); }
.intake .k { font-weight: 700; font-size: 10.5px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); }
.intake .v { font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.intake .v em { font-style: normal; font-weight: 400; color: var(--ink-2); font-size: 13px; }
.intake li:not(.done) .v { color: var(--ink-3); font-weight: 400; }
.stamp { justify-self: end; margin-right: 40px; padding: 10px 18px 12px; border: 2px solid var(--ok); box-shadow: inset 0 0 0 3px var(--paper), inset 0 0 0 4px var(--ok); color: var(--ok); text-transform: uppercase; letter-spacing: 0.16em; font-weight: 800; text-align: center; transform: rotate(-7deg); opacity: 0.9; }
.stamp b { display: block; font-size: 30px; line-height: 1; letter-spacing: 0.12em; }
.stamp span { display: block; font-size: 10px; margin-top: 6px; }
@media (max-width: 720px) { .cover { grid-template-columns: minmax(0, 1fr); } .stamp { justify-self: start; margin: 8px 0 0 12px; } }
"""
V1 = f"""
{NEXT}
<div class="cover">
  <ul class="intake" aria-label="The packet">
    <li class="done"><i></i><span class="k">CV</span><span class="v">cv-synthetic.docx</span></li>
    <li class="done"><i></i><span class="k">Job description</span><span class="v">GenAI Engineer @ Northwind Labs <em>· pasted 28 Aug</em></span></li>
    <li class="done"><i></i><span class="k">Supporting docs</span><span class="v">project-doc-synthetic.docx</span></li>
    <li><i></i><span class="k">GitHub repos</span><span class="v">none</span></li>
    <li class="done"><i></i><span class="k">Prep</span><span class="v">Profile built · JD analysed · company researched <em>· 28 Aug</em></span></li>
  </ul>
  <div class="stamp" aria-hidden="true"><b>Ready</b><span>to practice · 28 Aug</span></div>
</div>
<div class="ready-actions"><a class="btn-quiet" href="#">Manage CV, JDs &amp; docs</a></div>
"""

# ---------------------------------------------------------------------------
# V2 - Brief box: the role in one boxed field; the inventory as one line.
V2_CSS = NEXT_CSS + """
.ready-actions { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; padding-top: 2px; }
.ready-onfile { display: inline-flex; align-items: center; gap: 12px; font-size: 13px; color: var(--ink-2); }
.ready-onfile .btn-quiet { height: 24px; padding: 0; }
.box.brief { display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; }
.box.brief .lede { font-size: 16px; font-weight: 500; line-height: 1.4; }
.box.brief .row { display: grid; grid-template-columns: 104px minmax(0, 1fr); gap: 12px; align-items: baseline; font-size: 14px; line-height: 1.5; color: var(--ink); }
.box.brief .k { font-weight: 700; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); }
.box.brief .row p { margin: 0; }
.box.brief .sep { color: var(--ink-3); }
@media (max-width: 720px) { .ready-onfile { margin-left: 0; } .box.brief .row { grid-template-columns: minmax(0, 1fr); gap: 2px; } }
"""
SEP = ' <span class="sep">·</span> '
V2 = f"""
{NEXT}
<div class="ready-actions"><span class="ready-onfile"><span>Grounded in cv-synthetic.docx, project-doc-synthetic.docx and the JD</span><a class="btn-quiet" href="#">Manage</a></span></div>
<div class="box brief"><span class="lbl">Role brief</span>
  <p class="lede">Senior GenAI Engineer at Northwind Labs, which builds decision-support tools for logistics operators.</p>
  <div class="row"><span class="k">Must have</span><p>4+ years in ML/backend engineering, 1+ shipping LLM features to production{SEP}Strong Python; experience with FastAPI, Postgres, vector search (pgvector or similar){SEP}Hands-on with RAG, prompt engineering, and LLM evaluation{SEP}Clear written communication; you document decisions</p></div>
  <div class="row"><span class="k">Nice to have</span><p>Experience with agent frameworks (LangGraph or similar){SEP}Observability (Langfuse){SEP}GPU inference tuning</p></div>
  <div class="row"><span class="k">Looks for</span><p>cross-team communication{SEP}ownership{SEP}clear communication{SEP}stakeholder management</p></div>
</div>
"""

# ---------------------------------------------------------------------------
# V3 - Bare cover: the NEXT box and one grounding line; the rest is paper.
V3_CSS = NEXT_CSS + """
.ready-actions { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; padding: 2px 0 0 20px; }
.ready-onfile { display: inline-flex; align-items: center; gap: 12px; font-size: 13.5px; color: var(--ink-2); }
.ready-onfile .btn-quiet { height: 24px; padding: 0; }
"""
V3 = f"""
{NEXT}
<div class="ready-actions"><span class="ready-onfile"><span>Grounded in cv-synthetic.docx, project-doc-synthetic.docx and the Northwind Labs JD · no repos yet</span><a class="btn-quiet" href="#">Manage</a></span></div>
"""

VARIANTS = {
    "v1": ("V1 · Stamped cover", V1_CSS, V1),
    "v2": ("V2 · Brief box", V2_CSS, V2),
    "v3": ("V3 · Bare cover", V3_CSS, V3),
}

# The app's stylesheet, with its self-hosted faces pointed at
# frontend/public/fonts so a file:// mock renders in Archivo / Courier Prime.
# Regenerated on every run and gitignored - the source of truth stays in
# frontend/src/styles.css.
app_css = (HERE / "../../../frontend/src/styles.css").read_text()
(HERE / "landing.css").write_text(app_css.replace('url("/fonts/', 'url("../../../frontend/public/fonts/'))

for slug, (title, css, body) in VARIANTS.items():
    (HERE / f"{slug}.html").write_text(HEAD.format(title=title, extra=css, caret=CARET) + body + FOOT)
    print("wrote", slug + ".html")

NOTES = {
    "v1": "Under the NEXT box: the completed intake as five one-line rows (CV, JD, docs, repos, prep) and a READY stamp. Says what is in the packet; nothing about the role. ~6 lines.",
    "v2": "Under the NEXT box: a grounding line, then the role in one boxed field - one sentence (seniority, title, company, what it does), must-haves, nice-to-haves and what the interviewer looks for as inline runs. ~7 lines.",
    "v3": "The NEXT box and one grounding line. The rest of the cover is blank paper on purpose. 1 line.",
}
DECISION = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Ready landing · pick one</title>
<style>body{margin:0;background:#1f1f22;color:#cfcdc7;font:14px/1.5 Archivo,Helvetica Neue,Arial,sans-serif;padding:28px}h1{font-size:18px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;margin:0 0 6px}p{max-width:80ch;margin:0 0 18px}section{margin:0 0 36px}h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 8px}figure{margin:0 0 10px}img{max-width:100%;display:block;box-shadow:0 22px 44px rgba(0,0,0,.4)}a{color:#ffe94d}.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}</style></head><body>
<h1>Ready landing · three versions</h1>
<p>Same header (THE PACKET · Prepped 28 Aug), same NEXT box holding the one action, same Manage link. They differ only in what sits under the box - a density dial from V3 (one line) to V1 (six) to V2 (seven). Each is rendered on the app's stylesheet with the review account's real data; day stock left, night stock right. Open the .html next to this file to see a variant live at any width (add #night for night).</p>
{sections}
</body></html>"""
sections = "".join(
    f'<section><h2>{VARIANTS[s][0]}</h2><p>{NOTES[s]}</p><div class="row"><figure><img src="{s}-day.png" alt="{s} day"></figure><figure><img src="{s}-night.png" alt="{s} night"></figure></div><p><a href="{s}.html">{s}.html</a> · <a href="{s}.html#night">night</a></p></section>'
    for s in VARIANTS
)
(HERE / "decision.html").write_text(DECISION.replace("{sections}", sections))
print("wrote decision.html")
