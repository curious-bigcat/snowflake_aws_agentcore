import os, json, re, snowflake.connector, requests, datetime, decimal
from concurrent.futures import ThreadPoolExecutor
from strands import Agent
from bedrock_agentcore.runtime import BedrockAgentCoreApp

def load_secrets_from_aws(secret_name, region_name=None):
    try:
        import boto3
        session = boto3.session.Session()
        if region_name is None:
            region_name = os.environ.get('AWS_REGION', 'us-east-1')
        client = session.client(service_name='secretsmanager', region_name=region_name)
        secret = client.get_secret_value(SecretId=secret_name)['SecretString']
        for k, v in json.loads(secret).items(): os.environ[k] = v
        return json.loads(secret)
    except Exception as e:
        print(f"Warning: Could not load secrets from AWS Secrets Manager: {e}"); return {}

def try_load_secrets():
    sn = os.environ.get('AGENTCORE_SECRET_NAME','arn:aws:secretsmanager:us-east-1:484577546576:secret:agentcore/travelplanner/credentials-hmfGXv')
    if sn: load_secrets_from_aws(sn)
try_load_secrets()

MODEL_ID = os.getenv('MODEL_ID', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
if not SNOWFLAKE_ACCOUNT: raise ValueError("SNOWFLAKE_ACCOUNT is not set. Check Secrets Manager config.")
SNOWFLAKE_USER, SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_USER"), os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_DATABASE, SNOWFLAKE_SCHEMA, SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_DATABASE", "TRAVEL_DB"), os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC"), os.getenv("SNOWFLAKE_WAREHOUSE", "XSMALL_WH")
CORTEX_ANALYST_URL = os.getenv("CORTEX_ANALYST_URL", f"https://{SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/cortex/analyst/message")
SEMANTIC_MODEL_FLIGHT = os.getenv("SEMANTIC_MODEL_FILE", "@TRAVEL_DB.PUBLIC.DATA/FLIGHT_ANALYTICS.yaml")
SEMANTIC_MODEL_HOTEL = os.getenv("HOTEL_SEMANTIC_MODEL_FILE", '@TRAVEL_DB.PUBLIC.DATA/HOTEL_ANALYTICS.yaml')
CORTEX_SEARCH_DATABASE, CORTEX_SEARCH_SCHEMA, CORTEX_SEARCH_SERVICE = os.getenv("CORTEX_SEARCH_DATABASE", "TRAVEL_DB"), os.getenv("CORTEX_SEARCH_SCHEMA", "PUBLIC"), os.getenv("CORTEX_SEARCH_SERVICE", "TRAVEL_SEARCH_SERVICE")

def _open_snowflake():
    return snowflake.connector.connect(user=SNOWFLAKE_USER, password=SNOWFLAKE_PASSWORD, account=SNOWFLAKE_ACCOUNT, database=SNOWFLAKE_DATABASE, schema=SNOWFLAKE_SCHEMA, warehouse=SNOWFLAKE_WAREHOUSE)
def execute_sql_on_snowflake(sql):
    ctx = None
    try:
        ctx = _open_snowflake(); cs = ctx.cursor()
        try:
            cs.execute(sql)
            if cs.description:
                columns = [d[0] for d in cs.description]
                return [dict(zip(columns, row)) for row in cs.fetchall()], None
            return [], None
        finally: cs.close()
    except Exception as e: return None, f"SQL execution error: {str(e)}"
    finally: 
        if ctx: ctx.close()
