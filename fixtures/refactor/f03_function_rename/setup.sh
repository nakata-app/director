#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > svc_renamed.py <<'EOF'
def do_stuff(x):
    return x + 1

def main():
    print(do_stuff(5))
EOF
