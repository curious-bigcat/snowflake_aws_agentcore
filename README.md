# Travel Planner AI

## Overview
Travel Planner AI is an intelligent, multi-agent travel planning system that generates complete travel itineraries—including flights, hotels, and day-wise plans—from a single user prompt. It leverages LLMs (Anthropic Claude), Snowflake Cortex Analyst & Search, and AWS Bedrock AgentCore for robust, scalable, and user-friendly travel planning.

---

## Features
- **All-in-one prompt:** Enter your travel needs in natural language; get flights, hotels, and itinerary.
- **Multi-city, round-trip support:** Handles complex itineraries and preferences.
- **LLM-powered recommendations:** Uses Claude for intent extraction and plan generation.
- **Real-time data:** Queries live flight/hotel data and travel guides from Snowflake.
- **Modern UI:** Streamlit frontend with tabs, tables, and professional design.
- **Cloud-native:** Deployable to AWS Bedrock AgentCore for production use.

---

## Architecture & Tech Stack
- **Frontend:** Streamlit (Python)
- **Backend:** Python, BedrockAgentCoreApp (for AWS), FastAPI (for local/dev)
- **Data/AI:**
  - Snowflake Cortex Analyst (NL-to-SQL for flights/hotels)
  - Snowflake Cortex Search (semantic search for travel guides)
  - Anthropic Claude (via AWS Bedrock)
  - AWS Bedrock AgentCore & Strands (agent orchestration)
- **Deployment:** Docker, AWS Bedrock AgentCore Runtime

---

## Project Structure
```
agentcore/
├── all_in_one_travel_agent.py         # Main agent code (BedrockAgentCoreApp)
├── streamlit_coordinator_travel_agent.py # Streamlit UI
├── requirements.txt                   # Python dependencies
├── bedrock_agentcore.yaml             # AgentCore config
├── Dockerfile                         # For container builds
├── agentcore-prereqs.yaml             # CloudFormation for IAM role & ECR
├── snowflake_setup_worksheet.sql      # Step-by-step Snowflake setup worksheet
├── backup/                            # Backups and legacy code
├── flight_data.csv                    # Sample flight data (for Snowflake)
├── hotel_data.csv                     # Sample hotel data (for Snowflake)
├── Travel_Plan_Guide.pdf              # Travel guide PDF (for Cortex Search)
├── ...
```

---

## Snowflake Setup (Database, Tables, and Cortex Search)

**Follow the step-by-step worksheet:**

➡️  [`snowflake_setup_worksheet.sql`](./snowflake_setup_worksheet.sql)

This file contains all the SQL commands and instructions to:
- Create the database, stages, and network policy
- Upload and load your flight/hotel CSVs and travel guide PDF
- Create and populate the required tables
- Parse and chunk the travel guide for Cortex Search
- Create the Cortex Search service
- Test your data and search service

**Open the worksheet in Snowflake Web UI or your SQL editor and execute each step in order.**

---

## CloudFormation: Prerequisites Setup (IAM Role & ECR)

### **A. Deploy the Prerequisites Stack**
This will create the required IAM execution role and ECR repository for Bedrock AgentCore.

```sh
aws cloudformation create-stack \
  --stack-name agentcore-prereqs \
  --template-body file://agentcore-prereqs.yaml \
  --capabilities CAPABILITY_NAMED_IAM
```
- **Outputs:**
  - `ExecutionRoleArn`: Use this for agentcore configure
  - `ECRRepositoryUri`: Used internally by agentcore

---

## Local Development & Testing

### **A. Run the Agent Locally (for dev/testing)**
1. **Launch the agent (local mode):**
   ```sh
   agentcore launch -l
   # or
   python all_in_one_travel_agent.py
   ```
   The agent will be available at `http://localhost:8080/invocations`.

2. **Test with curl:**
   ```sh
   curl -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "I want to go from Delhi to Chennai and return for 3 nights, need a hotel with breakfast, and a sightseeing plan"}'
   ```

3. **Run the Streamlit UI:**
   ```sh
   export AGENT_ENDPOINT="http://localhost:8080/invocations"
   streamlit run streamlit_coordinator_travel_agent.py
   ```

---

## Cloud Deployment: AWS Bedrock AgentCore Runtime

### **A. Configure the Agent**
```sh
agentcore configure --entrypoint all_in_one_travel_agent.py -er <YOUR_EXECUTION_ROLE_ARN>
```
- Use the `ExecutionRoleArn` output from the CloudFormation stack.

### **B. Deploy to AWS**
```sh
agentcore launch
```
- This will build, push, and deploy your agent to Bedrock AgentCore Runtime.
- Note the **HTTPS endpoint** from the output or run `agentcore status` to get it.

### **C. Update Streamlit for Cloud**
```sh
export AGENT_ENDPOINT="https://<your-bedrock-agentcore-endpoint>/invocations"
streamlit run streamlit_coordinator_travel_agent.py
```
- The app will now connect to your cloud agent.

---

## Environment Variables
- **AGENT_ENDPOINT**: The HTTPS endpoint for your Bedrock AgentCore agent (required for Streamlit UI in cloud mode).
- **SNOWFLAKE_***: All Snowflake connection details.
- **MODEL_ID**: Claude model ID for Bedrock.
- **Other**: Any additional config for your data/models.

---

## Troubleshooting
- **Streamlit error: AGENT_ENDPOINT not set**
  - Set the endpoint as described above.
- **Request failed: ...**
  - Check agent logs in AWS CloudWatch.
  - Ensure the agent is running and the endpoint is correct.
- **Port 8080 in use (local)**
  - Free the port: `lsof -i :8080` then `kill -9 <PID>`
- **Agent returns string, not JSON**
  - Ensure your entrypoint returns a dict, not a string.
- **OpenTelemetry/OTLP errors**
  - These are non-blocking unless you want tracing. Ignore or disable tracing if not needed.

---

## Advanced
- **Logs:**
  - Tail logs: `aws logs tail /aws/bedrock-agentcore/runtimes/<agent-name>-DEFAULT --follow`
- **Invoke via CLI:**
  - `agentcore invoke '{"prompt": "Hello"}'`
- **Multiple environments:**
  - Use `.env` files or export variables per environment.

---

## Contributing
Pull requests and issues are welcome! Please ensure code is well-documented and tested.

---

## License
[MIT License](LICENSE)