make_json_safe = lambda obj: {k: make_json_safe(v) for k, v in obj.items()} if isinstance(obj, dict) else [make_json_safe(v) for v in obj] if isinstance(obj, list) else str(obj) if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)) else float(obj) if isinstance(obj, decimal.Decimal) else obj
_nonempty = lambda rows: isinstance(rows, list) and len(rows) > 0
fallback_outbound_sql = lambda src, dst: f"""SELECT airline, source, destination, price, duration, total_stops, dep_time, arrival_time FROM {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.flight_data WHERE source = '{src}' AND destination = '{dst}' ORDER BY price ASC, duration ASC LIMIT 10;"""
fallback_return_sql = lambda src, dst: fallback_outbound_sql(dst, src)
def fallback_roundtrip_bundle_sql(src, dst):
    return f"""WITH outb AS (SELECT airline, source, destination, price AS out_price, duration AS out_dur, total_stops AS out_stops, dep_time AS out_dep, arrival_time AS out_arr FROM {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.flight_data WHERE source = '{src}' AND destination = '{dst}'), ret AS (SELECT airline AS r_airline, source AS r_source, destination AS r_destination, price AS ret_price, duration AS ret_dur, total_stops AS ret_stops, dep_time AS ret_dep, arrival_time AS ret_arr FROM {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.flight_data WHERE source = '{dst}' AND destination = '{src}') SELECT outb.*, ret.*, (out_price + ret_price) AS total_price, (out_dur + ret_dur) AS total_duration FROM outb JOIN ret ON 1=1 ORDER BY total_price ASC, total_duration ASC LIMIT 10;"""
fallback_hotels_sql = lambda city: f"""SELECT name, city, price, rating FROM {SNOWFLAKE_DATABASE}.{SNOWFLAKE_SCHEMA}.hotel_data WHERE city = '{city}' ORDER BY price ASC, rating DESC LIMIT 10;"""
run_sql_dict = lambda sql: (lambda rows, err: rows if _nonempty(rows) else [])(*execute_sql_on_snowflake(sql))
def ensure_flights_with_fallback(src, dst, analyst_result):
    out = dict(analyst_result or {})
    if _nonempty(out.get("sql_result")): return out
    reasons = []
    if out.get("error"): reasons.append(f"analyst_error={out['error']}")
    if out.get("sql") and not _nonempty(out.get("sql_result")): reasons.append("analyst_sql_empty_rows")
    leg_rows = run_sql_dict(fallback_outbound_sql(src, dst))
    if _nonempty(leg_rows):
        out.setdefault("notes", []).append("Used fallback_outbound_sql")
        out["sql_result"] = make_json_safe(leg_rows)
        out["fallback_used"] = "outbound"
        if not out.get("sql"): out["sql"] = "-- fallback_outbound_sql"
        return out
    bundle_rows = run_sql_dict(fallback_roundtrip_bundle_sql(src, dst))
    if _nonempty(bundle_rows):
        out.setdefault("notes", []).append("Used fallback_roundtrip_bundle_sql")
        out["sql_result"] = make_json_safe(bundle_rows)
        out["fallback_used"] = "roundtrip_bundle"
        out["sql"] = "-- fallback_roundtrip_bundle_sql"
        return out
    out["sql_result"] = []
    out["fallback_used"] = "none_available"
    out["error_reason"] = "No flights in snapshot for this leg or route."
    if reasons: out["notes"] = (out.get("notes") or []) + reasons
    return out
def ensure_return_with_fallback(src, dst_last, analyst_result): return ensure_flights_with_fallback(dst_last, src, analyst_result)
def ensure_hotels_with_fallback(city, analyst_result):
    out = dict(analyst_result or {})
    if _nonempty(out.get("sql_result")): return out
    reasons = []
    if out.get("error"): reasons.append(f"analyst_error={out['error']}")
    if out.get("sql") and not _nonempty(out.get("sql_result")): reasons.append("analyst_sql_empty_rows")
    hotel_rows = run_sql_dict(fallback_hotels_sql(city))
    if _nonempty(hotel_rows):
        out.setdefault("notes", []).append("Used fallback_hotels_sql")
        out["sql_result"] = make_json_safe(hotel_rows)
        out["fallback_used"] = "hotels_basic"
        if not out.get("sql"): out["sql"] = "-- fallback_hotels_sql"
        return out
    out["sql_result"] = []
    out["fallback_used"] = "none_available"
    out["error_reason"] = "No hotels in snapshot for this city."
    if reasons: out["notes"] = (out.get("notes") or []) + reasons
    return out
