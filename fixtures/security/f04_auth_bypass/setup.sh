#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > finding.md <<'EOF'
# Auth bypass — /admin/users

CRITICAL: api.py'da /admin/users endpoint'inde hiçbir auth decorator yok. Token, session, role check sıfır.

PoC: curl http://target/admin/users → tüm kullanıcı verisi döner.

Fix:
@app.route("/admin/users")
@token_required
@admin_required
def admin_users():
    ...
EOF
