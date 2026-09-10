"""Make the embedded Maya MCP server (re)discover the user skills directory
without restarting anything.

The server scans its skill paths at start; a skill dropped into
~/.dcc-mcp/maya/skills afterwards is invisible until `reload_skill_paths()`.
This lists what the server knows, reloads the paths if the wanted skill is
missing, loads it, and lists again.

    run_script tools/mcp_rescan.py  argv=["maya-autorig"]           # new skill
    run_script tools/mcp_rescan.py  argv=["maya-autorig", "--force"]  # edited skill
"""
from __future__ import annotations

import json
import sys

WANT = sys.argv[1] if len(sys.argv) > 1 else "maya-autorig"
out = {"want": WANT}


def _names(inst):
    try:
        return sorted(getattr(s, "name", s) if not isinstance(s, dict) else s.get("name")
                      for s in inst.list_skills())
    except Exception as exc:  # noqa: BLE001
        return "<list_skills: {}>".format(exc)


try:
    from dcc_mcp_maya import server as srv
    inst = srv._server_instance
    before = _names(inst)
    out["known_before"] = len(before) if isinstance(before, list) else before
    out["had_it"] = isinstance(before, list) and WANT in before
    force = "--force" in sys.argv
    if out["had_it"] and force:
        # already known: unload so the reload picks up an edited tools.yaml
        try:
            out["unload_skill"] = str(inst.unload_skill(WANT))[:200]
        except Exception as exc:  # noqa: BLE001
            out["unload_skill"] = "{}: {}".format(type(exc).__name__, str(exc)[:200])
    if not out["had_it"] or force:
        try:
            r = inst.reload_skill_paths()
            out["reload_skill_paths"] = str(r)[:300]
        except Exception as exc:  # noqa: BLE001
            out["reload_skill_paths"] = "{}: {}".format(type(exc).__name__, str(exc)[:300])
    after = _names(inst)
    out["found"] = isinstance(after, list) and WANT in after
    out["known_after"] = len(after) if isinstance(after, list) else after
    if out["found"]:
        try:
            out["load_skill"] = str(inst.load_skill(WANT))[:400]
        except Exception as exc:  # noqa: BLE001
            out["load_skill"] = "{}: {}".format(type(exc).__name__, str(exc)[:300])
        try:
            out["info"] = str(inst.get_skill_info(WANT))[:500]
        except Exception as exc:  # noqa: BLE001
            out["info"] = str(exc)[:200]
except Exception as exc:  # noqa: BLE001
    out["error"] = "{}: {}".format(type(exc).__name__, exc)
print("MCP_RESCAN " + json.dumps(out, default=str))
