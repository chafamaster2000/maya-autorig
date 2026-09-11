#!/usr/bin/env bash
# Install (or refresh) the maya-autorig skill where each consumer reads it.
#
# 1. The dcc-mcp user skills dir, as a real directory copy. A symlink is NOT
#    enough: the skill scanner does not follow symlinked directories, so a
#    linked skill is silently invisible.
# 2. The agent-side copy of SKILL.md, into ~/.codex/skills and ~/.claude/skills.
#    The gateway's load_skill registers the tools but never returns the
#    instructions, so the workflow text has to reach the agent as a skill of
#    its own. Same file, one home per agent that is installed.
#
# Re-run after editing the skill, then rescan (tools/mcp_rescan.py) or restart Maya.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../skill/maya-autorig" && pwd)"
DST="${HOME}/.dcc-mcp/maya/skills/maya-autorig"
if [ -L "$DST" ]; then rm "$DST"; fi
mkdir -p "$DST"
# -i itemises what moved; a non-empty list means the running Maya is now
# behind and needs a rescan (tools/mcp_rescan.py) or a restart.
# The version travels with every copy, so an update can tell a stale copy
# from a current one without opening the repo.
# Only when it differs: a fresh copy every run would bump the mtime, rsync
# would count it as changed, and every run would say "restart Maya".
if ! cmp -s "$SRC/../../VERSION" "$SRC/VERSION" 2>/dev/null; then cp "$SRC/../../VERSION" "$SRC/VERSION" 2>/dev/null || true; fi
changed="$(rsync -ai --delete --exclude '__pycache__' --exclude '*.pyc' "$SRC/" "$DST/" | grep -c '^[><]f' || true)"
echo "installed: $SRC -> $DST ($(find "$DST" -type f | wc -l | tr -d ' ') files, $changed changed)"
if [ "${changed:-0}" -gt 0 ]; then echo "skill changed: restart Maya, or run tools/mcp_rescan.py, so the gateway picks it up"; fi

# An agent counts as present when its CLI is on PATH or its home dir exists
# (a CLI installed a minute ago has no home dir until its first run).
for agent in codex claude; do
  home="${HOME}/.${agent}"
  if command -v "$agent" >/dev/null 2>&1 || [ -d "$home" ]; then
    mkdir -p "$home/skills/maya-autorig"
    cp "$SRC/SKILL.md" "$home/skills/maya-autorig/SKILL.md"
    cmp -s "$SRC/VERSION" "$home/skills/maya-autorig/VERSION" 2>/dev/null || cp "$SRC/VERSION" "$home/skills/maya-autorig/VERSION" 2>/dev/null || true
    echo "agent skill ($agent): $home/skills/maya-autorig/SKILL.md"
  fi
done
