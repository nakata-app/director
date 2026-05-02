#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > finding.md <<'EOF'
# Reflected XSS — POST /login

CRITICAL: api.py:7 f-string user girdisini response'a sıfır escaping ile yansıtıyor.

PoC:
POST /login user=<script>alert(1)</script>&password=x

Fix:
from markupsafe import escape
return f"Welcome {escape(user)}!"
EOF
