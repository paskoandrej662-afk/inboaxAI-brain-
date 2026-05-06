#!/usr/bin/env bash
set -euo pipefail

TEST_COMPANY="00000000-0000-0000-0000-000000000002"
CUSTOMER="hist_test_user_$(date +%s)"

echo "=== T1: prvá správa (no history) ==="
curl -s -X POST http://localhost:8000/v1/respond \
  -H "Content-Type: application/json" \
  -d "{
    \"company_id\":\"$TEST_COMPANY\",
    \"channel\":\"messenger\",
    \"customer_id\":\"$CUSTOMER\",
    \"message\":\"Volám sa Andrej, mám 30 rokov.\",
    \"history\":[]
  }" | python3 -c "import sys,json; r=json.load(sys.stdin); print(f'response: {r[\"response\"][:120]}')"

sleep 2

echo "=== T2: druhá správa - mali by ste pamätať Andreja ==="
curl -s -X POST http://localhost:8000/v1/respond \
  -H "Content-Type: application/json" \
  -d "{
    \"company_id\":\"$TEST_COMPANY\",
    \"channel\":\"messenger\",
    \"customer_id\":\"$CUSTOMER\",
    \"message\":\"Pamätáš si moje meno?\",
    \"history\":[
      {\"role\":\"user\",\"content\":\"Volám sa Andrej, mám 30 rokov.\"},
      {\"role\":\"assistant\",\"content\":\"Ahoj Andrej!\"}
    ]
  }" | python3 -c "import sys,json; r=json.load(sys.stdin); resp=r['response']; print(f'response: {resp[:200]}'); assert 'andrej' in resp.lower(), 'FAIL: nepamatá meno'; print('✅ PASS - pamätá meno')"

echo "=== Conversation history WORKS ==="
