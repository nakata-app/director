#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > submit_button.html <<'EOF'
<button type="submit" aria-label="Form gönder" role="button" tabindex="0">Submit</button>
EOF
