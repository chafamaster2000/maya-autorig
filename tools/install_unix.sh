#!/usr/bin/env bash
# maya-autorig - macOS / Linux installer. The shell twin of tools/install_windows.ps1.
# Works with Codex AND Claude Code: both get the `maya` MCP server and the skill.
#
# The usual way to run this is not by hand: paste the repository link to Codex
# or Claude Code ("install https://github.com/chafamaster2000/maya-autorig") and
# the agent runs install.sh piped from curl, which clones (or updates) and calls
# this script. Takes about 5-10 minutes the first time (Python, Node and the
# agent CLIs download), about a minute afterwards, a few seconds when nothing
# changed.
#
#   Codex / Claude Code --HTTP--> dcc-mcp gateway (127.0.0.1:9765) --> Maya adapter
#
# Installs, in order, each step reporting OK / SKIP / WARN / FAIL:
#   1. preflight: python3, Maya, AdvancedSkeleton (the last two are checked, never installed)
#   2. pip install --user dcc-mcp-maya (pulls dcc-mcp-core + dcc-mcp-server)
#   3. the per-user Python bin dir on PATH (this session and the shell profile)
#   4. dcc-mcp-maya install --yes  (Maya module + userSetup.py)
#   5. this skill: the gateway copy and the agent-side SKILL.md (tools/install_skill.sh)
#   6. agents: Claude Code and Codex, installed when missing, and the `maya`
#      MCP server registered in both
#   7. verify: strict skill scan, dcc-mcp-maya verify, offline tests when tests/ exists
#
# It ends with "What changed -> what to do": every restart or login the person
# now owes, and nothing they do not. Idempotent: a second run changes nothing
# and says so.
#
# Flags: --dry-run --skip-prereqs --skip-packages --skip-adapter --skip-claude
#        --skip-codex --gateway-url URL --python PATH
# Exit code 1 when a required step failed, so it can gate a machine setup.
set -uo pipefail

DRY_RUN=0; SKIP_PREREQS=0; SKIP_PACKAGES=0; SKIP_ADAPTER=0; SKIP_CLAUDE=0; SKIP_CODEX=0
GATEWAY_URL='http://127.0.0.1:9765/mcp'; PYTHON=''
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-prereqs) SKIP_PREREQS=1 ;;
    --skip-packages) SKIP_PACKAGES=1 ;;
    --skip-adapter) SKIP_ADAPTER=1 ;;
    --skip-claude) SKIP_CLAUDE=1 ;;
    --skip-codex) SKIP_CODEX=1 ;;
    --gateway-url) GATEWAY_URL="$2"; shift ;;
    --python) PYTHON="$2"; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(cat "$REPO/VERSION" 2>/dev/null || echo unknown)"
STEPS=()          # "name|status|required|detail"
CHANGES=()        # "what|consequence"  -- only things that actually changed
add_step() {      # name status detail [required]
  STEPS+=("$1|$2|${4:-}|$3")
  local color=''; case "$2" in OK) color='\033[32m';; WARN) color='\033[33m';; FAIL) color='\033[31m';; SKIP) color='\033[90m';; esac
  printf '   %b%-4s\033[0m %s%s\n' "$color" "$2" "$1" "${3:+ - $3}"
}
add_change() { CHANGES+=("$1|$2"); }
run_tool() {      # prints combined output, returns the tool's exit code
  if [ "$DRY_RUN" = 1 ]; then echo "(dry run) $*"; return 0; fi
  "$@" 2>&1
}
have() { command -v "$1" >/dev/null 2>&1; }
maya_running() { pgrep -x Maya >/dev/null 2>&1 || pgrep -f 'maya\.bin' >/dev/null 2>&1; }

echo
echo "maya-autorig installer (macOS / Linux)  version $VERSION  -- works with Codex and Claude Code"
echo "usually run for you by an agent after you paste the repo link; first time ~5-10 min, later runs ~1 min"
echo "repo: $REPO ($(git -C "$REPO" describe --tags --always 2>/dev/null || echo 'not a git clone'))"
[ "$DRY_RUN" = 1 ] && echo "DRY RUN - nothing will be changed"

