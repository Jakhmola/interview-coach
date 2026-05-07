import streamlit as st
from ui import api_client, state

st.set_page_config(page_title="Setup — Interview Coach", page_icon="📄")
st.title("Setup")

if not state.is_logged_in():
    st.warning("Please log in first.")
    st.page_link("app.py", label="Go to login")
    st.stop()

token = state.get_token()
assert token is not None  # guarded above; for type-checkers

KIND_LABELS = {"cv": "CV / Resume", "project_doc": "Project document"}
ACCEPT_TYPES = ["pdf", "docx"]


# --- Upload ---

st.subheader("Upload a document")
st.caption(
    "Supported: PDF, DOCX. Max 10 MB. "
    "Uploading a new CV replaces your existing CV. Project docs accumulate."
)

with st.form("upload_form", clear_on_submit=True):
    kind = st.selectbox(
        "Kind",
        options=list(KIND_LABELS.keys()),
        format_func=lambda k: KIND_LABELS[k],
    )
    uploaded = st.file_uploader("File", type=ACCEPT_TYPES, accept_multiple_files=False)
    submitted = st.form_submit_button("Upload")

if submitted:
    if uploaded is None:
        st.error("Pick a file first.")
    else:
        try:
            doc = api_client.upload_document(
                token,
                kind=kind,
                filename=uploaded.name,
                content_type=uploaded.type or "application/octet-stream",
                data=uploaded.getvalue(),
            )
            chars = doc.get("char_count", len(doc.get("raw_text", "")))
            if chars == 0:
                st.warning(
                    f"Uploaded **{doc['filename']}**, but extracted 0 characters of text. "
                    "Is this a scanned/image-only PDF? OCR is not supported in v1."
                )
            else:
                st.success(f"Uploaded **{doc['filename']}** — {chars:,} chars extracted.")
        except api_client.ApiError as e:
            st.error(e.detail)


# --- List ---

st.subheader("Your documents")

try:
    docs = api_client.list_documents(token)
except api_client.ApiError as e:
    st.error(e.detail)
    docs = []

if not docs:
    st.info("No documents yet.")
else:
    for d in docs:
        kind_label = KIND_LABELS.get(d["kind"], d["kind"])
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 1])
            cols[0].markdown(f"**{d['filename']}**  \n_{kind_label}_")
            cols[1].caption(f"{d['byte_size'] // 1024} KB")
            cols[2].caption(f"{d['char_count']:,} chars")
            if cols[3].button("Delete", key=f"del-{d['id']}"):
                try:
                    api_client.delete_document(token, d["id"])
                    st.rerun()
                except api_client.ApiError as e:
                    st.error(e.detail)

            with st.expander("Preview extracted text"):
                try:
                    full = api_client.get_document(token, d["id"])
                    text = full.get("raw_text", "")
                    preview = text[:2000]
                    st.text(preview if preview else "(no text extracted)")
                    if len(text) > 2000:
                        st.caption(f"... truncated; showing first 2000 of {len(text):,} chars")
                except api_client.ApiError as e:
                    st.error(e.detail)


# --- Job description ---

st.divider()
st.header("Job description")
st.caption(
    "Paste a JD or fetch it from a URL. URL fetch needs `TAVILY_API_KEY` set in `.env`. "
    "Multiple JDs are allowed; the most recent is used by the agents."
)

paste_tab, url_tab = st.tabs(["Paste text", "Fetch from URL"])

with paste_tab:
    with st.form("jd_paste_form", clear_on_submit=True):
        jd_text = st.text_area("JD text", height=240, key="jd_text_input")
        submitted = st.form_submit_button("Save")
    if submitted:
        if not jd_text.strip():
            st.error("Paste something first.")
        else:
            try:
                job = api_client.submit_job_text(token, jd_text)
                st.success(f"Saved JD ({job['char_count']:,} chars).")
            except api_client.ApiError as e:
                st.error(e.detail)

