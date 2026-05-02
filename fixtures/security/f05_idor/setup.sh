#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > finding.md <<'EOF'
# IDOR — /profile/<user_id>

HIGH: auth decorator var ama owner check yok. Authenticated user başka herhangi bir user_id ile sorgulama yapabilir.

PoC: login + GET /profile/999 → başkasının profili döner.

Fix:
if session["user_id"] != int(user_id) and not is_admin(session["user_id"]):
    return "forbidden", 403
EOF