# --------------------------------------------------------------------------- #
echo; echo "1. Preflight"
# Which Python owns the DCC-MCP packages matters: the adapter's lifecycle CLI
# runs its preflight with mayapy, and a second install under another Python
# leaves two copies of the CLI, one of them broken. Preference, observed on a
# machine that already had a working install:
#   --python  >  the interpreter of an existing dcc-mcp-maya (its shebang)
#             >  mayapy of the Maya found  >  python3
case "$(uname -s)" in
  Darwin) MAYA_GLOB=(/Applications/Autodesk/maya*); AS_ROOTS=("$HOME/Library/Preferences/Autodesk/maya");
          USER_BINS=("$HOME"/Library/Python/*/bin); MAYAPY_REL="Maya.app/Contents/bin/mayapy";
          MAYA_MODULES="$HOME/Library/Preferences/Autodesk/maya/modules";;
  *)      MAYA_GLOB=(/usr/autodesk/maya* /opt/autodesk/maya*); AS_ROOTS=("$HOME/maya");
          USER_BINS=("$HOME/.local/bin"); MAYAPY_REL="bin/mayapy";
          MAYA_MODULES="$HOME/maya/modules";;
esac
MAYAS=(); for m in "${MAYA_GLOB[@]}"; do [ -d "$m" ] && [ -x "$m/$MAYAPY_REL" ] && MAYAS+=("$m"); done
PY=''; PY_WHY=''
if [ -n "$PYTHON" ]; then PY="$PYTHON"; PY_WHY='--python'; fi
if [ -z "$PY" ]; then
  for b in "${USER_BINS[@]}"; do
    if [ -f "$b/dcc-mcp-maya" ]; then
      cand="$(sed -n '1s/^#!//p' "$b/dcc-mcp-maya")"
      if [ -x "$cand" ]; then PY="$cand"; PY_WHY="owns the existing dcc-mcp-maya in $b"; break; fi
    fi
  done
fi
if [ -z "$PY" ] && [ "${#MAYAS[@]}" -gt 0 ]; then PY="${MAYAS[${#MAYAS[@]}-1]}/$MAYAPY_REL"; PY_WHY="mayapy of ${MAYAS[${#MAYAS[@]}-1]}"; fi
if [ -z "$PY" ] && have python3; then PY=python3; PY_WHY='python3 on PATH'; fi
if [ -z "$PY" ] && [ "$SKIP_PREREQS" = 0 ] && [ "$DRY_RUN" = 0 ] && have brew; then
  echo "   Python missing: brew install python"
  run_tool brew install python >/dev/null && PY=python3 && PY_WHY='installed by brew'
fi
if [ -n "$PY" ] && "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
  add_step 'Python' OK "$("$PY" --version 2>&1) - $PY ($PY_WHY)" required
else
  add_step 'Python' FAIL 'no python3 >= 3.9 (macOS: brew install python; Linux: your package manager)' required
  PY=''
fi

if [ "${#MAYAS[@]}" -gt 0 ]; then add_step 'Maya' OK "${MAYAS[*]}"
else add_step 'Maya' WARN 'no Maya found; the skill installs anyway, but nothing can run'; fi

AS=''
if [ -n "${ADVANCEDSKELETON_DIR:-}" ] && [ -f "$ADVANCEDSKELETON_DIR/AdvancedSkeleton.mel" ]; then AS="$ADVANCEDSKELETON_DIR"; fi
if [ -z "$AS" ]; then
  for root in "${AS_ROOTS[@]}"; do
    [ -d "$root" ] || continue
    AS="$(find "$root" -maxdepth 5 -name 'AdvancedSkeleton.mel' 2>/dev/null | head -1)"
    [ -n "$AS" ] && { AS="$(dirname "$AS")"; break; }
  done
fi
if [ -n "$AS" ]; then add_step 'AdvancedSkeleton' OK "$AS"
else add_step 'AdvancedSkeleton' WARN 'not found. Licensed content from Animation Studios: get it at animationstudios.com.au and install it into Maya, or set ADVANCEDSKELETON_DIR'; fi

# --------------------------------------------------------------------------- #
echo; echo "2. DCC-MCP packages"
pkg_version() { [ -n "$PY" ] && "$PY" -m pip show dcc-mcp-maya 2>/dev/null | awk '/^Version:/{print $2}'; }
ADAPTER_BEFORE="$(pkg_version)"
if [ "$SKIP_PACKAGES" = 1 ]; then add_step 'pip install dcc-mcp-maya' SKIP '--skip-packages'
elif [ -z "$PY" ]; then add_step 'pip install dcc-mcp-maya' FAIL 'no Python' required
else
  out="$(run_tool "$PY" -m pip install --user --upgrade dcc-mcp-maya)"; rc=$?
  if [ $rc -eq 0 ]; then
    ADAPTER_AFTER="$(pkg_version)"
    if [ "$DRY_RUN" = 0 ] && [ "$ADAPTER_BEFORE" != "$ADAPTER_AFTER" ]; then
      add_step 'pip install dcc-mcp-maya' OK "${ADAPTER_BEFORE:-none} -> $ADAPTER_AFTER" required
      add_change "dcc-mcp-maya ${ADAPTER_BEFORE:-none} -> $ADAPTER_AFTER" maya
    else
      add_step 'pip install dcc-mcp-maya' OK "already ${ADAPTER_AFTER:-installed}" required
    fi
  else add_step 'pip install dcc-mcp-maya' FAIL "$(echo "$out" | tail -3 | tr '\n' ' ')" required; fi
fi

# --------------------------------------------------------------------------- #
echo; echo "3. PATH"
if [ -n "$PY" ]; then
  USER_BIN="$("$PY" -c 'import site; print(site.USER_BASE)')/bin"
  case ":$PATH:" in
    *":$USER_BIN:"*) add_step 'user bin on PATH' OK "$USER_BIN" ;;
    *)
      export PATH="$USER_BIN:$PATH"
      # Persist it for the next shell. zsh on macOS, bash elsewhere; a marker
      # comment keeps the line findable and stops it from being added twice.
      case "$(basename "${SHELL:-bash}")" in zsh) RC="$HOME/.zprofile";; *) RC="$HOME/.bashrc";; esac
      if [ "$DRY_RUN" = 0 ] && ! grep -qs 'maya-autorig' "$RC" 2>/dev/null; then
        printf '\n# maya-autorig: per-user Python console scripts (dcc-mcp-maya, dcc-mcp-server)\nexport PATH="%s:$PATH"\n' "$USER_BIN" >> "$RC"
        add_change "PATH line added to $RC" shell
      fi
      add_step 'user bin on PATH' OK "added $USER_BIN (this session and $RC)" ;;
  esac
fi

# --------------------------------------------------------------------------- #
echo; echo "4. Maya adapter"
# The CLI's plain output is "install: failed" and nothing else; --json carries
# the reason. And the CLI's preflight can fail on a machine where the module
# is already in place and working (seen: mayapy 3.13 + Maya 2027, `maya.cmds`
# has no `about` before maya.standalone is initialised) -- that is a warning
# with the reason, not a failed install.
adapter_reason() { echo "$1" | "$PY" -c 'import json,sys
try: d=json.load(sys.stdin); print((d.get("failure_message") or d.get("status") or "").splitlines()[-1][:160])
except Exception: print("")' 2>/dev/null; }
module_in_place() { ls "$MAYA_MODULES"/dcc_mcp_maya*.mod >/dev/null 2>&1; }
if [ "$SKIP_ADAPTER" = 1 ]; then add_step 'dcc-mcp-maya install' SKIP '--skip-adapter'
elif ! have dcc-mcp-maya && [ "$DRY_RUN" = 0 ]; then add_step 'dcc-mcp-maya install' FAIL 'dcc-mcp-maya CLI not on PATH after install; open a new shell and re-run' required
else
  out="$(run_tool dcc-mcp-maya install --yes --json)"; rc=$?
  if [ $rc -eq 0 ]; then
    add_step 'dcc-mcp-maya install' OK 'Maya module + userSetup.py' required
  elif module_in_place; then
    add_step 'dcc-mcp-maya install' WARN "module already in place at $MAYA_MODULES; the CLI's own preflight failed: $(adapter_reason "$out")"
  else
    add_step 'dcc-mcp-maya install' FAIL "$(adapter_reason "$out")" required
  fi
fi

# --------------------------------------------------------------------------- #
echo; echo "5. The skill"
install_skill() {   # runs the copier; records a change when files moved
  out="$("$REPO/tools/install_skill.sh" 2>&1)"; rc=$?
  changed="$(echo "$out" | sed -n 's/.*(\([0-9]*\) files, \([0-9]*\) changed).*/\2/p' | head -1)"
  echo "$out" | grep -q 'agent skill (codex)' && AGENT_SKILL_CODEX=1
  echo "$out" | grep -q 'agent skill (claude)' && AGENT_SKILL_CLAUDE=1
  [ "${changed:-0}" -gt 0 ] 2>/dev/null && add_change "skill copy for the gateway updated ($changed files)" maya
  return $rc
}
AGENT_SKILL_CODEX=0; AGENT_SKILL_CLAUDE=0
if [ "$DRY_RUN" = 1 ]; then add_step 'install maya-autorig' OK '(dry run)' required
elif install_skill; then add_step 'install maya-autorig' OK "$(echo "$out" | head -1)" required
else add_step 'install maya-autorig' FAIL "$out" required; fi

