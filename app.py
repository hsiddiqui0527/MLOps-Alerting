import os
import re
import json
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# ---------- Env / Config ----------
load_dotenv()
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
CHAT_WEBHOOK_URL = os.getenv("CHAT_WEBHOOK_URL", "")
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")  # required for Vertex/BQ
LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")
MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-pro")

BQ_DATASET = os.getenv("BQ_DATASET", "chat_alert_test")
BQ_TABLE = os.getenv("BQ_TABLE", "alerts")
ASK_DEFAULT_SINCE_DAYS = int(os.getenv("ASK_DEFAULT_SINCE_DAYS", "7"))
ASK_MAX_ROWS = int(os.getenv("ASK_MAX_ROWS", "200"))
ASK_TS_COLUMN = os.getenv("ASK_TS_COLUMN", "ts")
ASK_SERVICE_COLUMN = os.getenv("ASK_SERVICE_COLUMN", "service")

# Lazy-load Google libs so the app can start even if they aren't installed (useful for quick testing)
_use_vertex = bool(PROJECT)
try:
    from google.cloud import aiplatform, bigquery
    from vertexai.generative_models import GenerativeModel
    aiplatform.init(project=PROJECT, location=LOCATION)
    _gen = GenerativeModel(MODEL) if _use_vertex else None
except Exception as _e:
    _use_vertex = False
    _gen = None

app = FastAPI()

# ---------- Data Models ----------
class ErrorAlert(BaseModel):
    service: str
    error_type: str
    message: str
    timestamp: Optional[str] = None
    stack_trace: Optional[str] = None
    affected_users: Optional[int] = None
    severity: str = "MEDIUM"
    recent_logs: Optional[list] = None
    environment: str = "production"

# ---------- Helpers ----------
def _reply_text(text: str, thread_name: Optional[str]) -> dict:
    # Return a plain dict instead of JSONResponse for Google Chat slash commands
    response = {"text": text}
    
    if thread_name:
        response["thread"] = {"name": thread_name}
    
    print(f"DEBUG - Returning response: {response}")
    return response  # Return dict directly, not JSONResponse


def _check_token(request: Request, event: dict):
    """Accept either header ('X-Goog-Chat-Token') or legacy body 'token'."""
    header_tok = request.headers.get("X-Goog-Chat-Token")
    body_tok = event.get("token")
    if VERIFY_TOKEN and (header_tok != VERIFY_TOKEN and body_tok != VERIFY_TOKEN):
        raise HTTPException(status_code=401, detail="Bad token")

def send_chat_notification(message: str) -> bool:
    """Send a message to Google Chat webhook."""
    if not CHAT_WEBHOOK_URL:
        print("⚠️  No CHAT_WEBHOOK_URL configured, skipping notification")
        return False
    
    try:
        response = requests.post(
            CHAT_WEBHOOK_URL,
            json={"text": message},
            timeout=10
        )
        response.raise_for_status()
        print(f"✅ Notification sent successfully: {response.status_code}")
        return True
    except requests.exceptions.RequestException as e:
        print(f"❌ Failed to send notification: {e}")
        return False

def format_error_notification(alert: ErrorAlert) -> str:
    """Format error alert into rich notification message."""
    # Get current timestamp if not provided
    timestamp = alert.timestamp or datetime.now(timezone.utc).isoformat()
    
    # Build severity emoji
    severity_emoji = {
        "LOW": "🟡",
        "MEDIUM": "🟠", 
        "HIGH": "🔴",
        "CRITICAL": "🚨"
    }.get(alert.severity.upper(), "⚪")
    
    message = f"""{severity_emoji} **Production Error Alert**

**Service:** {alert.service}
**Error:** {alert.error_type}
**Message:** {alert.message}
**Severity:** {alert.severity}
**Time:** {timestamp}
**Environment:** {alert.environment}"""

    if alert.affected_users:
        message += f"\n**Affected Users:** ~{alert.affected_users}"
    
    if alert.stack_trace:
        # Truncate long stack traces for chat
        truncated_trace = alert.stack_trace[:200] + "..." if len(alert.stack_trace) > 200 else alert.stack_trace
        message += f"\n**Stack Trace:** ```{truncated_trace}```"
    
    if alert.recent_logs:
        message += f"\n**Recent Logs:** {len(alert.recent_logs)} entries available"
    
    message += f"\n\n💬 *Type `/ask <question>` to investigate this error*"
    
    return message

