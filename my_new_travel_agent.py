import os
import json
import re
from concurrent.futures import ThreadPoolExecutor
import snowflake.connector
from strands import Agent
import requests
import datetime
import decimal
from bedrock_agentcore.runtime import BedrockAgentCoreApp


# --- Load Secrets from AWS Secrets Manager ---
def load_secrets_from_aws(secret_name, region_name=None):
    try:
        import boto3
        session = boto3.session.Session()
        if region_name is None:
            region_name = os.environ.get('AWS_REGION', 'us-east-1')
        client = session.client(service_name='secretsmanager', region_name=region_name)
        get_secret_value_response = client.get_secret_value(SecretId=secret_name)
        secret = get_secret_value_response['SecretString']
        secrets_dict = json.loads(secret)
        for k, v in secrets_dict.items():
            os.environ[k] = v
        return secrets_dict
    except Exception as e:
        print(f"Warning: Could not load secrets from AWS Secrets Manager: {e}")
        return {}


def try_load_secrets():
    secret_name = os.environ.get(
        'AGENTCORE_SECRET_NAME',
        'arn:aws:secretsmanager:us-east-1:484577546576:secret:agentcore/travelplanner/credentials-hmfGXv'
    )
    if secret_name:
        secrets = load_secrets_from_aws(secret_name)
        if secrets:
            print("✅ Loaded secrets from Secrets Manager:", list(secrets.keys()))
        else:
            print("⚠️ No secrets loaded, check Secret ARN or IAM permissions")


# --- MUST load secrets before anything else ---
try_load_secrets()


