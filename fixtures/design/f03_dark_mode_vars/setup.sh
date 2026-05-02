#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > theme.css <<'EOF'
:root {
  --bg: #ffffff;
  --fg: #111111;
}

[data-theme="dark"] {
  --bg: #111111;
  --fg: #f5f5f5;
}
EOF