def store_alert_in_bigquery(alert: ErrorAlert) -> bool:
    """Store alert in BigQuery for later /ask queries."""
    if not PROJECT:
        print("⚠️  No BigQuery project configured, skipping storage")
        return False
    
    try:
        client = bigquery.Client(project=PROJECT)
        table_id = f"{PROJECT}.{BQ_DATASET}.{BQ_TABLE}"
        
        # Convert alert to BigQuery row - MATCH YOUR ACTUAL SCHEMA
        row = {
            "ts": alert.timestamp or datetime.now(timezone.utc).isoformat(),
            "service": alert.service,
            "policy": alert.error_type,  # Map error_type to policy
            "state": "open",  # Default state
            "summary": alert.message,  # Map message to summary
            "severity": alert.severity,
            "error_type": alert.error_type,
            "resource": alert.service,  # Could map service to resource
            "url": None  # No URL in your alert model
        }
        
        errors = client.insert_rows_json(table_id, [row])
        if not errors:
            print(f"✅ Alert stored in BigQuery: {table_id}")
            return True
        else:
            print(f"❌ BigQuery insert errors: {errors}")
            return False
            
    except Exception as e:
        print(f"❌ Failed to store in BigQuery: {e}")
        return False

_filter_re = re.compile(r"(?:^|\s)(service:[^\s]+)|(since:\d+)", re.IGNORECASE)

def parse_prompt_and_filters(text: str) -> Tuple[str, Optional[str], int]:
    """
    Returns (prompt, service_filter, since_days)
    Supports: `service:<name>` and `since:<days>`
    Example: "why did it fail? service:rag-service since:3"
    """
    service = None
    since_days = ASK_DEFAULT_SINCE_DAYS

    parts = []
    for token in text.split():
        if token.lower().startswith("service:"):
            service = token.split(":", 1)[1]
        elif token.lower().startswith("since:"):
            try:
                since_days = max(1, int(token.split(":", 1)[1]))
            except ValueError:
                pass
        else:
            parts.append(token)
    prompt = " ".join(parts).strip()
    return prompt, service, since_days

def fetch_logs(service: Optional[str], since_days: int) -> list[dict]:
    """
    Pull recent rows from BigQuery to give the LLM some context.
    Assumes a timestamp column named ASK_TS_COLUMN and optional service column.
    """
    if not PROJECT:
        return []
    
    try:
        client = bigquery.Client(project=PROJECT)
        table_id = f"`{PROJECT}.{BQ_DATASET}.{BQ_TABLE}`"

        start_ts = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
        where = [f"{ASK_TS_COLUMN} >= TIMESTAMP('{start_ts}')"]
        if service:
            where.append(f"LOWER({ASK_SERVICE_COLUMN}) = LOWER(@service)")
        where_clause = " AND ".join(where)

        sql = f"""
            SELECT *
            FROM {table_id}
            WHERE {where_clause}
            ORDER BY {ASK_TS_COLUMN} DESC
            LIMIT {ASK_MAX_ROWS}
        """

        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("service", "STRING", service)
            ] if service else None
        )
        rows = client.query(sql, job_config=job_config).result()
        out = []
        for r in rows:
            out.append(dict(r))
        return out
    except Exception as e:
        print(f"❌ Failed to fetch logs from BigQuery: {e}")
        return []

def llm_answer(user_prompt: str, rows: list[dict]) -> str:
    """
    Compose a compact system prompt with rows (truncated) and ask the model to summarize / answer.
    """
    context_snippets = json.dumps(rows[: min(len(rows), 50)], default=str) if rows else "[]"
    sys = (
        "You are a reliability assistant. Use the provided recent logs to answer the user's question. "
        "Cite services, time ranges, and themes if visible; be concise and actionable."
    )
    prompt = f"{sys}\n\nRecent rows JSON (truncated):\n{context_snippets}\n\nUser question:\n{user_prompt}"
    if _gen:
        try:
            resp = _gen.generate_content(prompt)
            return resp.candidates[0].content.parts[0].text
        except Exception as e:
            return f"(Vertex error: {e})"
    # Fallback if Vertex not configured
    return f"(Vertex not configured) Based on recent rows ({len(rows)} found), no major anomalies detected. Question: {user_prompt}"

# ---------- Routes ----------
@app.get("/", response_class=PlainTextResponse)
def health():
    return "Chat Alert Bot - OK"

