"""Report the embedded Maya MCP server state: plugin loaded, server running,
which skills it knows and which search paths it scanned. Diagnostic for
'why does the gateway not see my skill'.

    run_script tools/mcp_status.py
"""
from __future__ import annotations

import json
import os

import maya.cmds as cmds

out = {"plugin_loaded": bool(cmds.pluginInfo("dcc_mcp_maya_plugin", query=True, loaded=True))}
try:
    from dcc_mcp_maya import server as srv
    inst = getattr(srv, "_server_instance", None)
    out["server_instance"] = type(inst).__name__ if inst is not None else None
    out["is_running"] = bool(getattr(inst, "is_running", False)) if inst is not None else False
    names = []
    for attr in dir(inst) if inst is not None else []:
        if "skill" in attr.lower() and not attr.startswith("__"):
            names.append(attr)
    out["skill_attrs"] = names[:20]
    # try the obvious accessors
    for attr in ("skills", "_skills", "loaded_skills", "skill_names", "registry", "_registry", "skill_registry"):
        v = getattr(inst, attr, None) if inst is not None else None
        if v is not None:
            try:
                keys = list(v.keys()) if hasattr(v, "keys") else [getattr(s, "name", str(s)) for s in v]
                out["skills_via_" + attr] = keys[:40]
            except Exception as exc:  # noqa: BLE001
                out["skills_via_" + attr] = "<{}>".format(exc)
    try:
        paths = inst.collect_skill_search_paths(extra_paths=None, include_bundled=True, filter_existing=True)
        out["search_paths"] = [str(p) for p in paths]
    except Exception as exc:  # noqa: BLE001
        out["search_paths_error"] = str(exc)
    out["user_skills_dir"] = os.path.expanduser("~/.dcc-mcp/maya/skills")
    out["user_skills_dir_entries"] = os.listdir(out["user_skills_dir"])
    out["env_skill_paths"] = {k: v for k, v in os.environ.items() if "SKILL" in k.upper() and "DCC" in k.upper()}
except Exception as exc:  # noqa: BLE001
    out["error"] = "{}: {}".format(type(exc).__name__, exc)
print("MCP_STATUS " + json.dumps(out, default=str))