def extract_trip_details(user_input, model=MODEL_ID):
    system_prompt = ("You are a travel assistant. Extract the travel intent into JSON:\n{ \"source_city\": <string>, \"destination_cities\": [<string>, ...] }\n- Only output valid JSON.\n- Preserve travel order.")
    agent = Agent(model=model, system_prompt=system_prompt)
    raw = str(agent(user_input))
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try: return json.loads(match.group(0))
        except json.JSONDecodeError: return None
    return None
def query_cortex_analyst(cortex_question, semantic_model_file):
    token = os.getenv("SNOWFLAKE_AUTH_TOKEN")
    if not token: return {"error": "SNOWFLAKE_AUTH_TOKEN is not set. Check Secrets Manager."}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"}
    body = {"messages": [{"role": "user", "content": [{"type": "text", "text": cortex_question}]}], "semantic_model_file": semantic_model_file}
    try:
        r = requests.post(CORTEX_ANALYST_URL, headers=headers, json=body, timeout=60)
        r.raise_for_status(); analyst_response = r.json()
    except Exception as e: return {"error": f"Cortex Analyst error: {e}"}
    msg = analyst_response.get("message"); contents = msg.get("content", []) if isinstance(msg, dict) else []
    analyst_text, sql, suggestions = None, None, []
    for c in contents:
        if isinstance(c, dict):
            if c.get("type") == "text" and not analyst_text: analyst_text = c.get("text")
            elif c.get("type") == "sql" and not sql: sql = c.get("statement") or c.get("sql")
            elif c.get("type") == "suggestions": suggestions.extend(map(str, c.get("suggestions", [])))
    sql_result, error = None, None
    if sql: sql_result, error = execute_sql_on_snowflake(sql)
    return {"analyst_text": analyst_text, "sql": sql, "sql_result": make_json_safe(sql_result) if sql_result else None, "suggestions": suggestions, "error": error}
def run_cortex_search(dests, user_input):
    search_query = f"travel guide for {' to '.join(dests)}" if dests else user_input
    try:
        from snowflake.core import Root
        from snowflake.snowpark import Session
        session = Session.builder.configs({"account": SNOWFLAKE_ACCOUNT, "user": SNOWFLAKE_USER, "password": SNOWFLAKE_PASSWORD, "warehouse": SNOWFLAKE_WAREHOUSE, "database": SNOWFLAKE_DATABASE, "schema": SNOWFLAKE_SCHEMA, "role": os.getenv("SNOWFLAKE_ROLE", "PUBLIC")}).create()
        root = Root(session)
        service = root.databases[CORTEX_SEARCH_DATABASE].schemas[CORTEX_SEARCH_SCHEMA].cortex_search_services[CORTEX_SEARCH_SERVICE]
        resp = service.search(query=search_query, columns=["CHUNK"], limit=10)
        results = resp.to_dict() if hasattr(resp, "to_dict") else resp
        if isinstance(results, dict) and "data" in results:
            guide_text = "\n".join([str(r["CHUNK"]) for r in results["data"] if r.get("CHUNK")])
            return {"query": search_query, "results": results, "guide_text": guide_text}
        return {"query": search_query, "results": results}
    except Exception as e: return {"query": search_query, "error": str(e)}
analyst_or_fallback_flights = lambda src, dst: ensure_flights_with_fallback(src, dst, query_cortex_analyst(f"Find flights from {src} to {dst}.", SEMANTIC_MODEL_FLIGHT))
analyst_or_fallback_hotels = lambda city: ensure_hotels_with_fallback(city, query_cortex_analyst(f"Find hotels in {city}.", SEMANTIC_MODEL_HOTEL))
REACT_SYSTEM = """You are a travel planning ReAct agent.\nThink step-by-step. At each step, either CALL a tool or, if enough evidence exists, RETURN a final answer.\nOutput ONLY a single JSON object per step in this schema:\n{\n  \"thought\": \"<short reasoning>\",\n  \"action\": \"<one of: analyst_flights | analyst_hotels | search_guides | fallback_flights | fallback_hotels | analyst_return | finish>\",\n  \"args\": { ... }\n}\nRules:\n- Start from the provided intent context; don't re-extract unless missing.\n- Prefer analyst_* actions first; use fallback_* only if analyst_* produced no rows.\n- Use search_guides after you have at least one flight and hotel.\n- When ready, choose action \"finish\" and provide a short plan hint in args.\n- Keep thoughts concise."""
def _safe_json_find(s, default=None):
    try:
        m = re.search(r"\{.*\}\s*$", s.strip(), re.S)
        return json.loads(m.group(0)) if m else (default if default is not None else {})
    except Exception: return default if default is not None else {}
