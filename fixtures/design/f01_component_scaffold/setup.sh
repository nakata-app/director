#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > card.html <<'EOF'
<div class="card">
  <header class="card-header">Title</header>
  <div class="card-body">Content</div>
</div>
EOF
