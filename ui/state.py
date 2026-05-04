from typing import Any

import streamlit as st


def get_token() -> str | None:
    return st.session_state.get("auth_token")


def get_user() -> dict[str, Any] | None:
    return st.session_state.get("auth_user")


def set_auth(token: str, user: dict[str, Any]) -> None:
    st.session_state["auth_token"] = token
    st.session_state["auth_user"] = user


def clear_auth() -> None:
    st.session_state.pop("auth_token", None)
    st.session_state.pop("auth_user", None)


def is_logged_in() -> bool:
    return get_token() is not None
