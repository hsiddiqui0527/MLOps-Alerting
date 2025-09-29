#!/bin/bash

# Test script for asking questions to the LLM
# Usage: ./tests/curl_ask.sh "your question here" [service] [since_days]

BASE_URL="http://localhost:8080"
QUESTION="$1"
SERVICE="$2"
SINCE_DAYS="$3"

if [ -z "$QUESTION" ]; then
    echo "❌ Usage: $0 \"your question here\" [service] [since_days]"
    echo ""
    echo "Examples:"
    echo "  $0 \"what errors happened today?\""
    echo "  $0 \"why did auth fail?\" user-authentication 1"
    echo "  $0 \"show me payment issues\" payment-processor 7"
    exit 1
fi

echo "🤖 Asking LLM: \"$QUESTION\""

# Build the argument text with filters
ARGUMENT_TEXT="$QUESTION"
if [ ! -z "$SERVICE" ]; then
    ARGUMENT_TEXT="$ARGUMENT_TEXT service:$SERVICE"
fi
if [ ! -z "$SINCE_DAYS" ]; then
    ARGUMENT_TEXT="$ARGUMENT_TEXT since:$SINCE_DAYS"
fi

echo "📝 Full query: \"$ARGUMENT_TEXT\""
echo ""

curl -X POST $BASE_URL/chat \
  -H "Content-Type: application/json" \
  -d "{
        \"type\": \"MESSAGE\",
        \"token\": \"chat-alert-1!\",
        \"message\": {
          \"argumentText\": \"$ARGUMENT_TEXT\",
          \"thread\": {\"name\": \"spaces/AAAA/thread-test\"}
        }
      }"

echo ""
echo ""
echo "✅ Question sent to LLM!"
