#!/bin/bash
# M2 dry-run scaffolding: pre-seed expected artifacts so the runner's
# disk-truth assertions exercise the evaluator. M3 wires real Director output.
set -e
cd "$(dirname "$0")"
cat > finding.md <<'EOF'
# SQL Injection — /user/<user_id>

CRITICAL: api.py'da `user_id` string concatenation ile SQL'e enjekte ediliyor.

PoC:
GET /user/1%20OR%201%3D1%20--%20-

Fix:
cur.execute("SELECT * FROM users WHERE id=?", (user_id,))
EOF