@app.post("/alert")
async def receive_alert(alert: ErrorAlert):
    print(f"📨 Received alert for service: {alert.service}")
    
    # Format and send notification
    notification_message = format_error_notification(alert)
    notification_sent = send_chat_notification(notification_message)
    print(f"🔔 Notification sent: {notification_sent}")
    
    # Store in BigQuery for /ask context
    bigquery_stored = store_alert_in_bigquery(alert)
    print(f"📊 BigQuery stored: {bigquery_stored}")  # Add this debug line
    
    return {
        "status": "processed",
        "service": alert.service,
        "notification_sent": notification_sent,
        "bigquery_stored": bigquery_stored,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/chat")
async def chat_endpoint(request: Request):
    """
    Handle Google Chat interactions, mainly the /ask command and mentions.
    """
    try:
        event = await request.json()
        print(f"DEBUG - Received event: {json.dumps(event, indent=2)}")
    except Exception as e:
        print(f"ERROR - JSON parsing failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # _check_token(request, event)  # Keep commented for now

    # Handle Google Chat slash commands (appCommandPayload format)
    if event.get("chat") and event.get("chat", {}).get("appCommandPayload"):
        print("DEBUG - Processing slash command")
        payload = event["chat"]["appCommandPayload"]
        message = payload.get("message", {})
        text = message.get("argumentText", "").strip()
        thread_name = message.get("thread", {}).get("name")
        
        print(f"DEBUG - Extracted text: '{text}'")
        print(f"DEBUG - Thread name: {thread_name}")
        
        if not text:
            return {"text": "Usage: `/ask <question> [service:... since:N]`\nExample: `/ask why is auth failing? service:user-auth since:1`"}

        user_prompt, service, since_days = parse_prompt_and_filters(text)
        print(f"DEBUG - Parsed prompt: '{user_prompt}', service: {service}, since: {since_days}")
        
        rows = fetch_logs(service, since_days)
        print(f"DEBUG - Fetched {len(rows)} rows from BigQuery")
        
        answer = llm_answer(user_prompt, rows)
        print(f"DEBUG - LLM answer: {answer[:100]}...")
        
        response_text = f"**Q:** {user_prompt}\n**A:** {answer}"
        if rows:
            response_text += f"\n\n*Based on {len(rows)} recent alerts*"
        
        print(f"DEBUG - Sending response")
        response_dict = {
            "text": response_text,
            "thread": {"name": thread_name} if thread_name else None
        }
        print(f"FINAL RESPONSE: {json.dumps(response_dict, indent=2)}")
        return response_dict

    # Handle mentions and messages (messagePayload format)
    if event.get("chat") and event.get("chat", {}).get("messagePayload"):
        print("DEBUG - Processing mention/message")
        payload = event["chat"]["messagePayload"]
        message = payload.get("message", {})
        text = message.get("argumentText", "").strip()
        thread_name = message.get("thread", {}).get("name")
        
        print(f"DEBUG - Extracted text: '{text}'")
        print(f"DEBUG - Thread name: {thread_name}")
        
        if not text:
            return {"text": "Usage: `@Prod Alert AI <question>` or `/ask <question>`\nExample: `@Prod Alert AI why is auth failing?`"}

        user_prompt, service, since_days = parse_prompt_and_filters(text)
        print(f"DEBUG - Parsed prompt: '{user_prompt}', service: {service}, since: {since_days}")
        
        rows = fetch_logs(service, since_days)
        print(f"DEBUG - Fetched {len(rows)} rows from BigQuery")
        
        answer = llm_answer(user_prompt, rows)
        print(f"DEBUG - LLM answer: {answer[:100]}...")
        
        response_text = f"**Q:** {user_prompt}\n**A:** {answer}"
        if rows:
            response_text += f"\n\n*Based on {len(rows)} recent alerts*"
        
        print(f"DEBUG - Sending response")
        response_dict = {
            "text": response_text,
            "thread": {"name": thread_name} if thread_name else None
        }
        print(f"FINAL RESPONSE: {json.dumps(response_dict, indent=2)}")
        return response_dict

    # Handle original event format (for curl tests and older integrations)
    thread_name = (
        event.get("message", {}).get("thread", {}).get("name")
        if event.get("message") else None
    )

    etype = event.get("type")
    if etype == "ADDED_TO_SPACE":
        return {"text": "Hey! I monitor production errors and can answer questions.\nType `/ask <question>` or mention me with `@Prod Alert AI <question>`."}

    if etype == "MESSAGE":
        # For slash commands, Google puts the user text in argumentText; for plain DM, it's in text.
        text = (event.get("message", {}).get("argumentText")
                or event.get("message", {}).get("text") or "").strip()

        if not text:
            return {"text": "Usage: `/ask <question> [service:... since:N]`\nExample: `/ask why is auth failing? service:user-auth since:1`"}

        user_prompt, service, since_days = parse_prompt_and_filters(text)
        rows = fetch_logs(service, since_days)

        answer = llm_answer(user_prompt, rows)
        response_text = f"**Q:** {user_prompt}\n**A:** {answer}"
        
        if rows:
            response_text += f"\n\n*Based on {len(rows)} recent alerts*"
        
        return {"text": response_text, "thread": {"name": thread_name} if thread_name else None}

    print("DEBUG - No matching event type, sending default response")
    return {"text": "I monitor production errors. Type `/ask <question>` or mention me to investigate."}