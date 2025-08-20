import os, json, requests, pandas as pd, streamlit as st, ast

st.set_page_config("Travel Planner AI", "🧳", layout="centered")
# Set AGENT_ENDPOINT to the AWS Bedrock AgentCore Runtime endpoint for cloud deployment
AGENT_ENDPOINT = os.getenv("AGENT_ENDPOINT")

if not AGENT_ENDPOINT:
    st.error("AGENT_ENDPOINT environment variable is not set. Please set it to your Bedrock AgentCore Runtime endpoint (e.g., https://<your-bedrock-agentcore-endpoint>/invocations)")
    st.stop()

# ---------- Styles ----------
st.markdown(
    """
    <style>
      :root{--bg:#f7f8fb;--ink:#0f172a;--muted:#6b7280;--line:#e5e7eb;--brand:#1e3c72;--cta:#2563eb;--ok:#10b981}
      html,body,[class*="css"]{background:var(--bg)!important;color:var(--ink)}
      .wrap{border:1px solid var(--line);border-radius:16px;padding:16px;background:#fff;box-shadow:0 8px 28px rgba(2,6,23,.06)}
      .title{display:flex;gap:10px;align-items:center;font:800 1.6rem system-ui;color:var(--brand)}
      .card{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fff;box-shadow:0 6px 20px rgba(15,23,42,.06);margin:8px 0}
      .card h4{margin:0 0 6px;font:800 1.05rem system-ui;color:#1f5fbf}
      .foot{margin-top:22px;color:#9aa3ad;font-size:.95rem;text-align:center;border-top:1px dashed var(--line);padding-top:10px}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------- Header ----------
st.markdown(
    """
    <div class="wrap">
      <div class="title">🧳 Travel Planner 
        <span style="font:700 .8rem system-ui;color:#1d4ed8;background:#eef2ff;
        border:1px solid #dbeafe;border-radius:999px;padding:2px 8px">
        AI Assistant</span>
      </div>
      <div class="kicker">One prompt → flights, hotels & a day-wise plan.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------- Form ----------
with st.form("planner"):
    prompt = st.text_area("Where do you want to go?", 
        "I want to go from Delhi to Pune for 3 nights, need a hotel with breakfast, and a sightseeing plan",
        height=110)
    submitted = st.form_submit_button("Plan My Trip")

# ---------- Helpers ----------
card = lambda t, h: st.markdown(f"<div class='card'><h4>{t}</h4>{h}</div>", unsafe_allow_html=True)

def dfshow(name: str, rows):
    if rows:
        with st.expander(name):
            st.dataframe(pd.DataFrame(rows))

def safe_get(d, key):
    """Safe dict access. If d is a dict → return d[key], else wrap string into dict."""
    if isinstance(d, dict):
        return d.get(key, {})
    if isinstance(d, str):
        return {"raw": d}
    return {}

# ---------- Submit Flow ----------
if submitted:
    try:
        with st.spinner("Planning your trip…"):
            resp = requests.post(AGENT_ENDPOINT, json={"prompt": prompt}, timeout=300)

        # --- normalize backend response into dict ---
        try:
            raw = resp.json()
        except Exception:
            raw = resp.text

        if isinstance(raw, str):
            try:
                data = json.loads(raw)       # JSON string
            except Exception:
                try:
                    data = ast.literal_eval(raw)  # Python dict string
                except Exception:
                    data = {"raw": raw}
        else:
            data = raw

        tabs = st.tabs(["✈️ Flights", "🏨 Hotels", "🗺️ Guide", "🧾 Raw"])

        # --- Flight ---
        with tabs[0]:
            flight = safe_get(data, "flight")
            rec = flight.get("best_flight_recommendation") or flight.get("raw")
            if rec: card("Best Flight Recommendation", rec)
            for seg in (flight.get("segments") or []):
                res = (seg or {}).get("result", {})
                dfshow(f"Flight Table: {seg.get('from','')} → {seg.get('to','')}", res.get("sql_result"))

        # --- Hotel ---
        with tabs[1]:
            hotel = safe_get(data, "hotel")
            rec = hotel.get("best_hotel_recommendation") or hotel.get("raw")
            if rec: card("Best Hotel Recommendation", rec)
            dfshow("Hotel Table", hotel.get("sql_result"))

        # --- Guide ---
        with tabs[2]:
            guide = safe_get(data, "guide")
            plan = guide.get("daywise_plan") or guide.get("raw")
            if plan: card("Day-wise Plan", plan)
            results = guide.get("results")
            rows = results.get("data") if isinstance(results, dict) and "data" in results else (results if isinstance(results, list) else [])
            dfshow("Guide Search Results Table", rows)

        # --- Raw ---
        with tabs[3]:
            st.json(data)
    except Exception as e:
        st.error(f"Request failed: {e}")

# --- Debug: show raw response ---
if submitted:
    st.subheader("🔎 Debug: Backend Response")
    st.write("Status Code:", resp.status_code)
    st.write("Raw Response:", raw)
    st.json(data)

# ---------- Footer ----------
st.markdown(
    "<div class='foot'>© 2025 Travel Planner AI — Powered by AWS Bedrock AgentCore, AWS Strands, Snowflake Data Cloud & Cortex AI</div>",
    unsafe_allow_html=True,
)
