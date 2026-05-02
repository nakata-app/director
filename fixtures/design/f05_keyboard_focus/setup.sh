#!/bin/bash
set -e
cd "$(dirname "$0")"
cat > form_focus.html <<'EOF'
<form>
  <input name="name" tabindex="1" />
  <input name="email" type="email" tabindex="2" />
  <button type="submit" tabindex="3">Submit</button>
</form>
EOF
