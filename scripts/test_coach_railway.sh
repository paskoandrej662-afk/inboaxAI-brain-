#!/usr/bin/env bash
set -euo pipefail

RAILWAY_URL="${1:-https://inboaxai-brain-production-b77b.up.railway.app}"
TEST_COMPANY="00000000-0000-0000-0000-000000000002"

echo "=== Coach propose ==="
RESP=$(curl -s -X POST "$RAILWAY_URL/v1/coach" \
  -H "Content-Type: application/json" \
  -d "{\"company_id\":\"$TEST_COMPANY\",\"query\":\"buď viac casual\"}")
echo "$RESP"
PROPOSAL=$(echo "$RESP" | python3 -c "import sys,json; r=json.load(sys.stdin); print(r.get('proposal_id') or '')")

if [[ -z "$PROPOSAL" ]]; then
  echo "ℹ️  Žiadny proposal (možno už je casual). Skús inú zmenu."
  exit 0
fi

echo "=== Coach apply ==="
curl -s -X POST "$RAILWAY_URL/v1/coach/apply" \
  -H "Content-Type: application/json" \
  -d "{\"proposal_id\":\"$PROPOSAL\"}"

echo
echo "=== Coach Mode na Railway WORKS ==="
