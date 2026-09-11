#!/usr/bin/env bash
# maya-autorig - macOS / Linux installer. The twin of install.bat.
#
#   ./install.sh                 from a clone: install everything installable
#   ./install.sh --dry-run       show what it would do, change nothing
#
# It also works without a clone, piped from the web:
#
#   curl -fsSL https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/install.sh | bash
#
# which clones the repository into ~/maya-autorig (MAYA_AUTORIG_DIR to change
# it) and runs the same installer from there. Flags pass through either way.
set -euo pipefail
REPO_URL='https://github.com/chafamaster2000/maya-autorig'

here="$(dirname "${BASH_SOURCE[0]:-}")"
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$here/tools/install_unix.sh" ]; then
  exec bash "$here/tools/install_unix.sh" "$@"
fi

# Piped from curl: no file on disk, so get one.
dest="${MAYA_AUTORIG_DIR:-$HOME/maya-autorig}"
if ! command -v git >/dev/null 2>&1; then
  echo "git is required to fetch the repository. macOS: xcode-select --install; Linux: your package manager." >&2
  exit 1
fi
# Pasting the link a second time must update, not reinstall blindly. The
# comparison is explicit: the VERSION file in the clone against the VERSION
# file on GitHub. Same version and everything in place -> nothing to do, and
# it says so (nothing to restart). --reinstall forces the installer anyway.
reinstall=0; pass=()
for a in "$@"; do case "$a" in --reinstall) reinstall=1;; *) pass+=("$a");; esac; done
remote_version="$(curl -fsSL "https://raw.githubusercontent.com/chafamaster2000/maya-autorig/main/VERSION" 2>/dev/null | tr -d '[:space:]')"
if [ -d "$dest/.git" ]; then
  local_version="$({ cat "$dest/VERSION" 2>/dev/null || echo unknown; } | tr -d '[:space:]')"
  echo "installed: $local_version   available: ${remote_version:-unknown (offline?)}"
  if [ -n "$remote_version" ] && [ "$local_version" = "$remote_version" ] && [ "$reinstall" = 0 ]; then
    # Same version: only re-run the installer if something is missing.
    healthy=1
    [ "$(cat "$HOME/.dcc-mcp/maya/skills/maya-autorig/VERSION" 2>/dev/null | tr -d '[:space:]')" = "$local_version" ] || healthy=0
    command -v codex  >/dev/null 2>&1 && ! codex  mcp get maya >/dev/null 2>&1 && healthy=0
    command -v claude >/dev/null 2>&1 && ! claude mcp get maya >/dev/null 2>&1 && healthy=0
    if [ "$healthy" = 1 ]; then
      echo "already up to date: version $local_version is installed and registered in every agent found."
      echo "-> nothing to do, nothing to restart. (Force a reinstall with: ... | bash -s -- --reinstall)"
      exit 0
    fi
    echo "version $local_version is current but something is missing; running the installer."
  else
    before="$(git -C "$dest" rev-parse --short HEAD)"
    git -C "$dest" fetch --quiet origin && git -C "$dest" pull --ff-only --quiet
    after="$(git -C "$dest" rev-parse --short HEAD)"
    new_version="$({ cat "$dest/VERSION" 2>/dev/null || echo unknown; } | tr -d '[:space:]')"
    if [ "$before" = "$after" ]; then echo "clone already at $after ($new_version)"; else echo "updated: $local_version ($before) -> $new_version ($after)"; fi
  fi
else
  echo "cloning $REPO_URL into $dest"; git clone --depth 1 "$REPO_URL" "$dest"
  echo "version: $({ cat "$dest/VERSION" 2>/dev/null || echo unknown; } | tr -d '[:space:]')"
fi
exec bash "$dest/tools/install_unix.sh" "${pass[@]}"
