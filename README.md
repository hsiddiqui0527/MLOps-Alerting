# Chat Relay Service

Small FastAPI app that relays Google Cloud Monitoring alerts into a Google Chat space.

## Run locally
```bash
cp .env.example .env
export $(grep -v '^#' .env | xargs)
pip install -r requirements.txt
uvicorn app:app --reload --port 8080

