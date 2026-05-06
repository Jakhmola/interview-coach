import streamlit as st
from ui import api_client, state

st.set_page_config(page_title="Interview — Interview Coach", page_icon="🎤")
st.title("Interview")

if not state.is_logged_in():
    st.warning("Please log in first.")
    st.page_link("app.py", label="Go to login")
    st.stop()

token = state.get_token()
assert token is not None

ROUND_LABELS = {
    "resume_walkthrough": "Resume / Project deep-dive",
    "behavioral_star": "Behavioral / STAR",
}


# --- Session picker / starter ---

if "interview_session_id" not in st.session_state:
    st.subheader("Start a new round")

    try:
        jobs = api_client.list_jobs(token)
    except api_client.ApiError as e:
        st.error(e.detail)
        jobs = []

    if not jobs:
        st.info("Save a JD on the **Setup** page first.")
        st.page_link("pages/Setup.py", label="Go to Setup")
        st.stop()

    job_choices = {
        f"{(j.get('source_url') or 'pasted')[:60]} — {j['created_at'][:19].replace('T', ' ')}": j[
            "id"
        ]
        for j in jobs
    }
    job_label = st.selectbox("Job description", options=list(job_choices.keys()))
    round_type = st.radio(
        "Round type",
        options=list(ROUND_LABELS.keys()),
        format_func=lambda r: ROUND_LABELS[r],
    )
    n_questions = st.slider("Number of questions", min_value=1, max_value=10, value=5)

    if st.button("Start interview", type="primary"):
        try:
            sess = api_client.create_session(
                token,
                job_id=job_choices[job_label],
                round_type=round_type,
                n_questions=n_questions,
            )
            st.session_state["interview_session_id"] = sess["id"]
            st.session_state["interview_round_type"] = sess["round_type"]
            st.rerun()
        except api_client.ApiError as e:
            # Common 400 codes: profile_missing, job_not_analyzed, company_snapshot_missing.
            hints = {
                "profile_missing": (
                    "Run ProfileBuilder first — your CV needs to be parsed into a profile. "
                    "(Phase 6 wires this from the Setup page; for now it's a backend step.)"
                ),
                "job_not_analyzed": (
                    "Run JobAnalyzer on this JD first. (Phase 6 wires this; backend step for now.)"
                ),
                "company_snapshot_missing": (
                    "Run CompanyResearcher for this JD first. "
                    "(Phase 7 wires this; backend step for now.)"
                ),
            }
            st.error(hints.get(e.detail, e.detail))

    st.stop()


# --- Active session view ---

session_id: str = st.session_state["interview_session_id"]
round_type: str = st.session_state.get("interview_round_type", "")

col1, col2 = st.columns([4, 1])
col1.caption(f"Session `{session_id[:8]}…` — {ROUND_LABELS.get(round_type, round_type)}")
if col2.button("End session"):
    try:
        api_client.abandon_session(token, session_id)
    except api_client.ApiError as e:
        st.error(e.detail)
    for k in ("interview_session_id", "interview_round_type", "interview_last_question_id"):
        st.session_state.pop(k, None)
    st.rerun()


try:
    detail = api_client.get_session_detail(token, session_id)
except api_client.ApiError as e:
    st.error(e.detail)
    st.stop()


# Render existing turns (so refreshes don't lose history).
for t in detail.get("turns", []):
    with st.chat_message("assistant"):
        st.write(t["question"])
    if t.get("answer"):
        with st.chat_message("user"):
            st.write(t["answer"])


turns = detail.get("turns", [])
n_questions = detail["n_questions"]
status = detail["status"]
need_new_question = (
    status == "active"
    and len(turns) < n_questions
    and (not turns or turns[-1].get("answer") is not None)
)

if need_new_question:
    if st.button("Next question", type="primary"):
        result = api_client.StreamResult()
        with st.chat_message("assistant"):
            try:
                st.write_stream(api_client.stream_next_question(token, session_id, result))
            except api_client.ApiError as e:
                st.error(e.detail)
                st.stop()
        if result.error:
            st.error(f"Generation failed: {result.error.get('code', 'unknown')}")
        elif result.done:
            st.session_state["interview_last_question_id"] = result.done.get("question_id")
        st.rerun()


# Phase 9 placeholder.
if turns and turns[-1].get("answer") is None:
    st.divider()
    st.caption("Answer flow lands in Phase 9.")
    st.text_area(
        "Your answer (disabled until Phase 9)",
        value="",
        disabled=True,
        height=120,
    )
elif status != "active":
    st.divider()
    st.info(f"Session **{status}**.")
elif len(turns) >= n_questions:
    st.divider()
    st.success(f"Reached {n_questions} questions. Mark as complete on Phase 9.")
