import os

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Interview Coach", page_icon="🎯")
st.title("Interview Coach")
st.write("Phase 0 — skeleton. The interview UI shows up in later phases.")

st.subheader("API status")
try:
    r = httpx.get(f"{API_BASE_URL}/healthz", timeout=5.0)
    r.raise_for_status()
    payload = r.json()
    st.success(f"API healthy — version {payload.get('version', '?')}")
    st.json(payload)
except Exception as e:
    st.error(f"Could not reach API at {API_BASE_URL}: {e}")
