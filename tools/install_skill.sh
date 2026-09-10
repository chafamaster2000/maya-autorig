#!/usr/bin/env bash
# Install (or refresh) the maya-autorig skill into the dcc-mcp user skills dir
# as a real directory copy. A symlink is NOT enough: the skill scanner does not
# follow symlinked directories, so a linked skill is silently invisible.
# Re-run after editing the skill, then rescan (tools/mcp_rescan.py) or restart Maya.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../skill/maya-autorig" && pwd)"
DST="${HOME}/.dcc-mcp/maya/skills/maya-autorig"
if [ -L "$DST" ]; then rm "$DST"; fi
mkdir -p "$DST"
rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$SRC/" "$DST/"
echo "installed: $SRC -> $DST ($(find "$DST" -type f | wc -l | tr -d ' ') files)"