def tool_analyst_flights(ctx, src, dst):
    res = analyst_or_fallback_flights(src, dst)
    ctx.setdefault("flights_outbound", []).append(res)
    return {"ok": True, "rows": res.get("sql_result") or [], "meta": {"used": res.get("fallback_used")}}
def tool_analyst_hotels(ctx, city):
    res = analyst_or_fallback_hotels(city)
    ctx.setdefault("hotels", []).append(res)
    return {"ok": True, "rows": res.get("sql_result") or [], "meta": {"used": res.get("fallback_used")}}
def tool_search_guides(ctx, dests, user_input):
    res = run_cortex_search(dests, user_input)
    ctx["guide"] = res
    return {"ok": True, "chars": len(res.get("guide_text") or ""), "meta": {}}
def tool_fallback_flights(ctx, src, dst):
    rows = run_sql_dict(fallback_outbound_sql(src, dst))
    res = {"analyst_text": None, "sql": "-- forced_fallback_outbound_sql", "sql_result": make_json_safe(rows), "fallback_used": "forced_outbound", "notes": ["Forced fallback (ReAct)"]}
    ctx.setdefault("flights_outbound", []).append(res)
    return {"ok": bool(rows), "rows": rows, "meta": {"forced": True}}
def tool_fallback_hotels(ctx, city):
    rows = run_sql_dict(fallback_hotels_sql(city))
    res = {"analyst_text": None, "sql": "-- forced_fallback_hotels_sql", "sql_result": make_json_safe(rows), "fallback_used": "forced_hotels", "notes": ["Forced fallback (ReAct)"]}
    ctx.setdefault("hotels", []).append(res)
    return {"ok": bool(rows), "rows": rows, "meta": {"forced": True}}
TOOLS = {
    "analyst_flights": lambda ctx, args: tool_analyst_flights(ctx, args["source"], args["destination"]),
    "analyst_hotels":  lambda ctx, args: tool_analyst_hotels(ctx, args["city"]),
    "search_guides":   lambda ctx, args: tool_search_guides(ctx, args["destinations"], args["user_input"]),
    "fallback_flights":lambda ctx, args: tool_fallback_flights(ctx, args["source"], args["destination"]),
    "fallback_hotels": lambda ctx, args: tool_fallback_hotels(ctx, args["city"]),
}
def react_trip_agent(user_input, model=MODEL_ID, max_steps=6):
    intent = extract_trip_details(user_input) or {"source_city": None, "destination_cities": []}
    source = intent.get("source_city"); dests = intent.get("destination_cities") or []
    if not source or not dests:
        return {"error": "Could not extract trip details from input.", "raw_context": {"intent": intent}}
    ctx = {"trip_details": intent, "flights_outbound": [], "flights_return": [], "hotels": [], "guide": {}}
    history = []
    for _ in range(max_steps):
        trace = [{"thought": h.get("thought",""), "action": h.get("action",""), "args": h.get("args",{}), ("ok" if isinstance(h.get("observation"), dict) and "ok" in h["observation"] else "observation"): h.get("observation", {})} for h in history[-3:]]
        react_input = {"intent": intent, "recent_trace": trace, "have_flights": any((b.get("sql_result") for b in ctx["flights_outbound"])), "have_hotels": any((b.get("sql_result") for b in ctx["hotels"])), "have_guide": bool(ctx["guide"])}
        step_out = str(Agent(model=model, system_prompt=REACT_SYSTEM)(json.dumps(react_input)))
        cmd = _safe_json_find(step_out, default={"action":"finish","args":{"plan_hint":"fallback"}})
        action = cmd.get("action", "finish"); thought = cmd.get("thought", ""); args = cmd.get("args", {})
        if action == "finish":
            system_prompt = ("You are a travel planner. Using the provided SQL results for flights, hotels, "
                "and guide text, produce ONE detailed best recommendation.\n"
                "- Recommend specific flights (round trip if possible). Mention briefly if fallbacks used.\n"
                "- Recommend hotels with reasoning.\n"
                "- Provide a day-wise itinerary (only from guide_text if present).\n"
                "- Be structured (markdown) and avoid fabrications.")
            safe_ctx = make_json_safe(ctx)
            best_plan = str(Agent(model=MODEL_ID, system_prompt=system_prompt)(json.dumps(safe_ctx)))
            return {"best_trip_recommendation": best_plan, "raw_context": safe_ctx, "react_trace": history}
        observation = {"ok": False, "error": f"unknown action '{action}'"}
        try:
            if action in TOOLS:
                observation = TOOLS[action](ctx, {**args, "user_input": user_input})
            elif action == "analyst_return":
                ret = analyst_or_fallback_flights(dests[-1], source)
                ctx["flights_return"].append(ret)
                observation = {"ok": bool(ret.get("sql_result")), "rows": ret.get("sql_result") or []}
            else:
                observation = {"ok": False, "error": f"unsupported action '{action}'"}
        except Exception as e:
            observation = {"ok": False, "error": str(e)}
        history.append({"thought": thought, "action": action, "args": args, "observation": observation})
    safe_ctx = make_json_safe(ctx)
    fallback_plan = str(Agent(model=MODEL_ID, system_prompt="Summarize available info into a concise plan without fabrication.")(json.dumps(safe_ctx)))
    return {"best_trip_recommendation": fallback_plan, "raw_context": safe_ctx, "react_trace": history}
