#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > responsive.css <<'EOF'
.container { padding: 8px; }

@media (min-width: 768px) {
  .container { padding: 16px; }
}

@media (min-width: 1024px) {
  .container { padding: 32px; }
}
EOF
