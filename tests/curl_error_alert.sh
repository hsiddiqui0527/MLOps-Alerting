#!/bin/bash
# Test script for sending error alerts to the bot
# Usage: ./tests/curl_error_alert.sh [error_type]

BASE_URL="http://localhost:8080"
ERROR_TYPE=${1:-"auth_timeout"}

echo "🧪 Testing error alert: $ERROR_TYPE"

case $ERROR_TYPE in
    "auth_timeout")
        echo "📨 Sending auth timeout error..."
        curl -X POST $BASE_URL/alert \
          -H "Content-Type: application/json" \
          -d '{
                "service": "user-authentication",
                "error_type": "Database Connection Timeout",
                "message": "Connection to primary auth database timed out after 30 seconds",
                "severity": "HIGH",
                "affected_users": 150,
                "stack_trace": "ConnectionError: timeout\n  at DatabaseClient.connect(auth_db.py:42)\n  at AuthService.validate_user(auth.py:128)",
                "environment": "production",
                "recent_logs": [
                    "2024-09-16 14:29:45 - WARNING: Slow query detected (28s)",
                    "2024-09-16 14:30:15 - ERROR: Connection pool exhausted",
                    "2024-09-16 14:30:22 - CRITICAL: Database unreachable"
                ]
              }'
        ;;
    "payment_failure")
        echo "📨 Sending payment failure error..."
        curl -X POST $BASE_URL/alert \
          -H "Content-Type: application/json" \
          -d '{
                "service": "payment-processor",
                "error_type": "Payment Gateway Error",
                "message": "Stripe API returning 503 Service Unavailable",
                "severity": "CRITICAL",
                "affected_users": 75,
                "stack_trace": "HTTPError: 503 Service Unavailable\n  at StripeClient.charge(stripe_client.py:89)\n  at PaymentService.process_payment(payment.py:156)",
                "environment": "production",
                "recent_logs": [
                    "2024-09-16 14:25:30 - INFO: Processing payment $49.99",
                    "2024-09-16 14:25:32 - ERROR: Stripe API timeout",
                    "2024-09-16 14:25:35 - ERROR: Failed to charge card ending in 4242"
                ]
              }'
        ;;
    "memory_leak")
        echo "📨 Sending memory leak error..."
        curl -X POST $BASE_URL/alert \
          -H "Content-Type: application/json" \
          -d '{
                "service": "recommendation-engine",
                "error_type": "Out of Memory",
                "message": "Pod crashed due to memory limit exceeded (2GB)",
                "severity": "MEDIUM",
                "affected_users": null,
                "stack_trace": "java.lang.OutOfMemoryError: Java heap space\n  at RecommendationCache.loadUserData(RecommendationCache.java:234)\n  at RecommendationService.getRecommendations(RecommendationService.java:67)",
                "environment": "production",
                "recent_logs": [
                    "2024-09-16 14:15:00 - INFO: Cache size: 1.8GB",
                    "2024-09-16 14:20:00 - WARNING: Memory usage at 95%",
                    "2024-09-16 14:22:15 - FATAL: OutOfMemoryError thrown"
                ]
              }'
        ;;
    *)
        echo "❌ Unknown error type: $ERROR_TYPE"
        echo "Available types: auth_timeout, payment_failure, memory_leak"
        exit 1
        ;;
esac

echo ""
echo "✅ Alert sent! Check your Google Chat space for the notification."
echo "💬 You can now test the /ask command with questions about this error."
