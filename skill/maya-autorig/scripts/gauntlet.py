"""The gauntlet loop as one tool: raw character -> auto-rig -> profile -> grid.

Builder side of the loop (Matt Shumer's gauntlet: builder + fresh-context
critic against a real bar, exit only when it wins). In one call:

  prep_mesh (optional: import, drop props, ground, freeze) -> markers.propose
  (automatic, no drag) -> harness.run (fit / build / bind / verify) ->
  rig_profile -> (optional) rig_compare against a bar of yours -> critic
  manifest for the ONE blind sonnet call the agent makes afterwards.

Returns the score, the failed rows and the paths; the grid lives in
<evidence_dir>/gauntlet.md. Rerun after fixing a heuristic; the loop ends
when `wins` is true.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

import maya.cmds as cmds

import autorig_common as ac


def run(source: Optional[str] = None, mesh: str = "Mesh", pose: str = "A", bar: Optional[str] = None,
        scene_tag: Optional[str] = None, template: str = "bipedBendy.ma", skin_method: str = "linear",
        max_influences: int = 4, critic: bool = True, max_failed: int = 12, compact: bool = True,
        **_kw) -> Dict[str, Any]:
    import harness
    import markers as mk
    import rig_compare
    import rig_profile

    t: Dict[str, float] = {}
    t0 = time.perf_counter()
    out: Dict[str, Any] = {"success": True, "bar": bar, "pose": pose}
    if source:
        import prep_mesh
        ts = time.perf_counter()
        mesh, prep = prep_mesh.prepare(source, name=mesh)
        out["prep"] = prep
        scenes = os.path.join(cmds.workspace(query=True, rootDirectory=True), "scenes")
        tag = scene_tag or "gauntlet_" + os.path.splitext(os.path.basename(source))[0].replace(" ", "_")
        cmds.file(rename=os.path.join(scenes, tag + ".mb"))
        cmds.file(save=True, type="mayaBinary")
        t["prep"] = round(time.perf_counter() - ts, 2)
    elif not cmds.objExists(mesh):
        raise ValueError("mesh {!r} not in the scene and no source given".format(mesh))
    out["scene"] = cmds.file(query=True, sceneName=True)

    ts = time.perf_counter()
    prop = mk.propose(mesh, pose=pose)
    out["pose_detected"] = prop.get("metrics", prop).get("pose_detected")
    t["propose"] = round(time.perf_counter() - ts, 2)
    ts = time.perf_counter()
    res = harness.run(mesh, start_from="fit", gallery=False, stop_on_fail=True, template=template,
                      skin_method=skin_method, max_influences=max_influences)
    t["harness"] = round(time.perf_counter() - ts, 2)
    out["stages"] = [{"stage": s["stage"], "passed": s["passed"], "seconds": s["seconds"], "error": s.get("error")}
                     for s in res.get("stages", [])]
    out["harness_passed"] = bool(res.get("passed"))
    out["evidence_dir"] = res.get("evidence_dir")
    try:
        import json as _json
        with open(os.path.join(out["evidence_dir"], "fit.json")) as fh:
            hand = _json.load(fh).get("hand") or {}
        out["hand"] = {k: hand.get(k) for k in ("kind", "names", "thumb", "wrist_shift_frac_H", "wrist_rule")}
    except Exception:  # noqa: BLE001 - the hand line is informative
        out["hand"] = None

    skinned = cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")
    if cmds.objExists("DeformationSystem") and skinned:
        ts = time.perf_counter()
        prof = rig_profile.main(mesh=mesh, label=os.path.basename(os.path.normpath(out["evidence_dir"])),
                                evidence_dir=os.path.join(out["evidence_dir"], "profile"))
        t["profile"] = round(time.perf_counter() - ts, 2)
        out["profile_path"] = prof["profile_path"]
        if not bar:
            # No yardstick: the rig is built and measured, there is just
            # nothing to be measured against. Profile a rig you trust with
            # rig_profile(save_as_bar=true) and pass its label as `bar`.
            out.update({"score": None, "wins": None, "hard": None, "failed": [], "failed_total": 0,
                        "grid_md": None, "bar": None,
                        "note": "no bar given: rigged and profiled, not graded"})
            t["TOTAL"] = round(time.perf_counter() - t0, 2)
            out["seconds"] = t
            return out
        cmp_ = rig_compare.main(bar, prof["profile_path"], out_path=os.path.join(out["evidence_dir"], "gauntlet.json"),
                                fit_path=os.path.join(out["evidence_dir"], "fit.json"))
        out.update({"profile_path": prof["profile_path"], "score": cmp_["score"],
                    "hard": "{}/{}".format(cmp_["hard_passed"], cmp_["hard_total"]), "wins": cmp_["wins"],
                    "by_group": cmp_["by_group"], "failed": cmp_["failed"][:max_failed],
                    "failed_total": len(cmp_["failed"]), "grid_md": cmp_["grid_md"], "bar_path": cmp_["bar_path"]})
        if critic:
            import review
            bar_dir = os.path.dirname(cmp_["bar_path"])
            bar_profile_dir = None
            # The bar's renders live next to its profile in the evidence tree
            # (profiles/<label>), not in bars/: find them by label.
            import json
            with open(cmp_["bar_path"]) as fh:
                bar_label = json.load(fh).get("label", bar)
            cand = os.path.join(ac.evidence_root(), "profiles", bar_label)
            if os.path.isdir(cand) and any(n.startswith("pose_") for n in os.listdir(cand)):
                bar_profile_dir = cand
            if bar_profile_dir:
                m = review.critic_manifest(bar_profile_dir, os.path.dirname(prof["profile_path"]))
                out["critic_manifest"] = m["manifest"]
                out["critic_next"] = ("ONE fresh-context critic call (sonnet) with critic_manifest; then "
                                      "critic_verdicts(ours_dir, picks) and rig_compare(critic_path=...)")
            else:
                out["critic_manifest"] = None
                out["critic_note"] = "no renders for bar {!r} under {} (profile it once with render=true)".format(bar, cand)
    else:
        out.update({"score": 0.0, "wins": False, "hard": "0/0",
                    "failed": [{"group": "pipeline", "criterion": "rig + skin exist",
                                "note": res.get("stopped_at") or "harness stopped"}]})
    t["TOTAL"] = round(time.perf_counter() - t0, 2)
    out["seconds"] = t
    return out