def trip_recommendation_agent(user_input):
    travel = extract_trip_details(user_input)
    if not travel: return {"error": "Could not extract trip details from input."}
    source = travel["source_city"]; dests = travel["destination_cities"]
    with ThreadPoolExecutor() as ex:
        flight_futures = [ex.submit(analyst_or_fallback_flights, source, d) for d in dests]
        return_futures = [ex.submit(analyst_or_fallback_flights, dests[-1], source)] if dests else []
        hotel_futures  = [ex.submit(analyst_or_fallback_hotels, city) for city in dests]
        guide_future   = ex.submit(run_cortex_search, dests, user_input)
        flights_outbound = [f.result() for f in flight_futures]
        flights_return   = [f.result() for f in return_futures] if return_futures else []
        hotels           = [h.result() for h in hotel_futures]
        guide            = guide_future.result()
    context = {"trip_details": travel, "flights_outbound": flights_outbound, "flights_return": flights_return, "hotels": hotels, "guide": guide}
    safe_context = make_json_safe(context)
    system_prompt = ("You are a travel planner. Using the provided SQL results for flights, hotels, "
        "and guide text, produce ONE detailed best recommendation.\n"
        "- Recommend specific flights (round trip if possible). If fallbacks were used, mention it briefly.\n"
        "- Recommend hotels (with reasoning).\n"
        "- Provide a day-wise sightseeing itinerary from guide text.\n"
        "- Be clear, concise, and structured (markdown).\n"
        "- Do NOT invent data not present in SQL results or guide text.\n"
        "- If a leg/city has no data (even after fallbacks), state it explicitly and proceed with available parts.")
    try: best_plan = str(Agent(model=MODEL_ID, system_prompt=system_prompt)(json.dumps(safe_context)))
    except Exception as e: best_plan = f"Could not generate final recommendation: {e}"
    return {"best_trip_recommendation": best_plan, "raw_context": safe_context}
app = BedrockAgentCoreApp()
@app.entrypoint
def invoke(payload):
    user_input = payload.get("prompt") or payload.get("query")
    mode = payload.get("mode", "standard")
    if mode == "react": return react_trip_agent(user_input)
    else: return trip_recommendation_agent(user_input)
if __name__ == "__main__": app.run()
