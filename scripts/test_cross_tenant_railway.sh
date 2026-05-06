#!/usr/bin/env bash
set -euo pipefail

RAILWAY_URL="${1:-https://inboaxai-brain-production-b77b.up.railway.app}"
COMPANY_A="00000000-0000-0000-0000-000000000002"
COMPANY_B="11111111-1111-1111-1111-111111111111"

echo "=== CROSS-TENANT TEST against $RAILWAY_URL ==="

RESP_A=$(curl -s -X POST "$RAILWAY_URL/v1/respond" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_A\",\"channel\":\"messenger\",\"customer_id\":\"x\",\"message\":\"Aké projekty?\",\"history\":[]}")

echo "Company A response:"
echo "$RESP_A" | python3 -c "import sys,json; r=json.load(sys.stdin); print(f'  cited: {r[\"cited_sources\"]}'); assert any('siea.sk' in s for s in r['cited_sources']), 'FAIL: A neobsahuje siea.sk'; print('  ✅ PASS')"

RESP_B=$(curl -s -X POST "$RAILWAY_URL/v1/respond" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$COMPANY_B\",\"channel\":\"messenger\",\"customer_id\":\"y\",\"message\":\"Aké projekty?\",\"history\":[]}")

echo "Company B response:"
echo "$RESP_B" | python3 -c "import sys,json; r=json.load(sys.stdin); leaked = [s for s in r['cited_sources'] if 'siea.sk' in s]; assert not leaked, f'FAIL: leak {leaked}'; print(f'  cited: {r[\"cited_sources\"]}'); print('  ✅ PASS - žiadny leak')"

echo "=== Cross-tenant izolácia OVERENÁ ==="
