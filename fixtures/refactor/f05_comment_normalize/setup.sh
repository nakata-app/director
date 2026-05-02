#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > h_clean.py <<'EOF'
# TODO fix
# FIXME later
def f():
    return 1
EOF
