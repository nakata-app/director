#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > finding.md <<'EOF'
# CSRF — POST /transfer

CRITICAL: api.py'da state-changing POST endpoint'inde CSRF token yok, SameSite cookie yok, Origin/Referer kontrolü yok.

PoC: malicious HTML otomatik form submit, kurban tarayıcısı session cookie ekler.

Fix:
- pip install flask-wtf
- app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
- CSRF token doğrulaması ekle
EOF
