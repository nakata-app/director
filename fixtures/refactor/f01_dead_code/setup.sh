#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > legacy_clean.py <<'EOF'
def used():
    return 42

def main():
    print(used())

if __name__ == "__main__":
    main()
EOF
