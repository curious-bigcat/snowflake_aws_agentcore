import os
import json
import re
from concurrent.futures import ThreadPoolExecutor
import snowflake.connector
from strands import Agent
from dotenv import load_dotenv
import requests
import datetime
import decimal
from bedrock_agentcore.runtime import BedrockAgentCoreApp

load_dotenv()

# --- ENV ---
MODEL_ID = os.getenv('MODEL_ID', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "TRAVEL_DB")
SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")
CORTEX_ANALYST_URL = os.getenv("CORTEX_ANALYST_URL", f"https://{SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/cortex/analyst/message")
SEMANTIC_MODEL_FLIGHT = os.getenv("SEMANTIC_MODEL_FILE", "@TRAVEL_DB.PUBLIC.DATA/FLIGHT_ANALYTICS.yaml")
SEMANTIC_MODEL_HOTEL = os.getenv("HOTEL_SEMANTIC_MODEL_FILE", '@"TRAVEL_DB"."PUBLIC"."DATA"/HOTEL_ANALYTICS.yaml')
CORTEX_SEARCH_DATABASE = os.getenv("CORTEX_SEARCH_DATABASE", "TRAVEL_DB")
CORTEX_SEARCH_SCHEMA = os.getenv("CORTEX_SEARCH_SCHEMA", "PUBLIC")
CORTEX_SEARCH_SERVICE = os.getenv("CORTEX_SEARCH_SERVICE", "TRAVEL_SEARCH_SERVICE")

# --- Utility: Snowflake connection ---
def _open_snowflake():
    return snowflake.connector.connect(
        user=SNOWFLAKE_USER, password=SNOWFLAKE_PASSWORD, account=SNOWFLAKE_ACCOUNT,
        database=SNOWFLAKE_DATABASE, schema=SNOWFLAKE_SCHEMA, warehouse=SNOWFLAKE_WAREHOUSE)

def execute_sql_on_snowflake(sql):
    ctx = None
    try:
        ctx = _open_snowflake()
        cs = ctx.cursor()
        try:
            cs.execute(sql)
            if cs.description:
                columns = [d[0] for d in cs.description]
                rows = cs.fetchall()
                return [dict(zip(columns, row)) for row in rows], None
            return [], None
        finally:
            cs.close()
    except Exception as e:
        return None, f"SQL execution error: {str(e)}"
    finally:
        if ctx:
            ctx.close()

# --- Extract Trip Details ---
def extract_trip_details(user_input, model=MODEL_ID):
    system_prompt = (
        "You are a travel assistant. Extract the travel intent into JSON:\n"
        "{ \"source_city\": <string>, \"destination_cities\": [<string>, ...] }\n"
        "- Only output valid JSON.\n"
        "- Preserve travel order."
    )
    agent = Agent(model=model, system_prompt=system_prompt)
    raw = str(agent(user_input))
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except:
            return None
    return None

# --- Cortex Analyst ---
def query_cortex_analyst(cortex_question, semantic_model_file):
    headers = {
        "Authorization": f"Bearer {SNOWFLAKE_PASSWORD}",  # replace with PAT if needed
        "Content-Type": "application/json",
        "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN"
    }
    body = {
        "messages": [{"role": "user", "content": [{"type": "text", "text": cortex_question}]}],
        "semantic_model_file": semantic_model_file
    }
    try:
        r = requests.post(CORTEX_ANALYST_URL, headers=headers, json=body, timeout=60)
        r.raise_for_status()
        analyst_response = r.json()
    except Exception as e:
        return {"error": f"Cortex Analyst error: {e}"}

    msg = analyst_response.get("message")
    contents = msg.get("content", []) if isinstance(msg, dict) else []

    analyst_text, sql, suggestions = None, None, []
    for c in contents:
        if isinstance(c, dict):
            if c.get("type") == "text" and not analyst_text:
                analyst_text = c.get("text")
            elif c.get("type") == "sql" and not sql:
                sql = c.get("statement") or c.get("sql")
            elif c.get("type") == "suggestions":
                suggestions.extend(map(str, c.get("suggestions", [])))

    sql_result, error = None, None
    if sql:
        sql_result, error = execute_sql_on_snowflake(sql)

    return {"analyst_text": analyst_text, "sql": sql, "sql_result": sql_result, "suggestions": suggestions, "error": error}

def make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return str(obj)
    elif isinstance(obj, decimal.Decimal):
        return float(obj)
    else:
        return obj

