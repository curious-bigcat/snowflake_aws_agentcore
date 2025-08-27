import os, json, pandas as pd, streamlit as st
import uuid, boto3

# ======================
# Basic Page & Style
# ======================
st.set_page_config("Travel Planner AI", "", layout="centered")

st.markdown(
    """
    <style>
      :root{--bg:#f7f8fb;--ink:#0f172a;--muted:#6b7280;--line:#e5e7eb;--brand:#1e3c72;--cta:#2563eb}
      html,body,[class*="css"]{background:var(--bg)!important;color:var(--ink)}
      .wrap{border:1px solid var(--line);border-radius:16px;padding:16px;background:#fff;box-shadow:0 8px 28px rgba(2,6,23,.06)}
      .title{display:flex;gap:10px;align-items:center;font:800 1.6rem system-ui;color:var(--brand)}
      .badge{display:inline-flex;gap:6px;align-items:center;font:700 .7rem system-ui;color:#1d4ed8;background:#eef2ff;border:1px solid #dbeafe;border-radius:999px;padding:2px 8px}
      .card{border:1px solid var(--line);border-radius:14px;padding:14px;background:#fff;box-shadow:0 6px 20px rgba(15,23,42,.06);margin:8px 0}
      .card h4{margin:0 0 6px;font:800 1.05rem system-ui;color:#1f5fbf}
      .muted{color:var(--muted)}
      .foot{margin-top:22px;color:#9aa3ad;font-size:.95rem;text-align:center;border-top:1px dashed var(--line);padding-top:10px}
      .mono{font-family:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace}
    </style>
    """,
    unsafe_allow_html=True,
)

# ======================
# Sidebar (Region / ARN / Mode)
# ======================
st.sidebar.title("Settings")

def get_region():
    region = os.environ.get("AWS_REGION")
    if not region:
        region = st.sidebar.text_input("AWS Region", value="us-east-1", key="region_input")
    return region or "us-east-1"

REGION = get_region()

def get_agentcore_client(region_name=None):
    region = region_name or REGION
    return boto3.client("bedrock-agentcore", region_name=region)

agent_arn = st.sidebar.text_input("Agent ARN", value="", key="agent_arn_input")
mode = st.sidebar.radio("Agent Mode", ["Standard", "ReAct"], index=0, horizontal=True)
mode_key = "react" if mode == "ReAct" else "standard"

if "runtime_session_id" not in st.session_state:
    st.session_state.runtime_session_id = str(uuid.uuid4())
st.sidebar.caption("Session")
st.sidebar.code(st.session_state.runtime_session_id, language="bash")