# --------------------------------------------------------------------------- #
echo; echo "6. Agents: Claude Code and Codex"
# Both are npm packages; Node comes first, from Homebrew when there is one.
ensure_npm() {
  have npm && return 0
  { [ "$SKIP_PREREQS" = 1 ] || [ "$DRY_RUN" = 1 ]; } && return 1
  if have brew; then echo "   Node missing: brew install node"; run_tool brew install node >/dev/null; fi
  have npm
}
register_agent() {   # name npm-package register-command...
  local name="$1" pkg="$2"; shift 2
  local installed_now=0
  if ! have "$name" && [ "$SKIP_PREREQS" = 0 ]; then
    if ensure_npm; then
      out="$(run_tool npm install -g "$pkg")"; rc=$?
      if [ $rc -eq 0 ]; then add_step "install $name" OK "$pkg"; installed_now=1; add_change "$name CLI installed" "login-$name"
      else add_step "install $name" WARN "$(echo "$out" | tail -2 | tr '\n' ' ')"; fi
    else
      add_step "install $name" WARN 'npm not available (install Node.js, then re-run)'
    fi
  fi
  if ! have "$name" && [ "$DRY_RUN" = 0 ]; then
    add_step "register MCP server ($name)" WARN "$name CLI not found; by hand: $*"
    return
  fi
  # Was it there before? That decides whether the agent needs a restart.
  local had=0; "$name" mcp get maya >/dev/null 2>&1 && had=1
  out="$(run_tool "$@")"; rc=$?
  if [ $rc -eq 0 ] || echo "$out" | grep -qi 'already'; then
    if [ "$had" = 1 ]; then add_step "register MCP server ($name)" OK "already registered"
    else add_step "register MCP server ($name)" OK "$GATEWAY_URL (new)"; add_change "maya MCP server registered in $name" "restart-$name"; fi
  else add_step "register MCP server ($name)" WARN "$(echo "$out" | tail -2 | tr '\n' ' ')"; fi
}
if [ "$SKIP_CLAUDE" = 1 ]; then add_step 'register MCP server (claude)' SKIP '--skip-claude'
else register_agent claude @anthropic-ai/claude-code claude mcp add --transport http maya "$GATEWAY_URL"; fi
if [ "$SKIP_CODEX" = 1 ]; then add_step 'register MCP server (codex)' SKIP '--skip-codex'
else register_agent codex @openai/codex codex mcp add maya --url "$GATEWAY_URL"; fi
# The agent-side SKILL.md goes wherever an agent now exists: install_skill.sh
# ran in step 5, possibly before a CLI above was installed.
if [ "$DRY_RUN" = 0 ] && { { have codex && [ "$AGENT_SKILL_CODEX" = 0 ]; } || { have claude && [ "$AGENT_SKILL_CLAUDE" = 0 ]; }; }; then
  "$REPO/tools/install_skill.sh" >/dev/null 2>&1 || true
