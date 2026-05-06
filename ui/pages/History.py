import streamlit as st
from ui import api_client, state

st.set_page_config(page_title="History — Interview Coach", page_icon="📜")
st.title("History")

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

STATUS_BADGE = {
    "active": ":blue[active]",
    "complete": ":green[complete]",
    "abandoned": ":gray[abandoned]",
}


try:
    sessions = api_client.list_sessions(token)
except api_client.ApiError as e:
    st.error(e.detail)
    st.stop()

if not sessions:
    st.info("No sessions yet. Start one on the **Interview** page.")
    st.page_link("pages/Interview.py", label="Go to Interview")
    st.stop()


for sess in sessions:
    sid = sess["id"]
    label = (
        f"**{ROUND_LABELS.get(sess['round_type'], sess['round_type'])}** — "
        f"{sess['created_at'][:19].replace('T', ' ')} "
        f"— {STATUS_BADGE.get(sess['status'], sess['status'])} "
        f"— {sess['n_questions']} question(s)"
    )
    with st.expander(label, expanded=False):
        try:
            detail = api_client.get_session_detail(token, sid)
        except api_client.ApiError as e:
            st.error(e.detail)
            continue

        turns = detail.get("turns", [])
        if not turns:
            st.caption("No turns recorded.")
            continue

        scored = [t for t in turns if t.get("score") is not None]
        if scored:
            avg = sum(t["score"] for t in scored) / len(scored)
            st.caption(f"Average score: **{avg:.1f}** across {len(scored)} evaluated turn(s).")

        for t in turns:
            with st.container(border=True):
                st.markdown(f"**Q{t['turn_index'] + 1}.** {t['question']}")
                if t.get("answer"):
                    st.markdown("**Your answer.**")
                    st.write(t["answer"])
                else:
                    st.caption("_no answer recorded_")
                if t.get("score") is not None:
                    st.markdown(f"**Score:** {t['score']}/10")
                    if t.get("feedback"):
                        st.markdown("**Feedback.**")
                        st.write(t["feedback"])
                    if t.get("model_answer"):
                        with st.expander("Model answer", expanded=False):
                            st.write(t["model_answer"])
                else:
                    st.caption("_no evaluation_")
