import json
import os
import logging
import azure.functions as func

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# Ensure the Agents subclient is available
try:
    import azure.ai.agents  # noqa: F401
except ImportError as exc:
    raise RuntimeError("Missing 'azure-ai-agents'. Install per requirements.txt") from exc

# --- Config from App Settings (Function App > Configuration) ---
PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")   # e.g. https://<project>.<region>.api.cognitive.microsoft.com
AGENT_ID        = os.getenv("AZURE_AI_USER_AGENT_ID")       # your agent id, e.g. asst_xxx

if not PROJECT_ENDPOINT or not AGENT_ID:
    raise RuntimeError("Set AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_USER_AGENT_ID in App Settings.")

# --- Managed Identity / Entra ID auth ---
credential = DefaultAzureCredential()

# --- Project/Agents client (module-level: reuse across invocations) ---
project_client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
agents = project_client.agents

def _extract_assistant_reply(messages_list) -> str | None:
    # Prefer convenience property if present
    if hasattr(messages_list, "text_messages") and messages_list.text_messages:
        for m in reversed(messages_list.text_messages):
            if getattr(m, "role", None) == "assistant":
                # m.text may be plain string or a typed object with .value
                text = getattr(getattr(m, "text", None), "value", None) or getattr(m, "text", None)
                if text:
                    return text
    # Fallback: walk raw data
    for m in reversed(getattr(messages_list, "data", []) or []):
        if getattr(m, "role", None) == "assistant":
            for b in getattr(m, "content", []) or []:
                text = getattr(getattr(b, "text", None), "value", None) or getattr(b, "text", None) or getattr(b, "value", None)
                if text:
                    return text
    return None

def _cors_headers():
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Allow-Methods": "POST,OPTIONS"
    }

def main(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return func.HttpResponse(status_code=204, headers=_cors_headers())

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
            headers=_cors_headers()
        )

    message   = (body or {}).get("message")
    thread_id = (body or {}).get("thread_id")

    if not message:
        return func.HttpResponse(
            json.dumps({"error": "Field 'message' is required"}),
            status_code=400,
            mimetype="application/json",
            headers=_cors_headers()
        )

    try:
        # 1) Create or reuse a thread
        if not thread_id:
            thread = agents.threads.create()
            thread_id = thread.id

        # 2) Add the user message
        agents.messages.create(thread_id=thread_id, role="user", content=message)

        # 3) Run the agent and wait until it’s finished
        run = agents.runs.create_and_process(thread_id=thread_id, agent_id=AGENT_ID)

        # 4) Read messages and extract the last assistant reply
        msgs = agents.messages.list(thread_id=thread_id)
        reply_text = _extract_assistant_reply(msgs)

        resp = {
            "thread_id": thread_id,
            "run_status": getattr(run, "status", None),
            "output": reply_text
        }
        return func.HttpResponse(json.dumps(resp), status_code=200, mimetype="application/json", headers=_cors_headers())

    except Exception as e:
        logging.exception("Agent call failed")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
            headers=_cors_headers()
        )
