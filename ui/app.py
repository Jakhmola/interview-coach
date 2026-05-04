import streamlit as st

from ui import api_client, state

st.set_page_config(page_title="Interview Coach", page_icon="🎯")
st.title("Interview Coach")


def render_login_register() -> None:
    login_tab, register_tab, resume_tab = st.tabs(["Login", "Register", "Resume with token"])

    with login_tab:
        with st.form("login_form"):
            email = st.text_input("Email", key="login_email")
            password = st.text_input("Password", type="password", key="login_password")
            submitted = st.form_submit_button("Log in")
        if submitted:
            try:
                resp = api_client.login(email.strip(), password)
                state.set_auth(resp["access_token"], resp["user"])
                st.rerun()
            except api_client.ApiError as e:
                st.error(e.detail)

    with register_tab:
        with st.form("register_form"):
            email = st.text_input("Email", key="register_email")
            password = st.text_input(
                "Password (min 8 chars)", type="password", key="register_password"
            )
            submitted = st.form_submit_button("Create account")
        if submitted:
            try:
                resp = api_client.register(email.strip(), password)
                state.set_auth(resp["access_token"], resp["user"])
                st.rerun()
            except api_client.ApiError as e:
                st.error(e.detail)

    with resume_tab:
        st.caption(
            "Paste an access token from a previous session to resume without re-entering "
            "your password. Tokens expire after 60 minutes by default."
        )
        with st.form("resume_form"):
            token = st.text_area("Access token", key="resume_token", height=100)
            submitted = st.form_submit_button("Resume")
        if submitted:
            t = token.strip()
            if not t:
                st.error("Paste a token first.")
            else:
                try:
                    user = api_client.me(t)
                    state.set_auth(t, user)
                    st.rerun()
                except api_client.ApiError as e:
                    st.error(e.detail)


def render_home() -> None:
    user = state.get_user() or {}
    col1, col2 = st.columns([4, 1])
    with col1:
        st.write(f"Logged in as **{user.get('email', '?')}**")
    with col2:
        if st.button("Log out"):
            state.clear_auth()
            st.rerun()

    with st.expander("Access token (copy to resume after reload)", expanded=False):
        st.caption(
            "Reloading the page clears your session. Save this token if you want to "
            "resume from the **Resume with token** tab. Treat it like a password."
        )
        st.code(state.get_token() or "", language=None)

    st.subheader("API status")
    try:
        payload = api_client.healthz()
        st.success(f"API healthy — version {payload.get('version', '?')}")
        st.json(payload)
    except Exception as e:
        st.error(f"Could not reach API: {e}")


if state.is_logged_in():
    render_home()
else:
    render_login_register()
