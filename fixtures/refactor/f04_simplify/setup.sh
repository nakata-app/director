#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > calc_simple.py <<'EOF'
def is_even(n):
    return n % 2 == 0
EOF
