"""Generate the Manage (file inventory) variants as static HTML on the app's
own stylesheet (frontend/src/styles.css) with the review account's real files,
so they can be screenshotted and compared. `python3 gen.py` writes v1..v4.html
(+ decision.html). Open any variant with `#night` for the night stock.

All four share one distilled chrome (title, tally, one back link, no feature
tour, no permanent warning, no "ready" pills); they differ only in how a file
is previewed. The repo is synthetic - the review account has none."""
import html
from pathlib import Path

HERE = Path(__file__).parent

# ---------------------------------------------------------------------------
# The review account's files, verbatim from GET /documents/{id} and /jobs/{id}.
CV = """Priya Raman
GenAI Engineer · Bengaluru · priya.raman@example.com · github.com/priyaraman (synthetic CV for UI review)
Experience
Bobble AI - Senior Machine Learning Engineer (2022 - present)
Designed a modular prompt system for a keyboard assistant: reusable tone, guideline and output-format blocks; 70% of prompt structure shared across features.
Built an LLM-powered conversational agent with retrieval over product docs; cut escalation rate 30% and doubled campaign CTR during seasonal launches.
Introduced parallel generation across three prompt variants per request, reducing perceived latency to a single LLM call; owned the evaluation harness (BLEU, human preference, guardrail checks).
Aurora Analytics - Data Scientist (2019 - 2022)
Shipped a churn model (XGBoost) feeding a retention dashboard used by 40 account managers.
Migrated batch feature pipelines to Airflow; p95 pipeline latency down 55%.
Skills
Python, PyTorch, LangChain, FastAPI, Postgres, pgvector, Docker, prompt engineering, RAG, evaluation, Airflow, AWS
Education
M.Tech Computer Science, IIT Madras, 2019"""

JD = """GenAI Engineer - Northwind Labs (Bengaluru, hybrid)

Northwind Labs builds decision-support tools for logistics operators. We are hiring a GenAI Engineer to own our LLM features end to end.

What you will do
- Design and ship retrieval-augmented assistants over operational documents (SOPs, incident logs, contracts).
- Build evaluation harnesses: offline metrics, human preference loops, guardrails and red-teaming.
- Own prompt systems and model routing across hosted and self-hosted models (llama.cpp, vLLM).
- Optimise latency and cost: batching, caching, parallel generation, streaming.
- Work with product and ops to turn ambiguous workflows into reliable agents.

Must have
- 4+ years in ML/backend engineering, 1+ shipping LLM features to production.
- Strong Python; experience with FastAPI, Postgres, vector search (pgvector or similar).
- Hands-on with RAG, prompt engineering, and LLM evaluation.
- Clear written communication; you document decisions.

Nice to have
- Experience with agent frameworks (LangGraph or similar), observability (Langfuse), and GPU inference tuning."""

DOC = """LLM-Powered Conversational Agent - Bobble AI (design note)
Synthetic project write-up for UI review. Architecture note for the retrieval-augmented keyboard assistant shipped at Bobble AI in 2023.
Retrieval
Product docs were chunked at 400-600 tokens with 15% overlap and embedded with a Jina model into pgvector. Queries ran hybrid BM25 plus vector search fused with reciprocal rank fusion; a metadata filter restricted search to the user locale and product surface. Nightly re-embedding was replaced by incremental embedding keyed on document hash, cutting embedding cost by roughly 80%.
Generation and prompts
Prompts were assembled from versioned blocks (tone, guidelines, output format). Three variants were generated in parallel under a semaphore of three and a per-request token budget; the fastest well-formed response was returned, taking p95 latency from 4.2 s to 1.6 s.
Evaluation and guardrails
An offline set of 1,200 labelled prompts scored relevance and format compliance; a weekly panel of five raters scored preference. Guardrails combined regex filters with a small unsafe-content classifier; failures were logged to Langfuse with the prompt version. Escalation rate fell 30% and campaign CTR doubled during the seasonal launch."""