with url_tab:
    with st.form("jd_url_form", clear_on_submit=True):
        jd_url = st.text_input("JD URL (https://...)", key="jd_url_input")
        submitted = st.form_submit_button("Fetch and save")
    if submitted:
        u = jd_url.strip()
        if not u:
            st.error("Enter a URL first.")
        else:
            try:
                job = api_client.submit_job_url(token, u)
                st.success(f"Fetched and saved JD ({job['char_count']:,} chars).")
            except api_client.ApiError as e:
                st.error(e.detail)


st.subheader("Your JDs")

try:
    jobs = api_client.list_jobs(token)
except api_client.ApiError as e:
    st.error(e.detail)
    jobs = []

NODE_LABELS = {
    "profile_builder": "Profile builder",
    "job_analyzer": "Job analyzer",
    "company_researcher": "Company researcher",
}


def _run_prep(token: str, job_id: str, force_refresh: bool = False) -> None:
    """Drive the /sessions/prepare SSE stream and render per-node progress."""
    has_cv = any(d["kind"] == "cv" for d in docs)
    if not has_cv:
        st.error("Upload a CV first — profile_builder needs a document to read.")
        return

    with st.status("Preparing for interview…", expanded=True) as status:
        rows: dict[str, st.delta_generator.DeltaGenerator] = {n: st.empty() for n in NODE_LABELS}
        for n, label in NODE_LABELS.items():
            rows[n].markdown(f"⚪ **{label}** — pending")
        try:
            for frame in api_client.prepare_session(token, job_id, force_refresh=force_refresh):
                ev = frame["event"]
                data = frame["data"]
                node = data.get("node")
                if ev == "node_started" and node in rows:
                    rows[node].markdown(f"🟡 **{NODE_LABELS[node]}** — running…")
                elif ev == "node_done" and node in rows:
                    rows[node].markdown(f"🟢 **{NODE_LABELS[node]}** — done")
                elif ev == "node_skipped" and node in rows:
                    reason = data.get("reason", "cached")
                    rows[node].markdown(f"🔵 **{NODE_LABELS[node]}** — skipped ({reason})")
                elif ev == "error":
                    code = data.get("code", "error")
                    detail = data.get("detail", "")
                    if node in rows:
                        rows[node].markdown(f"🔴 **{NODE_LABELS[node]}** — {code}")
                    status.update(label=f"Failed: {code} — {detail}", state="error")
                    return
                elif ev == "done":
                    status.update(label="Ready — start interview", state="complete")
                    st.page_link("pages/Interview.py", label="Go to Interview →")
                    return
        except api_client.ApiError as e:
            status.update(label=f"Prep failed: {e.detail}", state="error")


if not jobs:
    st.info("No JDs yet.")
else:
    for j in jobs:
        with st.container(border=True):
            cols = st.columns([4, 2, 2, 1])
            label = f"[{j['source_url']}]({j['source_url']})" if j.get("source_url") else "_pasted_"
            cols[0].markdown(f"**{j['source']}**  \n{label}")
            cols[1].caption(f"{j['char_count']:,} chars")
            cols[2].caption(j["created_at"][:19].replace("T", " "))
            if cols[3].button("Delete", key=f"jdel-{j['id']}"):
                try:
                    api_client.delete_job(token, j["id"])
                    st.rerun()
                except api_client.ApiError as e:
                    st.error(e.detail)

            with st.expander("Preview"):
                preview = j.get("preview", "")
                st.text(preview if preview else "(empty)")
                if j["char_count"] > len(preview):
                    st.caption(f"... preview only; full text is {j['char_count']:,} chars")

            # Phase 10: prepare button — runs the prep graph end-to-end.
            prep_cols = st.columns([2, 2, 4])
            if prep_cols[0].button("Prepare for interview", key=f"prep-{j['id']}"):
                _run_prep(token, j["id"], force_refresh=False)
            if prep_cols[1].button(
                "Re-research",
                key=f"reprep-{j['id']}",
                help="Force company_researcher to re-run",
            ):
                _run_prep(token, j["id"], force_refresh=True)
