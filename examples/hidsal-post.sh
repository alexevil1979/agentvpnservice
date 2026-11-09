#!/usr/bin/env bash
# Example: hidsal → agentvpnservice incident intake
set -euo pipefail
BASE_URL="${AGENT_BASE_URL:-https://agentvpnservice.1tlt.ru}"
TOKEN="${INCIDENT_WEBHOOK_TOKEN:?set INCIDENT_WEBHOOK_TOKEN}"

curl -sS -X POST "$BASE_URL/api/v1/incidents" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"server_id\": \"${1:-vpn-example}\",
    \"check\": \"${2:-unknown_check}\",
    \"summary\": \"${3:-test from hidsal example}\",
    \"evidence\": \"${4:-}\",
    \"observed_at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
  }"
echo