# --- Flight Agent ---
def flight_agent(travel, user_input):
    try:
        source = travel["source_city"]
        dests = travel["destination_cities"]
        segments = [(source, d) for d in dests] + ([(dests[-1], source)] if dests else [])
        segment_results, all_sql_results = [], []

        for seg_from, seg_to in segments:
            cortex_question = f"Find flights from {seg_from} to {seg_to}."
            result = query_cortex_analyst(cortex_question, SEMANTIC_MODEL_FLIGHT)
            segment_results.append({"from": seg_from, "to": seg_to, "result": result})
            res = result.get("sql_result")
            if res:
                all_sql_results.append({"from": seg_from, "to": seg_to, "flights": make_json_safe(res)})

        rec_prompt = "Recommend best flights strictly from SQL results. Return markdown."
        context = {"user_input": user_input, "segments": all_sql_results}
        try:
            best = str(Agent(model=MODEL_ID, system_prompt=rec_prompt)(json.dumps(context)))
        except Exception as e:
            best = f"Could not generate recommendation: {e}"

        first_result = segment_results[0]["result"] if segment_results else {}
        return {"segments": segment_results, "best_flight_recommendation": best, "analyst_text": first_result.get("analyst_text"), "sql": first_result.get("sql"), "sql_result": first_result.get("sql_result"), "suggestions": first_result.get("suggestions"), "error": first_result.get("error")}
    except Exception as e:
        return {"error": f"Flight agent error: {e}"}

# --- Hotel Agent ---
def hotel_agent(travel, user_input):
    try:
        source = travel["source_city"]
        dests = [d for d in travel["destination_cities"] if d.lower() != source.lower()]
        city_results, all_sql_results = [], []
        for city in dests:
            result = query_cortex_analyst(f"Find hotels in {city}.", SEMANTIC_MODEL_HOTEL)
            city_results.append({"city": city, "result": result})
            res = result.get("sql_result")
            if res:
                all_sql_results.append({"city": city, "hotels": make_json_safe(res)})

        rec_prompt = "Recommend best hotels strictly from SQL results. Return markdown."
        context = {"user_input": user_input, "cities": all_sql_results}
        try:
            best = str(Agent(model=MODEL_ID, system_prompt=rec_prompt)(json.dumps(context)))
        except Exception as e:
            best = f"Could not generate recommendation: {e}"

        first_result = city_results[0]["result"] if city_results else {}
        return {"city_results": city_results, "best_hotel_recommendation": best, "analyst_text": first_result.get("analyst_text"), "sql": first_result.get("sql"), "sql_result": first_result.get("sql_result"), "suggestions": first_result.get("suggestions"), "error": first_result.get("error")}
    except Exception as e:
        return {"error": f"Hotel agent error: {e}"}

# --- Guide Agent ---
def guide_agent(travel, user_input):
    try:
        dests = [d for d in travel.get("destination_cities", []) if d.lower() != travel.get("source_city", "").lower()]
        search_query = f"travel guide for {' to '.join(dests)}" if dests else user_input
        guide_text, results = "", []
        try:
            from snowflake.core import Root
            from snowflake.snowpark import Session
            session = Session.builder.configs({
                "account": SNOWFLAKE_ACCOUNT, "user": SNOWFLAKE_USER, "password": SNOWFLAKE_PASSWORD,
                "warehouse": SNOWFLAKE_WAREHOUSE, "database": SNOWFLAKE_DATABASE, "schema": SNOWFLAKE_SCHEMA,
                "role": os.getenv("SNOWFLAKE_ROLE", "test_role")
            }).create()
            root = Root(session)
            service = root.databases[CORTEX_SEARCH_DATABASE].schemas[CORTEX_SEARCH_SCHEMA].cortex_search_services[CORTEX_SEARCH_SERVICE]
            resp = service.search(query=search_query, columns=["CHUNK"], limit=10)
            results = resp.to_dict() if hasattr(resp, "to_dict") else resp
            if isinstance(results, dict) and "data" in results:
                guide_text = "\n".join([str(r["CHUNK"]) for r in results["data"] if r.get("CHUNK")])
        except Exception:
            pass

        prompt = "Create a day-wise itinerary using guide text. Only include destination cities."
        context = {"user_input": user_input, "travel_guide": guide_text, "itinerary_cities": dests}
        try:
            plan = str(Agent(model=MODEL_ID, system_prompt=prompt)(json.dumps(context)))
        except Exception as e:
            plan = f"Could not generate plan: {e}"

        return {"search_query": search_query, "results": results, "daywise_plan": plan}
    except Exception as e:
        return {"error": f"Guide agent error: {e}"}

# --- Coordinator Agent ---
def coordinator_agent(user_input):
    travel = extract_trip_details(user_input)
    if not travel:
        return {"error": "Could not extract trip details from input."}
    with ThreadPoolExecutor() as ex:
        f, h, g = ex.submit(flight_agent, travel, user_input), ex.submit(hotel_agent, travel, user_input), ex.submit(guide_agent, travel, user_input)
        flight, hotel, guide = f.result(), h.result(), g.result()
    return {"flight": flight if isinstance(flight, dict) else {"error": str(flight)},
            "hotel": hotel if isinstance(hotel, dict) else {"error": str(hotel)},
            "guide": guide if isinstance(guide, dict) else {"error": str(guide)}}

# --- FastAPI App ---
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    user_input = payload.get("prompt") or payload.get("query")
    return coordinator_agent(user_input)

if __name__ == "__main__":
    app.run()