# ======================
# Header
# ======================
st.markdown(
    """
    <div class="wrap">
      <div class="title">Travel Planner
        <span class="badge">AI Assistant</span>
      </div>
      <div class="kicker muted">One prompt → flights, hotels & a day-wise plan.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ======================
# Form (simple)
# ======================
with st.form("planner"):
    prompt = st.text_area(
        "Where do you want to go?",
        "I want to go from Delhi to Pune for 3 nights, need a hotel with breakfast, and a sightseeing plan",
        height=110
    )
    submitted = st.form_submit_button("Plan My Trip")

# ======================
# Helpers
# ======================
card = lambda t, h: st.markdown(f"<div class='card'><h4>{t}</h4>{h}</div>", unsafe_allow_html=True)

def dfshow(name: str, rows):
    if rows:
        with st.expander(name):
            try:
                st.dataframe(pd.DataFrame(rows))
            except Exception:
                st.write(rows)

def parse_event_stream(lines_iter):
    lines = [line.decode("utf-8")[6:] for line in lines_iter if line and line.decode("utf-8").startswith("data: ")]
    raw = "".join(lines)
    try: return json.loads(raw), raw
    except Exception: return {"raw": raw}, raw

# --- Submit Flow ---
raw, data = None, None
if submitted:
    if not agent_arn:
        st.error("Agent ARN is required to invoke the agent. Please provide it in the sidebar.")
    else:
        try:
            with st.spinner(f"Planning your trip… ({mode})"):
                client = get_agentcore_client(REGION)
                payload = json.dumps({"prompt": prompt, "mode": mode_key}).encode()
                response = client.invoke_agent_runtime(
                    agentRuntimeArn=agent_arn,
                    runtimeSessionId=st.session_state.runtime_session_id,
                    payload=payload
                )
                ct = response.get("contentType", "")
                if "text/event-stream" in ct:
                    data, raw = parse_event_stream(response["response"].iter_lines(chunk_size=10))
                elif "application/json" in ct:
                    raw = "".join([chunk.decode("utf-8") for chunk in response.get("response", [])])
                    try: data = json.loads(raw)
                    except Exception: data = {"raw": raw}
                else:
                    raw = str(response)
                    data = {"raw": raw}
        except Exception as e:
            st.error(f"Request failed: {e}")

if submitted and raw is not None:
    with st.expander("Debug: Backend Response", expanded=False):
        st.write("Session ID:", st.session_state.runtime_session_id)
        st.write("Agent ARN:", agent_arn)
        st.write("Mode:", mode_key)
        st.write("Raw Response:", raw)
        if data: st.json(data)

st.markdown(
    "<div class='foot'>© 2025 Travel Planner AI — Powered by AWS Bedrock AgentCore, AWS Strands, Snowflake Data Cloud & Cortex AI</div>",
    unsafe_allow_html=True,
)

# --- Main Display ---
if data:
    best = data.get("best_trip_recommendation")
    raw_context = data.get("raw_context")
    react_trace = data.get("react_trace")
    if best:
        card("Best Trip Recommendation", f"<div class='mono' style='white-space:pre-wrap'>{best}</div>")
    tab_cfg = [
        ("Flights (Outbound)", "flights_outbound", "Outbound Segment"),
        ("Flights (Return)", "flights_return", "Return Segment"),
        ("Hotels", "hotels", "Hotel Query"),
        ("Guide", "guide", None),
        ("Raw", None, None)
    ]
    if raw_context or react_trace:
        tab_names = [t[0] for t in tab_cfg]
        if react_trace: tab_names.append("ReAct Trace")
        tabs = st.tabs(tab_names)
        for i, (tab, (tab_name, ctx_key, seg_label)) in enumerate(zip(tabs, tab_cfg)):
            with tab:
                if tab_name == "Guide":
                    guide = (raw_context or {}).get("guide", {}) or {}
                    if guide.get("error"): st.error(f"Guide Search Error: {guide['error']}")
                    else:
                        gtxt = guide.get("guide_text")
                        if gtxt: card("Guide Summary", f"<div style='white-space:pre-wrap'>{gtxt}</div>")
                        results = guide.get("results"); rows = []
                        if isinstance(results, dict) and "data" in results: rows = results.get("data") or []
                        elif isinstance(results, list): rows = results
                        dfshow("Guide Search Rows (raw)", rows)
                elif tab_name == "Raw":
                    st.json(raw_context or {})
                elif ctx_key:
                    items = (raw_context or {}).get(ctx_key, []) or []
                    if not items: st.info(f"No {tab_name.lower()} data.")
                    for idx, f in enumerate(items):
                        analyst_text = (f or {}).get("analyst_text", "")
                        fallback_used = (f or {}).get("fallback_used")
                        header = analyst_text or "(no analyst summary)"
                        if fallback_used and fallback_used != "none_available":
                            header += f" · fallback: {fallback_used}"
                        card(f"{seg_label} {idx+1}", header)
                        dfshow(f"{tab_name.split()[0]} SQL Results", (f or {}).get("sql_result"))
        if react_trace:
            with tabs[-1]:
                if not react_trace:
                    st.info("No ReAct trace available.")
                else:
                    for i, step in enumerate(react_trace, 1):
                        thought = step.get("thought", "")
                        action = step.get("action", "")
                        args   = step.get("args", {})
                        obs    = step.get("observation", {})
                        ok     = obs.get("ok")
                        rows   = obs.get("rows")
                        err    = obs.get("error")
                        head = f"Step {i} — {action} ({'ok' if ok else 'error'})"
                        body = []
                        if thought: body.append(f"Thought: {thought}")
                        if args: body.append(f"Args: {json.dumps(args)}")
                        if rows is not None:
                            try: body.append(f"Rows: {len(rows)}")
                            except Exception: pass
                        if err: body.append(f"Error: {err}")
                        card(head, "<br/>".join(body))
