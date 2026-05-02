#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > m_clean.py <<'EOF'
import json

def parse(s):
    return json.loads(s)
EOF