# --- ENV VARS (now populated from secrets) ---
MODEL_ID = os.getenv('MODEL_ID', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')
SNOWFLAKE_ACCOUNT = os.getenv("SNOWFLAKE_ACCOUNT")
if not SNOWFLAKE_ACCOUNT:
    raise ValueError("❌ SNOWFLAKE_ACCOUNT is not set. Check Secrets Manager config.")

SNOWFLAKE_USER = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_DATABASE = os.getenv("SNOWFLAKE_DATABASE", "TRAVEL_DB")
SNOWFLAKE_SCHEMA = os.getenv("SNOWFLAKE_SCHEMA", "PUBLIC")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE", "XSMALL_WH")

CORTEX_ANALYST_URL = os.getenv(
    "CORTEX_ANALYST_URL",
    f"https://{SNOWFLAKE_ACCOUNT}.snowflakecomputing.com/api/v2/cortex/analyst/message"
)
SEMANTIC_MODEL_FLIGHT = os.getenv("SEMANTIC_MODEL_FILE", "@TRAVEL_DB.PUBLIC.DATA/FLIGHT_ANALYTICS.yaml")
SEMANTIC_MODEL_HOTEL = os.getenv("HOTEL_SEMANTIC_MODEL_FILE", '@TRAVEL_DB.PUBLIC.DATA/HOTEL_ANALYTICS.yaml')
CORTEX_SEARCH_DATABASE = os.getenv("CORTEX_SEARCH_DATABASE", "TRAVEL_DB")
CORTEX_SEARCH_SCHEMA = os.getenv("CORTEX_SEARCH_SCHEMA", "PUBLIC")
CORTEX_SEARCH_SERVICE = os.getenv("CORTEX_SEARCH_SERVICE", "TRAVEL_SEARCH_SERVICE")


# --- Snowflake connection helper ---
def _open_snowflake():
    return snowflake.connector.connect(
        user=SNOWFLAKE_USER,
        password=SNOWFLAKE_PASSWORD,
        account=SNOWFLAKE_ACCOUNT,
        database=SNOWFLAKE_DATABASE,
        schema=SNOWFLAKE_SCHEMA,
        warehouse=SNOWFLAKE_WAREHOUSE
    )


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


# --- JSON safety helper ---
def make_json_safe(obj):
    if isinstance(obj, dict):
        return {k: make_json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [make_json_safe(v) for v in obj]
    elif isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return str(obj)  # convert to string
    elif isinstance(obj, decimal.Decimal):
        return float(obj)  # convert to float
    else:
        return obj


# --- Trip details extraction ---
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
        except json.JSONDecodeError:
            return None
    return None


# --- Cortex Analyst call ---
def query_cortex_analyst(cortex_question, semantic_model_file):
    token = os.getenv("SNOWFLAKE_AUTH_TOKEN")
    if not token:
        return {"error": "SNOWFLAKE_AUTH_TOKEN is not set. Check Secrets Manager."}

    headers = {
        "Authorization": f"Bearer {token}",
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

    return {
        "analyst_text": analyst_text,
        "sql": sql,
        "sql_result": make_json_safe(sql_result) if sql_result else None,
        "suggestions": suggestions,
        "error": error
    }


# --- Cortex Search helper ---
def run_cortex_search(dests, user_input):
    search_query = f"travel guide for {' to '.join(dests)}" if dests else user_input
    try:
        from snowflake.core import Root
        from snowflake.snowpark import Session
        session = Session.builder.configs({
            "account": SNOWFLAKE_ACCOUNT,
            "user": SNOWFLAKE_USER,
            "password": SNOWFLAKE_PASSWORD,
            "warehouse": SNOWFLAKE_WAREHOUSE,
            "database": SNOWFLAKE_DATABASE,
            "schema": SNOWFLAKE_SCHEMA,
            "role": os.getenv("SNOWFLAKE_ROLE", "PUBLIC")
        }).create()
        root = Root(session)
        service = root.databases[CORTEX_SEARCH_DATABASE].schemas[CORTEX_SEARCH_SCHEMA].cortex_search_services[CORTEX_SEARCH_SERVICE]
        resp = service.search(query=search_query, columns=["CHUNK"], limit=10)
        results = resp.to_dict() if hasattr(resp, "to_dict") else resp
        if isinstance(results, dict) and "data" in results:
            guide_text = "\n".join([str(r["CHUNK"]) for r in results["data"] if r.get("CHUNK")])
            return {"query": search_query, "results": results, "guide_text": guide_text}
        return {"query": search_query, "results": results}
    except Exception as e:
        return {"query": search_query, "error": str(e)}


# --- Unified Trip Recommendation Agent ---
def trip_recommendation_agent(user_input):
    travel = extract_trip_details(user_input)
    if not travel:
        return {"error": "❌ Could not extract trip details from input."}

    source = travel["source_city"]
    dests = travel["destination_cities"]

    with ThreadPoolExecutor() as ex:
        flight_futures = [ex.submit(query_cortex_analyst, f"Find flights from {source} to {d}.", SEMANTIC_MODEL_FLIGHT) for d in dests]
        return_futures = [ex.submit(query_cortex_analyst, f"Find flights from {dests[-1]} to {source}.", SEMANTIC_MODEL_FLIGHT)] if dests else []
        hotel_futures = [ex.submit(query_cortex_analyst, f"Find hotels in {city}.", SEMANTIC_MODEL_HOTEL) for city in dests]
        guide_future = ex.submit(run_cortex_search, dests, user_input)

    flights = [f.result() for f in flight_futures + return_futures]
    hotels = [h.result() for h in hotel_futures]
    guide = guide_future.result()

    context = {
        "trip_details": travel,
        "flights": flights,
        "hotels": hotels,
        "guide": guide
    }

    # ✅ Make everything JSON safe
    safe_context = make_json_safe(context)

    system_prompt = (
        "You are a travel planner. Using the provided SQL results for flights, hotels, "
        "and guide text, produce ONE detailed best recommendation.\n"
        "- Recommend specific flights (round trip).\n"
        "- Recommend hotels (with reasoning).\n"
        "- Provide a day-wise sightseeing itinerary from guide text.\n"
        "- Be clear, concise, and structured (markdown).\n"
        "- Do NOT invent data not present in SQL results or guide text."
    )

    try:
        best_plan = str(Agent(model=MODEL_ID, system_prompt=system_prompt)(json.dumps(safe_context)))
    except Exception as e:
        best_plan = f"❌ Could not generate final recommendation: {e}"

    return {"best_trip_recommendation": best_plan, "raw_context": safe_context}


# --- App Entrypoint ---
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    user_input = payload.get("prompt") or payload.get("query")
    return trip_recommendation_agent(user_input)


if __name__ == "__main__":
    app.run()
