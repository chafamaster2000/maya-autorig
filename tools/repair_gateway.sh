#!/usr/bin/env bash
# Recover the DCC-MCP gateway when it stops answering with Maya open.
# The shell twin of tools/repair_gateway.ps1 (macOS / Linux).
#
# Symptom: Maya is running, the plugin is loaded, every MCP call fails with a
# transport error, nothing listens on port 9765.
#
# Cause (seen for real, twice): a Maya crash or kill, or two Mayas started at
# once, leaves rows for dcc_type "__gateway__" in the file registry claiming
# a port nobody listens on. Every gateway that starts afterwards probes the
# "resident gateway", gets no answer, and instead of taking the port over
# logs "found an existing owner; exiting". A gateway row carries no pid, so
# the reaper has nothing to prove it dead with -- which is why this script
# judges a gateway row by its PORT (does anything answer there?) and every
# other row by its pid. services.json is backed up before anything is
# rewritten.
#
#   tools/repair_gateway.sh --dry-run     report what would be pruned
#   tools/repair_gateway.sh               prune
#   tools/repair_gateway.sh --start       prune, then start a gateway here if
#                                         nothing answers on the port
set -uo pipefail
DRY=0; START=0; PORT=9765; REG=""
while [ $# -gt 0 ]; do case "$1" in
  --dry-run) DRY=1;; --start) START=1;; --port) PORT="$2"; shift;; --registry-dir) REG="$2"; shift;;
  -h|--help) sed -n '2,22p' "$0"; exit 0;; *) echo "unknown flag $1" >&2; exit 2;; esac; shift; done
if [ -z "$REG" ]; then tmp="${TMPDIR:-/tmp}"; REG="${tmp%/}/dcc-mcp-registry"; fi
[ -d "$REG" ] || REG="$(python3 -c 'import tempfile;print(tempfile.gettempdir())')/dcc-mcp-registry"

echo; echo "DCC-MCP gateway repair"; echo "registry : $REG"
code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" 2>/dev/null || true)"; code="${code:-000}"
echo "health   : $code on port $PORT $([ "$code" = 000 ] && echo '(nobody listening)')"
if [ "$code" = 200 ]; then echo "Gateway is healthy; nothing to repair."; exit 0; fi
if [ ! -d "$REG" ]; then echo "No registry directory: nothing to prune. Open Maya and the sidecar will create one."; exit 0; fi

DRY="$DRY" REG="$REG" PORT="$PORT" python3 - <<'PY'
import json, os, shutil, subprocess, sys, time
reg, dry, port = os.environ["REG"], os.environ["DRY"] == "1", int(os.environ["PORT"])
def port_answers(p):
    try:
        import urllib.request
        return urllib.request.urlopen("http://127.0.0.1:%d/health" % p, timeout=3).status < 500
    except Exception:
        return False
def pid_alive(pid):
    try: os.kill(int(pid), 0); return True
    except Exception: return False
path = os.path.join(reg, "services.json")
kept, pruned = [], []
if os.path.exists(path):
    rows = json.load(open(path))
    for r in rows:
        if r.get("dcc_type") == "__gateway__":
            ghost = not port_answers(int(r.get("port") or 0))          # judged by its port
        else:
            pid = r.get("host_pid") or r.get("sidecar_pid")
            ghost = not (pid and pid_alive(pid))                      # judged by its pid
        (pruned if ghost else kept).append(r)
    print("rows: %d total, %d ghost, %d live" % (len(rows), len(pruned), len(kept)))
    for r in pruned: print("  prune %-12s %s" % (r.get("dcc_type"), str(r.get("instance_id", "?"))[:8]))
    for r in kept:   print("  keep  %-12s %s" % (r.get("dcc_type"), str(r.get("instance_id", "?"))[:8]))
    if pruned and not dry:
        shutil.copy(path, path + ".bak")
        json.dump(kept, open(path, "w"), indent=2)
        print("services.json rewritten (%d rows kept); backup at %s.bak" % (len(kept), path))
else:
    print("no services.json")
live_ids = [str(r.get("instance_id", "")) for r in kept]
locks = os.path.join(reg, "locks")
if os.path.isdir(locks):
    for f in os.listdir(locks):
        if not any(i and i in f for i in live_ids):
            print("  prune lock  " + f); dry or os.remove(os.path.join(locks, f))
sent = os.path.join(reg, "sentinels")
if os.path.isdir(sent):
    dead = [f for f in os.listdir(sent) if f.split("-")[0].isdigit() and not pid_alive(f.split("-")[0])]
    for f in dead: dry or os.remove(os.path.join(sent, f))
    if dead: print("  prune %d dead sentinel(s)" % len(dead))
ll = os.path.join(reg, "gateway-launch.lock")
if os.path.exists(ll): print("  prune gateway-launch.lock"); dry or os.remove(ll)
print("DRY RUN - nothing was changed" if dry else "Registry cleaned.")
PY

if [ "$START" = 1 ] && [ "$DRY" = 0 ]; then
  bin="$(command -v dcc-mcp-server || ls "$HOME"/Library/Python/*/bin/dcc-mcp-server 2>/dev/null | tail -1)"
  if [ -z "$bin" ]; then echo "dcc-mcp-server not found; cannot start a gateway"; exit 1; fi
  echo "starting a gateway: $bin"
  nohup "$bin" gateway --host 127.0.0.1 --port "$PORT" --remote-host 0.0.0.0 --remote-port 59765 \
    --gateway-idle-timeout-secs 300 --name "dcc-mcp-gateway@$(hostname -s)" >"$REG/gateway-manual-$PORT.log" 2>&1 &
  for i in 1 2 3 4 5 6 7 8 9 10; do sleep 1; c="$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health")"; [ "$c" = 200 ] && { echo "gateway up (health 200)"; break; }; done
fi
echo
echo "Now reload the plugin so its sidecar registers with the gateway:"
echo "  Maya > Windows > Settings/Preferences > Plug-in Manager > untick and re-tick dcc_mcp_maya_plugin"
echo "  (or restart Maya - one instance at a time, two racing is what creates the ghosts)"
echo "Then reconnect: in Claude Code type /mcp; in Codex start a new session."
