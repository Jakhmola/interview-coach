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
    # Resume offer: any active server-side sessions for this user?
    try:
        all_sessions = api_client.list_sessions(token)
    except api_client.ApiError as e:
        st.error(e.detail)
        all_sessions = []
    active_sessions = [s for s in all_sessions if s.get("status") == "active"]

    if active_sessions:
        st.subheader("Resume an active session")
        st.caption(
            "You have unfinished interview(s). Pick one to continue, or start a new round below."
        )
        for s in active_sessions:
            sid = s["id"]
            label = (
                f"**{ROUND_LABELS.get(s['round_type'], s['round_type'])}** — "
                f"{s['created_at'][:19].replace('T', ' ')} — "
                f"{s['n_questions']} question(s)"
            )
            cols = st.columns([5, 2, 2])
            cols[0].markdown(label)
            if cols[1].button("Resume", key=f"resume-{sid}", type="primary"):
                st.session_state["interview_session_id"] = sid
                st.session_state["interview_round_type"] = s["round_type"]
                st.rerun()
            if cols[2].button("Abandon", key=f"abandon-{sid}"):
                try:
                    api_client.abandon_session(token, sid)
                    st.rerun()
                except api_client.ApiError as e:
                    st.error(e.detail)
        st.divider()

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
            prep_hint = "Go to Setup and click 'Prepare for interview' on this JD."
            hints = {
                "profile_missing": f"No profile yet — {prep_hint}",
                "job_not_analyzed": f"JD not analyzed yet — {prep_hint}",
                "company_snapshot_missing": f"No company snapshot yet — {prep_hint}",
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
    if t.get("score") is not None:
        with st.chat_message("assistant"):
            st.markdown(f"**Score: {t['score']}/10**")
            if t.get("feedback"):
                st.markdown(f"**Feedback.** {t['feedback']}")
            if t.get("model_answer"):
                with st.expander("Model answer"):
                    st.write(t["model_answer"])


turns = detail.get("turns", [])
n_questions = detail["n_questions"]
status = detail["status"]


def _need_new_question() -> bool:
    if status != "active":
        return False
    if len(turns) >= n_questions:
        return False
    if not turns:
        return True
    last = turns[-1]
    return last.get("answer") is not None and last.get("score") is not None


def _need_answer() -> bool:
    return bool(turns) and turns[-1].get("answer") is None and status == "active"


def _need_evaluation_resume() -> bool:
    """Answer was saved but evaluator didn't finish (e.g. user reloaded mid-stream)."""
    return (
        bool(turns)
        and turns[-1].get("answer") is not None
        and turns[-1].get("score") is None
        and status == "active"
    )


if _need_new_question():
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


if _need_answer():
    st.divider()
    with st.form("answer_form", clear_on_submit=False):
        answer = st.text_area("Your answer", height=180, key="answer_input")
        submitted = st.form_submit_button("Submit answer", type="primary")
    if submitted:
        text = answer.strip()
        if not text:
            st.error("Type something before submitting.")
        else:
            try:
                ev = api_client.submit_answer(token, session_id, text)
            except api_client.ApiError as e:
                st.error(e.detail)
                st.stop()
            try:
                # Echo the user's answer in the chat history.
                with st.chat_message("user"):
                    st.write(text)
                # Score badge appears as soon as feedback starts streaming.
                with st.chat_message("assistant"):
                    feedback_placeholder = st.empty()
                    feedback_text = st.write_stream(ev.consume_feedback_tokens())
                    score_label = f"**Score: {ev.score}/10**" if ev.score is not None else ""
                    if score_label:
                        feedback_placeholder.markdown(score_label)
                    st.markdown(f"**Feedback.** {feedback_text}")
                    with st.expander("Model answer", expanded=False):
                        st.write_stream(ev.consume_model_answer_tokens())
                ev.consume_remaining()
            finally:
                ev.finish()
            if ev.error:
                st.error(f"Evaluation failed: {ev.error.get('code', 'unknown')}")
            elif ev.done and ev.done.get("session_status") == "complete":
                st.success(f"Session complete — {n_questions} questions answered.")
            st.rerun()


if _need_evaluation_resume():
    st.divider()
    st.warning(
        "An evaluation didn't finish on the last turn. Click below to retry — "
        "your answer is already saved."
    )
    if st.button("Retry evaluation", type="primary"):
        # Re-submit the same answer text; the route is idempotent on a turn
        # whose answer is set but score is null.
        last = turns[-1]
        try:
            ev = api_client.submit_answer(token, session_id, last["answer"])
        except api_client.ApiError as e:
            st.error(e.detail)
            st.stop()
        try:
            with st.chat_message("assistant"):
                feedback_placeholder = st.empty()
                feedback_text = st.write_stream(ev.consume_feedback_tokens())
                if ev.score is not None:
                    feedback_placeholder.markdown(f"**Score: {ev.score}/10**")
                st.markdown(f"**Feedback.** {feedback_text}")
                with st.expander("Model answer", expanded=False):
                    st.write_stream(ev.consume_model_answer_tokens())
            ev.consume_remaining()
        finally:
            ev.finish()
        st.rerun()


if status != "active":
    st.divider()
    st.info(f"Session **{status}**.")
elif len(turns) >= n_questions and not _need_answer():
    st.divider()
    st.success(f"Reached {n_questions} questions.")