fi

# --------------------------------------------------------------------------- #
echo; echo "7. Verify"
if [ -z "$PY" ]; then add_step 'verify' SKIP 'no Python'
elif [ "$DRY_RUN" = 1 ]; then add_step 'verify' SKIP 'dry run'
else
  verdict="$("$PY" - "$HOME/.dcc-mcp/maya/skills" <<'PY'
import json, sys
try:
    from dcc_mcp_core._core import scan_and_load_strict as scan
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"ok": None, "why": "dcc_mcp_core not importable from this Python: %s" % exc})); sys.exit(0)
try:
    found = scan(extra_paths=[sys.argv[1]], dcc_name="maya")
    names = [getattr(s, "name", str(s)) for s in (found if isinstance(found, (list, tuple)) else [])]
    print(json.dumps({"ok": "maya-autorig" in " ".join(names), "found": names}))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"ok": False, "why": str(exc)}))
PY
)"
  ok="$(echo "$verdict" | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("ok"))' 2>/dev/null || echo err)"
  case "$ok" in
    True) add_step 'skill scans clean' OK "$(echo "$verdict" | cut -c1-120)" required ;;
    None) add_step 'skill scans clean' SKIP "$(echo "$verdict" | cut -c1-160)" ;;
    *)    add_step 'skill scans clean' FAIL "$(echo "$verdict" | cut -c1-200)" required ;;
  esac
  if have dcc-mcp-maya; then
    out="$(run_tool dcc-mcp-maya verify | tail -1)"; rc=$?
    add_step 'dcc-mcp-maya verify' "$([ $rc -eq 0 ] && echo OK || echo WARN)" "$out"
  fi
  if [ -d "$REPO/tests" ]; then
    out="$(cd "$REPO" && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 "$PY" -m pytest tests/ -q 2>&1 | tail -1)"; rc=$?
    add_step 'offline tests' "$([ $rc -eq 0 ] && echo OK || echo WARN)" "$out"
  fi
