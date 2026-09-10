"""Skin parameter sweep on a built (unskinned) checkpoint, scored by the
gauntlet's skin/deformation rows -- the loop for the skinning half.

Each variant re-binds the mesh, profiles it (no renders) and reports the
rows that matter against the bar. Unity's default skin quality is 4 bones
per vertex (ModelImporter "Standard"); "Custom" goes to 255, so 8 is listed
for information but 4 is the target.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

import maya.cmds as cmds

import autorig_common as ac

# Voxel 512 crashed Maya outright on a stylised short character (stylised short character, 4.4k verts / 46 joints):
# the scene came back as gauntlet_3__build[Recovered-...].ma twice. 256 is the
# ceiling this pipeline ships with; 512 stays in the list for a deliberate,
# supervised sweep on a case that survives it, never as a default.
VARIANTS: List[Dict[str, Any]] = [
    {"name": "geo256_f0.2_i4", "voxel": (256, True), "falloff": 0.2, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo256_f0.5_i4 (default)", "voxel": (256, True), "falloff": 0.5, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo256_f0.75_i4", "voxel": (256, True), "falloff": 0.75, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo256_f1.0_i4", "voxel": (256, True), "falloff": 1.0, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo512_f1.0_i4", "voxel": (512, True), "falloff": 1.0, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo512_f0.5_i4", "voxel": (512, True), "falloff": 0.5, "max_influences": 4, "dropoff": 4.0},
    {"name": "geo256_f0.5_i8 (Unity custom)", "voxel": (256, True), "falloff": 0.5, "max_influences": 8, "dropoff": 4.0},
    {"name": "closest_i4_drop4", "bind": "closest_distance", "max_influences": 4, "dropoff": 4.0},
]
ROWS = ("locality p95 (frac H)", "L/R symmetry L1 mean", "smoothness L1 p95 (per edge)",
        "FKElbow_R bleed outside limb (frac H)", "FKKnee_R bleed outside limb (frac H)",
        "wrist twist 90: forearm radius ratio (min)", "elbows_90 volume ratio", "elbows_90 edge strain p99",
        "arms_up volume ratio", "arms_up edge strain p99", "arms_up edge compression p01",
        "torso_twist edge strain p99", "walk_step edge strain p99")


def run(scene: str, mesh: str = "Mesh", bar: str = "", variants: Sequence[str] = (),
        compact: bool = True, **_kw) -> Dict[str, Any]:
    """`scene`: a <name>__build.mb checkpoint (rig built, mesh unskinned).
    `variants`: name prefixes to run (default: all)."""
    import bind_skin
    import rig_compare
    import rig_profile

    bar_path = rig_compare.resolve_bar(bar)
    with open(bar_path) as fh:
        bar_prof = json.load(fh)
    chosen = [v for v in VARIANTS if not variants or any(v["name"].startswith(o) for o in variants)]
    out_dir = os.path.join(ac.EVIDENCE_ROOT, "skin_sweep", os.path.splitext(os.path.basename(scene))[0])
    os.makedirs(out_dir, exist_ok=True)
    keep_voxel, keep_falloff = bind_skin.GEODESIC_VOXEL, bind_skin.GEODESIC_FALLOFF
    results = []
    try:
        for v in chosen:
            cmds.file(scene, open=True, force=True)
            t0 = time.time()
            bind_skin.GEODESIC_VOXEL = v.get("voxel", keep_voxel)
            bind_skin.GEODESIC_FALLOFF = v.get("falloff", keep_falloff)
            try:
                bind_skin.main(mesh=mesh, bind_method=v.get("bind", "geodesic"), skin_method="linear",
                               max_influences=v["max_influences"], dropoff_rate=v["dropoff"],
                               replace_existing=True, compact=True)
                prof = rig_profile.main(mesh=mesh, label="sweep", render=False,
                                        evidence_dir=os.path.join(out_dir, v["name"].split(" ")[0]), compact=False)
                res = rig_compare.compare(bar_prof, prof)
                rows = {r["criterion"]: (r["ours"], r["verdict"]) for r in res["rows"]}
                results.append({"variant": v["name"], "seconds": round(time.time() - t0, 1),
                                "hard": "{}/{}".format(res["hard_passed"], res["hard_total"]),
                                "skin_deform_fails": sum(1 for r in res["rows"] if r["hard"] and r["group"] in ("skin", "deform") and r["verdict"] != "PASS"),
                                "rows": {k: rows.get(k) for k in ROWS}})
            except Exception as exc:  # noqa: BLE001 - one bad variant must not kill the sweep
                results.append({"variant": v["name"], "error": "{}: {}".format(type(exc).__name__, exc)})
    finally:
        bind_skin.GEODESIC_VOXEL, bind_skin.GEODESIC_FALLOFF = keep_voxel, keep_falloff
    bar_rows = {r["criterion"]: r["bar"] for r in rig_compare.compare(bar_prof, bar_prof)["rows"]}
    report = {"success": True, "scene": scene, "bar": bar, "out_dir": out_dir,
              "bar_rows": {k: bar_rows.get(k) for k in ROWS}, "results": results}
    with open(os.path.join(out_dir, "sweep.json"), "w") as fh:
        json.dump(report, fh, indent=1, default=str)
    if compact:
        best = min((r for r in results if "error" not in r), key=lambda r: r["skin_deform_fails"], default=None)
        return {"success": True, "out_dir": out_dir, "best": best and best["variant"],
                "table": [{"variant": r["variant"], "hard": r.get("hard"), "skin_deform_fails": r.get("skin_deform_fails"),
                           "elbow_bleed": (r.get("rows") or {}).get("FKElbow_R bleed outside limb (frac H)", [None])[0],
                           "error": r.get("error")} for r in results]}
    return report
