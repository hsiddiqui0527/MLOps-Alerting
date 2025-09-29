#!/bin/bash

# Full end-to-end test: Send error → Ask question → Get LLM response
# Usage: ./tests/curl_full_flow.sh

BASE_URL="http://localhost:8080"

echo "🔄 Running full flow test..."
echo ""

# Step 1: Send an error alert
echo "📨 Step 1: Sending authentication error..."
./tests/curl_error_alert.sh auth_timeout

echo ""
echo "⏳ Waiting 3 seconds for processing..."
sleep 3

# Step 2: Ask about the error
echo ""
echo "🤖 Step 2: Asking LLM about recent auth errors..."
./tests/curl_ask.sh "what authentication errors happened recently?" user-authentication 1

echo ""
echo "⏳ Waiting 2 seconds..."
sleep 2

# Step 3: Ask a follow-up question
echo ""
echo "🤖 Step 3: Asking follow-up question..."
./tests/curl_ask.sh "how many users were affected by the recent database timeout?"

echo ""
echo "✅ Full flow test complete!"
echo ""
echo "Expected results:"
echo "1. ✅ Error notification sent to Google Chat"
echo "2. ✅ Alert stored in BigQuery (if configured)"
echo "3. ✅ LLM answered questions using error context"
echo ""
echo "Check your Google Chat space and terminal logs to verify!"
