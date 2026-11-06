#!/usr/bin/env bash
# Old name of install.sh, kept so the one-line command from earlier versions still works.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/install.sh" ]; then
    exec bash "$here/install.sh" "$@"
fi
curl -fsSL https://raw.githubusercontent.com/aritabryan/copyover/main/install.sh | bash
