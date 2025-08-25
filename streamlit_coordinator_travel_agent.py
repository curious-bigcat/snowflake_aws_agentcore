import os, json, requests, pandas as pd, streamlit as st
import uuid, boto3

st.set_page_config("Travel Planner AI", "", layout="centered")

# --- Region selection ---
def get_region():
    region = os.environ.get("AWS_REGION")
    if not region:
        region = st.sidebar.text_input("AWS Region", value="us-east-1", key="region_input")
    return region or "us-east-1"

REGION = get_region()

def get_agentcore_client(region_name=None):
    region = region_name or REGION
    return boto3.client("bedrock-agentcore", region_name=region)

# --- Prompt for Agent ARN ---
agent_arn = st.sidebar.text_input("Agent ARN (from AWS Console or CLI):", value="", key="agent_arn_input")

# --- Session Management ---
if "runtime_session_id" not in st.session_state:
    st.session_state.runtime_session_id = str(uuid.uuid4())

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
      <div class="title">Travel Planner 
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

# ---------- Submit Flow ----------
resp, raw, data = None, None, None
if submitted:
    if not agent_arn:
        st.error("Agent ARN is required to invoke the agent. Please provide it above.")
    else:
        try:
            with st.spinner("Planning your trip…"):
                client = get_agentcore_client(REGION)
                payload = json.dumps({"prompt": prompt}).encode()
                response = client.invoke_agent_runtime(
                    agentRuntimeArn=agent_arn,
                    runtimeSessionId=st.session_state.runtime_session_id,
                    payload=payload
                )
                content_type = response.get("contentType", "")
                if "text/event-stream" in content_type:
                    content = []
                    for line in response["response"].iter_lines(chunk_size=10):
                        if line:
                            line = line.decode("utf-8")
                            if line.startswith("data: "):
                                line = line[6:]
                                content.append(line)
                    raw = "\n".join(content)
                    try:
                        data = json.loads("".join(content))
                    except Exception:
                        data = {"raw": raw}
                elif content_type == "application/json":
                    raw = "".join([chunk.decode("utf-8") for chunk in response.get("response", [])])
                    try:
                        data = json.loads(raw)
                    except Exception:
                        data = {"raw": raw}
                else:
                    raw = str(response)
                    data = {"raw": raw}
        except Exception as e:
            st.error(f"Request failed: {e}")

# --- Debug: show raw response ---
if submitted and raw is not None:
    with st.expander("Debug: Backend Response", expanded=False):
        st.write("Session ID:", st.session_state.runtime_session_id)
        st.write("Agent ARN:", agent_arn)
        st.write("Raw Response:", raw)
        if data: st.json(data)

# ---------- Footer ----------
st.markdown(
    "<div class='foot'>© 2025 Travel Planner AI — Powered by AWS Bedrock AgentCore, AWS Strands, Snowflake Data Cloud & Cortex AI</div>",
    unsafe_allow_html=True,
)

# ---------- Main Display ----------
if data:
    best = data.get("best_trip_recommendation")
    raw_context = data.get("raw_context")

    if best:
        card("Best Trip Recommendation", best)

    if raw_context:
        tabs = st.tabs(["Flights", "Hotels", "Guide", "Raw"])
        # --- Flights ---
        with tabs[0]:
            flights = raw_context.get("flights", [])
            for idx, f in enumerate(flights):
                card(f"Flight Segment {idx+1}", f.get("analyst_text", ""))
                dfshow("Flight SQL Results", f.get("sql_result"))
        # --- Hotels ---
        with tabs[1]:
            hotels = raw_context.get("hotels", [])
            for idx, h in enumerate(hotels):
                card(f"Hotel Query {idx+1}", h.get("analyst_text", ""))
                dfshow("Hotel SQL Results", h.get("sql_result"))
        # --- Guide ---
        with tabs[2]:
            guide = raw_context.get("guide", {})
            plan = guide.get("results") or []
            dfshow("Guide Search Results", plan.get("results") if isinstance(plan, dict) else plan)
        # --- Raw ---
        with tabs[3]:
            st.json(raw_context)
