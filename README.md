[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

# Travel Planner AI – Enterprise-Grade Multi-Agent Travel Planning

---

## Badges
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)]()
[![AWS](https://img.shields.io/badge/deploys%20to-AWS%20Bedrock%20AgentCore-orange)]()

---

## Overview
Travel Planner AI is a robust, production-ready, multi-agent travel planning system. It generates complete, data-driven travel itineraries—including flights, hotels, and day-wise plans—from a single user prompt. The system leverages:
- **LLMs (Anthropic Claude via AWS Bedrock)** for intent extraction, reasoning, and plan generation
- **Snowflake Cortex Analyst & Search** for real-time flight/hotel data and semantic travel guide search
- **AWS Bedrock AgentCore** for secure, scalable orchestration
- **Streamlit** for a modern, interactive frontend

This solution is designed for both enterprise and open-source deployments, with a focus on security, modularity, and extensibility.

---

## Features
- **Unified Prompt:** Enter your travel needs in natural language; get flights, hotels, and a day-wise itinerary.
- **Multi-city, Round-trip Support:** Handles complex itineraries and user preferences.
- **LLM-Powered Recommendations:** Uses Claude for intent extraction, reasoning, and plan generation.
- **Live Data:** Queries real-time flight/hotel data and travel guides from Snowflake.
- **Modern UI:** Streamlit frontend with tabs, tables, and professional design.
- **Cloud-Native:** Deployable to AWS Bedrock AgentCore for production use.
- **Secure Secrets Management:** Uses AWS Secrets Manager for all credentials and sensitive config.
- **User Feedback:** Collects thumbs up/down feedback on recommendations for continuous improvement.

---

## Architecture

**Tech Stack:**
- **Frontend:** Streamlit (Python)
- **Backend:** Python, BedrockAgentCoreApp (AWS), FastAPI (local/dev)
- **Data/AI:**
  - Snowflake Cortex Analyst (NL-to-SQL for flights/hotels)
  - Snowflake Cortex Search (semantic search for travel guides)
  - Anthropic Claude (via AWS Bedrock)
  - AWS Bedrock AgentCore & Strands (agent orchestration)
- **Deployment:** Docker, AWS Bedrock AgentCore Runtime
- **Secrets:** AWS Secrets Manager

**High-Level Flow:**
1. User submits a travel prompt via Streamlit UI
2. Backend agent extracts intent, queries Snowflake for flights/hotels, and uses Claude for reasoning
3. Results are combined and presented in a modern UI, with feedback options
4. All credentials are securely loaded from AWS Secrets Manager

---

## Project Structure
```
agentcore/
├── my_new_travel_agent.py              # Main agent code (BedrockAgentCoreApp)
├── streamlit_coordinator_travel_agent.py # Streamlit UI
├── requirements.txt                   # Python dependencies
├── bedrock_agentcore.yaml             # AgentCore config
├── Dockerfile                         # For container builds
├── agentcore-prereqs.yaml             # CloudFormation for IAM, ECR, Secrets Manager
├── snowflake_setup_worksheet.sql      # Step-by-step Snowflake setup worksheet
├── FLIGHT.csv, HOTEL.csv              # Sample data for Snowflake
├── Travel_Plan_Guide.pdf              # Travel guide PDF (for Cortex Search)
├── ...
```

---

## Setup & Deployment

### 1. **Snowflake Setup**
- Follow [`snowflake_setup_worksheet.sql`](./snowflake_setup_worksheet.sql) to:
  - Create the database, stages, and network policy
  - Upload and load your flight/hotel CSVs and travel guide PDF
  - Create and populate the required tables
  - Parse and chunk the travel guide for Cortex Search
  - Create the Cortex Search service

### 2. **CloudFormation Prerequisites**
- Deploy the stack to create IAM role, ECR repo, and a Secrets Manager secret:
  ```sh
  aws cloudformation create-stack \
    --stack-name agentcore-prereqs \
    --template-body file://agentcore-prereqs.yaml \
    --capabilities CAPABILITY_NAMED_IAM
  ```
- You will be prompted for all required secret values (Snowflake, model ID, etc.)
- Outputs:
  - `ExecutionRoleArn`: Use for agentcore configure
  - `ECRRepositoryUri`: Used internally by agentcore
  - `AgentCoreSecretArn`: Use as `AGENTCORE_SECRET_NAME` env var

### 3. **Local Development & Testing**
- Clone the repo and set up your Python environment:
  ```sh
  git clone https://github.com/curious-bigcat/agentcore_snowflake.git
  cd agentcore_snowflake/agentcore
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```
- Set the secret ARN:
  ```sh
  export AGENTCORE_SECRET_NAME=<AgentCoreSecretArn>
  ```
- Launch the agent locally:
  ```sh
  agentcore launch -l
  # or
  python my_new_travel_agent.py
  ```
- Run the Streamlit UI:
  ```sh
  export AGENT_ENDPOINT="http://localhost:8080/invocations"
  streamlit run streamlit_coordinator_travel_agent.py
  ```

### 4. **Cloud Deployment: AWS Bedrock AgentCore**
- Configure and launch:
  ```sh
  agentcore configure --entrypoint my_new_travel_agent.py -er <YOUR_EXECUTION_ROLE_ARN>
  agentcore launch
  ```
- Update Streamlit to use the cloud endpoint:
  ```sh
  export AGENT_ENDPOINT="https://<your-bedrock-agentcore-endpoint>/invocations"
  streamlit run streamlit_coordinator_travel_agent.py
  ```

---

## Security & Best Practices
- **Secrets:** All credentials are stored in AWS Secrets Manager and loaded at runtime. Never hardcode secrets.
- **IAM:** The agent runs with least-privilege IAM permissions (see CloudFormation template).
- **Networking:** For production, restrict access to ECR, Secrets Manager, and Bedrock AgentCore via VPC or security groups.
- **Auditing:** Use AWS CloudTrail and CloudWatch for monitoring and auditing agent activity.
- **Data:** All data at rest and in transit is encrypted by default (Snowflake, AWS, etc.).

---

## Troubleshooting & FAQ
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
- **Secrets not loading**
  - Ensure `AGENTCORE_SECRET_NAME` is set to the correct ARN and IAM permissions are correct.
- **Snowflake connection errors**
  - Double-check all Snowflake credentials and network access.

---

## User Feedback
The Streamlit UI allows users to provide feedback on flight and hotel recommendations using 👍 and 👎 buttons. This helps improve the quality of recommendations and user experience.

---

## Contributing & Support
- Fork the repository and create your branch from `main`.
- Make your changes and ensure all tests pass.
- Submit a pull request describing your changes.
- For issues, use GitHub Issues or Discussions.
- For enterprise support, contact the project maintainer.

---

## License
[MIT License](LICENSE)
