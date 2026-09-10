"""Bind the character's props (a bag, a bomb, a weapon) to the rig.

A prop is not flesh: it does not stretch, it does not blend between bones,
it rides one bone. So it gets a one-influence skinCluster on the deform
joint whose *bone* (joint to child, not just the joint's pivot) runs closest
to the prop -- a bag across the back lands on the chest, a bomb in a fist
lands on the wrist, a sword on its hand.

Uniting props into the body instead (prep_mesh extras="unite") makes the
geodesic solver treat a steel bomb like a belly: it deforms.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np

import maya.cmds as cmds

import autorig_common as ac
import mcp_result

EXPECTATION = (
    "Maya viewport in X-ray: every prop (bag, bomb, weapon) sits where it did before the rig was built, "
    "and each one is bound to a single bone -- so when that bone moves the prop moves with it, rigidly, "
    "without stretching or tearing."
)

# A prop bound to a bone whose own centre is further than this (fraction of
# the character's height) is reported: it is probably a stray shell, not a
# prop worn on the body.
FAR_FRAC_H = 0.35
# Set dressing, not equipment: a prop resting on the ground near the body's
# own centre line follows the root, never a limb. The stylised short character ships a bomb
# lying between its feet; nearest-bone put it on a knee, so walking would
# have kicked it along. Deciding by geometry beats guessing at intent.
FLOOR_BAND_H = 0.03
CENTRE_BAND_H = 0.12


def deform_joints(joint_root: str = "DeformationSystem") -> List[str]:
    if not cmds.objExists(joint_root):
        return []
    return [j for j in (cmds.listRelatives(joint_root, allDescendents=True, type="joint") or [])
            if not j.endswith("End")]


def _bones(joints: Sequence[str]) -> List[Dict[str, Any]]:
    """(joint, a, b): the segment from a joint to its first child joint. A
    leaf joint is a point, which is still a usable segment of zero length."""
    out = []
    for j in joints:
        a = np.array(cmds.xform(j, query=True, worldSpace=True, translation=True))
        kids = [k for k in (cmds.listRelatives(j, children=True, type="joint") or []) if k in joints]
        b = np.array(cmds.xform(kids[0], query=True, worldSpace=True, translation=True)) if kids else a
        out.append({"joint": j, "a": a, "b": b})
    return out


def _dist_to_segment(P: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    denom = float(ab @ ab)
    if denom < 1e-12:
        return np.linalg.norm(P - a, axis=1)
    t = np.clip((P - a) @ ab / denom, 0.0, 1.0)
    return np.linalg.norm(P - (a + np.outer(t, ab)), axis=1)


def best_bone(points: np.ndarray, bones: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The bone that is closest to the prop as a whole: the smallest mean
    distance over its points. A mean, not a minimum -- a bag brushing a hand
    should still ride the chest it hangs on."""
    best = None
    for bone in bones:
        d = _dist_to_segment(points, bone["a"], bone["b"])
        score = float(d.mean())
        if best is None or score < best["mean_dist"]:
            best = {"joint": bone["joint"], "mean_dist": score, "min_dist": float(d.min())}
    return best or {}


def prop_meshes(body: str) -> List[str]:
    """Every renderable mesh in the scene that is not the body and is not
    already skinned (props the rig has not claimed yet)."""
    out = []
    body_long = (cmds.ls(body, long=True) or [body])[0]
    for shape in cmds.ls(type="mesh", long=True, noIntermediate=True):
        xf = (cmds.listRelatives(shape, parent=True, fullPath=True) or [None])[0]
        if not xf or xf == body_long or xf.startswith(ac.TMP_PREFIX) or "|" + ac.TMP_PREFIX in xf:
            continue
        short = xf.split("|")[-1]
        if short == body or cmds.ls(cmds.listHistory(xf) or [], type="skinCluster"):
            continue
        out.append(short)
    return sorted(set(out))


