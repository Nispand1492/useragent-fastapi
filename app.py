# app.py
import os
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# require the agents package at runtime (needed for .agents subclient)
try:
    import azure.ai.agents  # noqa: F401
except ImportError as exc:
    raise RuntimeError("Install 'azure-ai-agents' (pip install azure-ai-agents)") from exc

load_dotenv()

PROJECT_ENDPOINT = "https://daywell-ai.eastus.api.cognitive.microsoft.com"
USER_AGENT_ID = "asst_6kpuVo6jJiveT5gGBUQW2Jtw"
CORS_ORIGINS = "http://localhost:3000"

if not PROJECT_ENDPOINT or not USER_AGENT_ID:
    raise RuntimeError("Set AZURE_AI_PROJECT_ENDPOINT and AZURE_AI_USER_AGENT_ID in env/.env")

# Entra ID auth (Managed Identity / SP / Azure CLI)
credential = DefaultAzureCredential()
project = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)
agents = project.agents  # AgentsClient

app = FastAPI(title="UserAgent Gateway", version="1.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in CORS_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatIn(BaseModel):
    message: str
    thread_id: Optional[str] = None  # reuse to keep the same conversation

@app.get("/healthz")
def health():
    return {"status": "ok"}

@app.post("/chat")
def chat(req: ChatIn):
    try:
        # 1) Create or reuse a thread
        thread_id = req.thread_id
        if not thread_id:
            thread = agents.threads.create()
            thread_id = thread.id

        # 2) Add the user message (role is a STRING)
        agents.messages.create(thread_id=thread_id, role="user", content=req.message)

        # 3) Run the agent and wait until it’s done
        run = agents.runs.create_and_process(thread_id=thread_id, agent_id=USER_AGENT_ID)

        # 4) Fetch messages and extract the latest assistant reply
        msgs = agents.messages.list(thread_id=thread_id)
        reply_text = None

        # helper property available in recent SDKs
        if hasattr(msgs, "text_messages") and msgs.text_messages: # type: ignore
            for m in reversed(msgs.text_messages): # type: ignore
                if getattr(m, "role", None) == "assistant":
                    reply_text = getattr(getattr(m, "text", None), "value", None) or getattr(m, "text", None)
                    if reply_text:
                        break

        if reply_text is None:
            # fallback: inspect raw data
            for m in reversed(getattr(msgs, "data", []) or []):
                if getattr(m, "role", None) == "assistant":
                    for b in getattr(m, "content", []) or []:
                        reply_text = getattr(getattr(b, "text", None), "value", None) or getattr(b, "text", None) or getattr(b, "value", None)
                        if reply_text:
                            break
                if reply_text:
                    break

        return {
            "thread_id": thread_id,
            "run_status": getattr(run, "status", None),
            "output": reply_text,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