fi

# --------------------------------------------------------------------------- #
echo; echo "Summary"
printf '   %-32s %-5s %s\n' NAME STATUS REQUIRED
failed=0; required_failed=0
for s in "${STEPS[@]}"; do
  IFS='|' read -r name status required detail <<<"$s"
  printf '   %-32s %-5s %s\n' "$name" "$status" "${required:+yes}"
  [ "$status" = FAIL ] && { failed=$((failed+1)); [ -n "$required" ] && required_failed=$((required_failed+1)); }
done
if [ "$required_failed" -gt 0 ]; then echo; echo "$required_failed required step(s) failed."; exit 1; fi
[ "$failed" -gt 0 ] && echo "$failed optional step(s) failed."

# --------------------------------------------------------------------------- #
# The part the person actually needs: what changed, and what each change
# costs them. Nothing changed -> say so, and that nothing needs a restart.
echo; echo "What changed -> what to do"
if [ "$DRY_RUN" = 1 ]; then echo "   (dry run: nothing was changed)"
elif [ "${#CHANGES[@]}" -eq 0 ]; then
  echo "   nothing changed: everything was already installed at version $VERSION."
  echo "   -> nothing to restart. If Maya is closed, open it; then ask the agent to rig."
else
  need_maya=0; need_codex=0; need_claude=0; login_codex=0; login_claude=0
  for c in "${CHANGES[@]}"; do
    IFS='|' read -r what why <<<"$c"
    printf '   - %s\n' "$what"
    case "$why" in maya) need_maya=1;; restart-codex) need_codex=1;; restart-claude) need_claude=1;;
                   login-codex) login_codex=1; need_codex=1;; login-claude) login_claude=1; need_claude=1;; esac
  done
  echo; echo "   To do, in this order:"
  n=0
  if [ "$login_codex" = 1 ]; then n=$((n+1)); echo "   $n. Codex was just installed: run 'codex login' once (it opens the browser)."; fi
  if [ "$login_claude" = 1 ]; then n=$((n+1)); echo "   $n. Claude Code was just installed: run 'claude' once and log in."; fi
  if [ "$need_maya" = 1 ]; then
    n=$((n+1))
    if maya_running; then echo "   $n. Maya is open and is running the old copy: close it and open it again (one window)."
    else echo "   $n. Open Maya (one window). It reads the new copy at start-up."; fi
  fi
  if [ "$need_codex" = 1 ] && [ "$login_codex" = 0 ]; then n=$((n+1)); echo "   $n. Codex: start a new session (its MCP servers and skills are read at start-up). A session that is already open will not see 'maya'."; fi
  if [ "$need_claude" = 1 ] && [ "$login_claude" = 0 ]; then n=$((n+1)); echo "   $n. Claude Code: type /mcp in the open session, or start a new one."; fi
  if [ "$need_maya" = 0 ]; then n=$((n+1)); echo "   $n. Maya: nothing to do (leave it open if it is open)."; fi
fi
echo
echo "Then, in Codex or Claude Code:  load_skill(skill_name=\"maya-autorig\")"
echo "and rig a character:            gauntlet_run(source=\"/path/to/character.fbx\", pose=\"A\")"
echo "If the MCP goes quiet with Maya open:  curl http://127.0.0.1:9765/health  (000 = nobody listening)"
exit 0