def main(mesh: str = "Mesh", props: Optional[Sequence[str]] = None, joint_root: str = "DeformationSystem",
         evidence_dir: Optional[str] = None, render: bool = True, compact: bool = True,
         **_kw) -> Dict[str, Any]:
    joints = deform_joints(joint_root)
    out: Dict[str, Any] = {"mesh": mesh, "joint_root": joint_root, "attached": [], "skipped": []}
    if not joints:
        out.update({"passed": False, "failed": [{"check": "rig_present", "detail": "no " + joint_root}]})
        return mcp_result.slim(out, "props", evidence_dir) if compact else out

    names = list(props) if props is not None else prop_meshes(mesh)
    out["props_found"] = names
    if not names:
        out.update({"passed": True, "checks": [], "failed": [], "note": "no props in the scene"})
        return mcp_result.slim(out, "props", evidence_dir) if compact else out

    bb = cmds.exactWorldBoundingBox(mesh)
    H = bb[4] - bb[1]
    bones = _bones(joints)
    checks: List[Dict[str, Any]] = []
    for name in names:
        if not cmds.objExists(name):
            out["skipped"].append({"prop": name, "why": "not in the scene"})
            continue
        P = ac.world_points(name)
        on_floor = bool(P[:, 1].min() - bb[1] < FLOOR_BAND_H * H
                        and abs(float(P[:, 0].mean()) - 0.5 * (bb[0] + bb[3])) < CENTRE_BAND_H * H)
        root = next((j for j in ("Root_M", "Root") if j in joints), None)
        pick = {"joint": root, "mean_dist": 0.0, "min_dist": 0.0} if (on_floor and root) else best_bone(P, bones)
        if not pick or not pick.get("joint"):
            out["skipped"].append({"prop": name, "why": "no bone matched"})
            continue
        rest = P.mean(0)
        # One influence, full weight: a prop rides its bone, it does not blend.
        cluster = cmds.skinCluster(pick["joint"], name, toSelectedBones=True, bindMethod=0,
                                   skinMethod=0, maximumInfluences=1, obeyMaxInfluences=True,
                                   normalizeWeights=1, name=name + "_propCluster")[0]
        after = ac.world_points(name).mean(0)
        drift = float(np.linalg.norm(after - rest))
        far = (not on_floor) and pick["mean_dist"] > FAR_FRAC_H * H
        entry = {"prop": name, "joint": pick["joint"], "cluster": cluster,
                 "verts": int(len(P)), "rule": "on the ground: rides the root" if on_floor else "nearest bone",
                 "on_floor": on_floor,
                 "mean_dist_frac_H": round(pick["mean_dist"] / H, 3),
                 "min_dist_frac_H": round(pick["min_dist"] / H, 3),
                 "drift_cm": round(drift, 4), "far_from_body": far}
        out["attached"].append(entry)
        checks.append({"check": "prop_" + name, "ok": drift < 1e-3 and not far, "detail": entry})

    out["checks"] = checks
    out["passed"] = all(c["ok"] for c in checks) and not out["skipped"]
    out["failed"] = [c for c in checks if not c["ok"]]
    renders = []
    if render and evidence_dir and out["attached"]:
        r = ac.render_evidence(_p(evidence_dir, "props_front.png"), mesh, "front", None, transparent_mesh=False)
        renders.append(r)
        out["renders"] = renders
        out["expectation"] = EXPECTATION
    if evidence_dir:
        ac.Evidence(evidence_dir).record("props", out["passed"], out,
                                         images=[r["path"] for r in renders if r.get("ok")],
                                         expectation=EXPECTATION)
    if compact:
        return mcp_result.slim(out, "props", evidence_dir, metrics={
            "props": len(out["attached"]),
            "bones": [a["joint"] for a in out["attached"]]})
    return out


def _p(*parts: str) -> str:
    import os
    return os.path.join(*parts)