# Synthetic: the review account has no repos. Shaped like a folded ProjectItem.
REPO = {
    "title": "keyboard-rag",
    "url": "github.com/priyaraman/keyboard-rag",
    "tech": ["Python", "FastAPI", "pgvector", "Jina", "Langfuse"],
    "features": [
        "Hybrid BM25 + vector retrieval fused with reciprocal rank fusion",
        "Incremental embedding keyed on document hash",
        "Three prompt variants generated in parallel under a semaphore",
        "Offline eval set of 1,200 labelled prompts",
    ],
    "readme": """# keyboard-rag
Retrieval-augmented assistant for a mobile keyboard.

## What it does
- Chunks product docs (400-600 tokens, 15% overlap) into pgvector
- Hybrid BM25 + vector search, fused with RRF
- Three prompt variants in parallel; fastest well-formed wins
- Offline eval: 1,200 labelled prompts, weekly rater panel

## Stack
Python · FastAPI · pgvector · Jina embeddings · Langfuse""",
}

# ---------------------------------------------------------------------------
# Icons (lucide paths, 14px) so the mocks carry the app's own glyphs.
def icon(paths: str, size: int = 14) -> str:
    return f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'

I_BACK = icon('<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>')
I_TRASH = icon('<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>')
I_UP = icon('<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M12 12v6"/><path d="m15 15-3-3-3 3"/>')
I_SPARK = icon('<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>')
I_PLUS = icon('<path d="M5 12h14"/><path d="M12 5v14"/>')
I_EXT = icon('<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>', 12)

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<script>if (location.hash === "#night") document.documentElement.dataset.theme = "dark";</script>
<link rel="stylesheet" href="manage.css">
<style>
/* chrome shared by every variant */
.manage-page { display: grid; gap: 22px; }
.back { justify-self: start; height: 24px; padding: 0; margin-top: -8px; }
.manage-section { display: grid; gap: 10px; border-top: 1px solid var(--ink); padding-top: 10px; }
.manage-section h2 { font-size: 11px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); margin: 0; }
.manage-section-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.actions { display: grid; gap: 4px; justify-items: end; flex: none; }
.actions .row { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.actions .consequence { font-size: 12px; color: var(--ink-2); text-align: right; max-width: 30ch; }
.meta { font-size: 12.5px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.danger .about { max-width: 70ch; }
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
    <div class="manage-page">
      <header class="sheet-head"><h1>On file</h1><span class="page">4 of 4 in the packet</span></header>
      <div class="fields">
        <div class="f"><span class="cap">Candidate</span><span class="v">packet-review@gmail.com</span></div>
      </div>
      <a class="btn-quiet back" href="#">{back}Back to the packet</a>
"""

FOOT = """
    </div>
    <footer class="sheet-foot">Interview Coach · runs on your machine</footer>
  </main>
</div>
</body>
</html>
"""

DANGER = f"""
<section class="manage-section danger">
  <h2>Danger zone</h2>
  <div class="file plain">
    <div class="about"><strong>Reset account</strong><span class="meta">Deletes every file, job description, session and the profile. Your login stays.</span></div>
    <div class="actions"><div class="row"><button class="btn-ghost">{I_TRASH} Reset account…</button></div></div>
  </div>
</section>
"""

ACT_CV = f'<div class="actions"><div class="row"><button class="btn-ghost">{I_UP} Replace CV</button></div><span class="consequence">Rebuilds the profile and remaps 1 doc.</span></div>'
ACT_JD = f'<div class="actions"><div class="row"><button class="btn-ghost">{I_SPARK} Re-analyze</button><button class="btn-ghost">{I_TRASH} Delete</button></div></div>'
ACT_DOC = f'<div class="actions"><div class="row"><button class="btn-ghost">{I_SPARK} Remap</button><button class="btn-ghost">{I_TRASH} Delete</button></div></div>'
ACT_REPO = f'<div class="actions"><div class="row"><button class="btn-ghost">{I_TRASH} Delete</button></div></div>'
REPOS_HEAD = f'<div class="manage-section-head"><h2>GitHub repos</h2><button class="btn-ghost">{I_PLUS} Add / manage repos</button></div>'
ACTIVE = '<span class="status-pill status-good">Active</span>'


def esc(s: str) -> str:
    return html.escape(s)


def thumb(text: str, kind: str = "") -> str:
    """A paper slip: the file's real first lines, first line bold, faded out."""
    first, _, rest = text.partition("\n")
    return f'<button class="thumb {kind}" type="button" aria-label="Open the full text"><b>{esc(first)}</b>{esc(rest)}</button>'


# ---------------------------------------------------------------------------
# V1 - Slips: each file is a paper slip in the packet. The slip shows the real
# first lines of the file at reading size (typed face), the name and the
# provenance sit beside it, and the slip itself opens the full text.
V1_CSS = """
.file { display: grid; grid-template-columns: 160px minmax(0, 1fr) auto; gap: 0 22px; align-items: start; padding: 12px 0; border-bottom: 1px solid var(--rule); }
.file.plain { grid-template-columns: minmax(0, 1fr) auto; }
.file .about { display: grid; gap: 6px; padding-top: 2px; align-content: start; }
.file .about strong { font-weight: 500; font-size: 15px; }
.file .about .btn-quiet { justify-self: start; height: 22px; padding: 0; margin-top: 2px; }
.thumb { display: block; width: 160px; height: 200px; padding: 12px 11px; text-align: left; background: var(--paper); border: 1px solid var(--rule-2); box-shadow: 0 5px 14px rgba(0, 0, 0, 0.16); overflow: hidden; position: relative; font-family: var(--font-typed); font-size: 8.5px; line-height: 1.32; color: var(--ink-2); white-space: pre-wrap; overflow-wrap: anywhere; cursor: zoom-in; transition: transform 160ms cubic-bezier(0.2, 0.7, 0.2, 1), box-shadow 160ms; }
.thumb b { display: block; color: var(--ink); font-weight: 700; margin-bottom: 2px; }
.thumb::after { content: ""; position: absolute; inset: auto 0 0 0; height: 48px; background: linear-gradient(transparent, var(--paper)); }
.thumb:hover { transform: translateY(-2px) rotate(-0.6deg); box-shadow: 0 10px 22px rgba(0, 0, 0, 0.2); }
.thumb.readme { font-size: 9px; }
@media (max-width: 720px) { .file { grid-template-columns: 120px minmax(0, 1fr); } .file .actions { grid-column: 1 / -1; justify-items: start; } .thumb { width: 120px; height: 150px; font-size: 7px; } }
"""
V1 = f"""
<section class="manage-section">
  <h2>CV</h2>
  <div class="file">
    {thumb(CV)}
    <div class="about"><strong>cv-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,083 chars</span><a class="btn-quiet" href="#">Read the full text</a></div>
    {ACT_CV}
  </div>
</section>
<section class="manage-section">
  <h2>Job descriptions</h2>
  <div class="file">
    {thumb(JD)}
    <div class="about"><strong>GenAI Engineer @ Northwind Labs</strong><span class="meta">Pasted 28 Aug · 1,088 chars {ACTIVE}</span><a class="btn-quiet" href="#">Read the full text</a></div>
    {ACT_JD}
  </div>
</section>
<section class="manage-section">
  <h2>Supporting docs</h2>
  <div class="file">
    {thumb(DOC)}
    <div class="about"><strong>project-doc-synthetic.docx</strong><span class="meta">Filed under “LLM-Powered Conversational Agent” at Bobble AI · 1,245 chars</span><a class="btn-quiet" href="#">Read the full text</a></div>
    {ACT_DOC}
  </div>
</section>
<section class="manage-section">
  {REPOS_HEAD}
  <div class="file">
    {thumb(REPO["readme"], "readme")}
    <div class="about"><strong>{REPO["title"]}</strong><a class="repo-link" href="#">{I_EXT} {REPO["url"]}</a><div class="repo-chips">{"".join(f'<span class="repo-chip">{t}</span>' for t in REPO["tech"])}</div></div>
    {ACT_REPO}
  </div>
</section>
{DANGER}
"""

# ---------------------------------------------------------------------------
# V2 - Digest: no thumbnail; under each name, what the interviewer read out of
# the file (the profile it built, the brief it parsed, the section headings,
# the repo's features) as key/value rows, plus a link to the full text.
V2_CSS = """
.file { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 22px; align-items: start; padding: 12px 0; border-bottom: 1px solid var(--rule); }
.file .about { display: grid; gap: 6px; align-content: start; min-width: 0; }
.file .about strong { font-weight: 500; font-size: 15px; }
.digest { display: grid; gap: 4px; margin-top: 4px; max-width: 92ch; }
.digest .row { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; align-items: baseline; font-size: 13.5px; line-height: 1.5; }
.digest .k { font-weight: 700; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); }
.digest .row p { margin: 0; }
.digest .row p.q { color: var(--ink-2); }
.digest .repo-chips { margin-top: 2px; }
.file .about .btn-quiet { justify-self: start; height: 22px; padding: 0; margin-top: 2px; }
@media (max-width: 720px) { .file { grid-template-columns: minmax(0, 1fr); } .file .actions { justify-items: start; } .digest .row { grid-template-columns: minmax(0, 1fr); gap: 2px; } }
"""
SEP = " · "
V2 = f"""
<section class="manage-section">
  <h2>CV</h2>
  <div class="file">
    <div class="about">
      <strong>cv-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,083 chars</span>
      <div class="digest">
        <div class="row"><span class="k">Candidate</span><p>Priya Raman · GenAI Engineer · Bengaluru</p></div>
        <div class="row"><span class="k">Roles</span><p>Senior Machine Learning Engineer, Bobble AI (2022 - present){SEP}Data Scientist, Aurora Analytics (2019 - 2022)</p></div>
        <div class="row"><span class="k">Skills</span><p>Python{SEP}PyTorch{SEP}LangChain{SEP}FastAPI{SEP}Postgres{SEP}pgvector{SEP}Docker{SEP}prompt engineering{SEP}RAG{SEP}evaluation{SEP}Airflow{SEP}AWS</p></div>
        <div class="row"><span class="k">Education</span><p>M.Tech Computer Science, IIT Madras, 2019</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_CV}
  </div>
</section>
<section class="manage-section">
  <h2>Job descriptions</h2>
  <div class="file">
    <div class="about">
      <strong>GenAI Engineer @ Northwind Labs</strong><span class="meta">Pasted 28 Aug · 1,088 chars {ACTIVE}</span>
      <div class="digest">
        <div class="row"><span class="k">Opens</span><p class="q">“Northwind Labs builds decision-support tools for logistics operators. We are hiring a GenAI Engineer to own our LLM features end to end.”</p></div>
        <div class="row"><span class="k">Read as</span><p>Senior · Bengaluru, hybrid · 5 responsibilities · 4 must-haves · 3 nice-to-haves</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_JD}
  </div>
</section>
<section class="manage-section">
  <h2>Supporting docs</h2>
  <div class="file">
    <div class="about">
      <strong>project-doc-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,245 chars</span>
      <div class="digest">
        <div class="row"><span class="k">Opens</span><p class="q">“LLM-Powered Conversational Agent - Bobble AI (design note)”</p></div>
        <div class="row"><span class="k">Sections</span><p>Retrieval{SEP}Generation and prompts{SEP}Evaluation and guardrails</p></div>
        <div class="row"><span class="k">Filed under</span><p>Bobble AI · “Built an LLM-powered conversational agent with retrieval over product docs…”</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_DOC}
  </div>
</section>
<section class="manage-section">
  {REPOS_HEAD}
  <div class="file">
    <div class="about">
      <strong>{REPO["title"]}</strong><a class="repo-link" href="#">{I_EXT} {REPO["url"]}</a>
      <div class="digest">
        <div class="row"><span class="k">Tech</span><div class="repo-chips">{"".join(f'<span class="repo-chip">{t}</span>' for t in REPO["tech"])}</div></div>
        <div class="row"><span class="k">Does</span><p>{SEP.join(REPO["features"])}</p></div>
      </div>
    </div>
    {ACT_REPO}
  </div>
</section>
{DANGER}
"""

# ---------------------------------------------------------------------------
# V3 - Reading pane: the files as an index on the left (selected row in
# highlighter), the selected file's full text on the right in a boxed field,
# with its actions in the box head. One file open at a time; nothing to click
# through to.
V3_CSS = """
.cabinet { display: grid; grid-template-columns: minmax(260px, 340px) minmax(0, 1fr); gap: 0 36px; align-items: start; }
.index { display: grid; gap: 16px; }
.index .manage-section { gap: 4px; }
.index .entry { display: grid; gap: 2px; padding: 8px 10px; margin: 0 -10px; border-bottom: 1px solid var(--rule); cursor: pointer; }
.index .entry:hover { background: var(--paper-2); }
.index .entry.on { background: var(--hl); color: #1b1b1f; }
.index .entry.on .meta { color: #1b1b1f; opacity: 0.75; }
.index .entry strong { font-weight: 500; font-size: 14.5px; }
.index .entry .meta { font-size: 12px; }
.index .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.index .head .btn-quiet { height: 22px; padding: 0; }
.pane { position: relative; border: 1px solid var(--ink); min-height: 420px; }
.pane .lbl { position: absolute; top: -1px; left: -1px; padding: 3px 8px; background: var(--ink); color: var(--paper); font-size: 10px; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; }
.pane-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 26px 18px 12px; }
.pane-head .about { display: grid; gap: 4px; }
.pane-head strong { font-weight: 500; font-size: 15px; }
.pane-text { margin: 0; padding: 4px 18px 20px; font-family: var(--font-typed); font-size: 13px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--ink); max-height: 560px; overflow: auto; }
.file.plain { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0 22px; align-items: start; padding: 12px 0; }
.file .about { display: grid; gap: 6px; }
.file .about strong { font-weight: 500; font-size: 15px; }
@media (max-width: 980px) { .cabinet { grid-template-columns: minmax(0, 1fr); gap: 20px; } }
"""
V3 = f"""
<div class="cabinet">
  <nav class="index" aria-label="Files">
    <section class="manage-section"><h2>CV</h2>
      <div class="entry on"><strong>cv-synthetic.docx</strong><span class="meta">Priya Raman · 1,083 chars · 27 Aug</span></div>
    </section>
    <section class="manage-section"><h2>Job descriptions</h2>
      <div class="entry"><strong>GenAI Engineer @ Northwind Labs</strong><span class="meta">Pasted 28 Aug · 1,088 chars {ACTIVE}</span></div>
    </section>
    <section class="manage-section"><h2>Supporting docs</h2>
      <div class="entry"><strong>project-doc-synthetic.docx</strong><span class="meta">LLM-Powered Conversational Agent · 1,245 chars</span></div>
    </section>
    <section class="manage-section"><div class="head"><h2>GitHub repos</h2><a class="btn-quiet" href="#">{I_PLUS} Add</a></div>
      <div class="entry"><strong>{REPO["title"]}</strong><span class="meta">{REPO["url"]}</span></div>
    </section>
  </nav>
  <div class="pane"><span class="lbl">CV</span>
    <div class="pane-head">
      <div class="about"><strong>cv-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,083 chars · the profile was built from this</span></div>
      {ACT_CV}
    </div>
    <pre class="pane-text">{esc(CV)}</pre>
  </div>
</div>
{DANGER}
"""

# ---------------------------------------------------------------------------
# V4 - Slip + digest: V1's paper slip with V2's digest beside it, so the row
# carries both how the file looks and what the interviewer read out of it.
V4_CSS = V1_CSS + """
.digest { display: grid; gap: 4px; margin-top: 2px; max-width: 80ch; }
.digest .row { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; align-items: baseline; font-size: 13.5px; line-height: 1.5; }
.digest .k { font-weight: 700; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--ink-2); }
.digest .row p { margin: 0; }
.digest .row p.q { color: var(--ink-2); }
@media (max-width: 720px) { .digest .row { grid-template-columns: minmax(0, 1fr); gap: 2px; } }
"""
V4 = f"""
<section class="manage-section">
  <h2>CV</h2>
  <div class="file">
    {thumb(CV)}
    <div class="about">
      <strong>cv-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,083 chars</span>
      <div class="digest">
        <div class="row"><span class="k">Candidate</span><p>Priya Raman · GenAI Engineer · Bengaluru</p></div>
        <div class="row"><span class="k">Roles</span><p>Senior Machine Learning Engineer, Bobble AI (2022 - present){SEP}Data Scientist, Aurora Analytics (2019 - 2022)</p></div>
        <div class="row"><span class="k">Skills</span><p>Python{SEP}PyTorch{SEP}LangChain{SEP}FastAPI{SEP}Postgres{SEP}pgvector{SEP}Docker{SEP}prompt engineering{SEP}RAG{SEP}evaluation{SEP}Airflow{SEP}AWS</p></div>
        <div class="row"><span class="k">Education</span><p>M.Tech Computer Science, IIT Madras, 2019</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_CV}
  </div>
</section>
<section class="manage-section">
  <h2>Job descriptions</h2>
  <div class="file">
    {thumb(JD)}
    <div class="about">
      <strong>GenAI Engineer @ Northwind Labs</strong><span class="meta">Pasted 28 Aug · 1,088 chars {ACTIVE}</span>
      <div class="digest">
        <div class="row"><span class="k">Opens</span><p class="q">“Northwind Labs builds decision-support tools for logistics operators. We are hiring a GenAI Engineer to own our LLM features end to end.”</p></div>
        <div class="row"><span class="k">Read as</span><p>Senior · Bengaluru, hybrid · 5 responsibilities · 4 must-haves · 3 nice-to-haves</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_JD}
  </div>
</section>
<section class="manage-section">
  <h2>Supporting docs</h2>
  <div class="file">
    {thumb(DOC)}
    <div class="about">
      <strong>project-doc-synthetic.docx</strong><span class="meta">Uploaded 27 Aug · 1,245 chars</span>
      <div class="digest">
        <div class="row"><span class="k">Sections</span><p>Retrieval{SEP}Generation and prompts{SEP}Evaluation and guardrails</p></div>
        <div class="row"><span class="k">Filed under</span><p>Bobble AI · “Built an LLM-powered conversational agent with retrieval over product docs…”</p></div>
      </div>
      <a class="btn-quiet" href="#">Read the full text</a>
    </div>
    {ACT_DOC}
  </div>
</section>
<section class="manage-section">
  {REPOS_HEAD}
  <div class="file">
    {thumb(REPO["readme"], "readme")}
    <div class="about">
      <strong>{REPO["title"]}</strong><a class="repo-link" href="#">{I_EXT} {REPO["url"]}</a>
      <div class="digest">
        <div class="row"><span class="k">Tech</span><div class="repo-chips">{"".join(f'<span class="repo-chip">{t}</span>' for t in REPO["tech"])}</div></div>
        <div class="row"><span class="k">Does</span><p>{SEP.join(REPO["features"])}</p></div>
      </div>
    </div>
    {ACT_REPO}
  </div>
</section>
{DANGER}
"""

VARIANTS = {
    "v4": ("V4 · Slip + digest", V4_CSS, V4),
    "v1": ("V1 · Slips", V1_CSS, V1),
    "v2": ("V2 · Digest", V2_CSS, V2),
    "v3": ("V3 · Reading pane", V3_CSS, V3),
}

# The app's stylesheet with its self-hosted faces pointed at
# frontend/public/fonts so a file:// mock renders in Archivo / Courier Prime.
# Regenerated on every run and gitignored - the source of truth stays in
# frontend/src/styles.css.
app_css = (HERE / "../../../frontend/src/styles.css").read_text()
(HERE / "manage.css").write_text(app_css.replace('url("/fonts/', 'url("../../../frontend/public/fonts/'))

for slug, (title, css, body) in VARIANTS.items():
    page = HEAD.replace("{title}", title).replace("{extra}", css).replace("{back}", I_BACK) + body + FOOT
    (HERE / f"{slug}.html").write_text(page)
    print("wrote", slug + ".html")

NOTES = {
    "v4": "V1's paper slip with V2's digest beside it: the row shows how the file looks and what the interviewer read out of it, and the slip's height is used instead of left empty. Both previews in one row, the longest rows of the four.",
    "v1": "Every file is a paper slip in the packet: its real first lines in the typed face at 8.5px (the CV's name and first role, the JD's opening, the doc's title and sections, the repo's README), the name and provenance beside it, the actions to the right. The slip opens the full text. The most 'packet' of the three and the most compact.",
    "v2": "No thumbnails. Under each name, what the interviewer read out of the file, as key/value rows: the CV as the profile it built (candidate, roles, skills, education), the JD as its opening line plus how it was read, the doc as its opening, its section headings and the highlight it was filed under, the repo as tech and features. A link to the full text. The most informative; the JD row overlaps the cover's brief a little.",
    "v3": "A reading pane: the files as an index on the left (selected row in highlighter), the selected file's full text on the right in a boxed field with its actions in the head. Nothing to click through to - what is loaded is on the page. The heaviest, and it makes Manage a place to read rather than a list to act on.",
}
DECISION = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Manage · pick one</title>
<style>body{margin:0;background:#1f1f22;color:#cfcdc7;font:14px/1.5 Archivo,Helvetica Neue,Arial,sans-serif;padding:28px}h1{font-size:18px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;margin:0 0 6px}p{max-width:90ch;margin:0 0 18px}section{margin:0 0 36px}h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;margin:0 0 8px}figure{margin:0 0 10px}img{max-width:100%;display:block;box-shadow:0 22px 44px rgba(0,0,0,.4)}a{color:#ffe94d}.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}ul{max-width:90ch}</style></head><body>
<h1>Manage · four ways to preview a file</h1>
<p>All four share one distilled chrome, so the choice is only about the previews. What the chrome drops from today's page: the second title ("Everything on file for this account.") and the feature-tour paragraph, the permanent red CV warning (its consequence now sits under the Replace CV button, in ink, not in the interviewer's red pen), the "Embeddings ready" pill on every healthy row (pills only appear when something is pending, failed or unmapped), and the "Reveal reset" label. What it keeps: the section headings, the actions, one quiet way back to the packet, and the tally in the page meta ("4 of 4 in the packet", matching the cover). Rendered on the app's stylesheet with the review account's real files; the repo is synthetic because the review account has none. Day stock left, night right. Open the .html next to this file to see a variant live at any width (add #night for night).</p>
{sections}
</body></html>"""
sections = "".join(
    f'<section><h2>{VARIANTS[s][0]}</h2><p>{NOTES[s]}</p><div class="row"><figure><img src="{s}-day.png" alt="{s} day"></figure><figure><img src="{s}-night.png" alt="{s} night"></figure></div><p><a href="{s}.html">{s}.html</a> · <a href="{s}.html#night">night</a></p></section>'
    for s in VARIANTS
)
(HERE / "decision.html").write_text(DECISION.replace("{sections}", sections))
print("wrote decision.html")
