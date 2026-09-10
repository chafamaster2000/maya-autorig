"""Place the AdvancedSkeleton FitSkeleton from the markers, then verify it.

Replaces asFitAutoPlace entirely. That proc scans the mesh itself, dies on
arms-down models (unguarded asFitShoulderStraight) and can run for many
minutes on five-fingered hands; with markers there is nothing to scan.

Flow: AS bootstrap -> import template (bipedBendy, the yardstick's) ->
derive the body joint positions from markers + mesh -> hand geometry (finger
lobes, thumb, the real wrist; hand_geom) -> drop the finger chains the mesh
does not have -> apply in hierarchy order -> verify_fit (inside-mesh test,
side convention, proportions, renders).

`fingers`: "auto" (default) keeps the chains the mesh shows (5 on a modelled
hand, 1 mitten chain on a blob), "full" forces all five, "none" drops them
all and adds a WristEnd (the pre-hand behaviour).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

import maya.cmds as cmds

import autorig_common as ac
import hand_geom as hg
import marker_geom as mg
import markers as mk
import mcp_result
import verify_fit


def _copy_fit_attrs(src: str, dst: str) -> List[str]:
    """AS fit joints carry user attributes (fat, fatY, ...); a hand-made joint
    needs the same set so the build treats it like a template joint."""
    copied = []
    for attr in cmds.listAttr(src, userDefined=True) or []:
        if cmds.attributeQuery(attr, node=dst, exists=True):
            continue
        typ = cmds.getAttr("{}.{}".format(src, attr), type=True)
        kw = {"longName": attr, "keyable": cmds.getAttr("{}.{}".format(src, attr), keyable=True)}
        if typ == "string":
            cmds.addAttr(dst, dataType="string", **kw)
            cmds.setAttr("{}.{}".format(dst, attr), cmds.getAttr("{}.{}".format(src, attr)) or "", type="string")
        elif typ in ("double", "float", "long", "short", "bool", "enum"):
            cmds.addAttr(dst, attributeType=typ, **kw)
            cmds.setAttr("{}.{}".format(dst, attr), cmds.getAttr("{}.{}".format(src, attr)))
        else:
            continue
        copied.append(attr)
    return copied


def _scale_fat_to_mesh(mesh: str) -> Dict[str, Any]:
    """AdvancedSkeleton sizes every controller from the fit joints' `fat`
    attribute, and its own FitSkeleton-scale step multiplies `fat` by the
    scale factor (AdvancedSkeleton.mel ~6784). The templates are ~17 units
    tall; placing joints on a 185 cm body by world position leaves `fat` at
    template scale and every control 10x too small (the artist's rig had
    them scaled with the FitSkeleton). Do what the artist's scale did."""
    joints = ac.fit_joints()
    ys = [cmds.xform(j, query=True, worldSpace=True, translation=True)[1] for j in joints]
    tpl_h = (max(ys) - min(ys)) if ys else 0.0
    bb = cmds.exactWorldBoundingBox(mesh)
    H = bb[4] - bb[1]
    if tpl_h < 1e-6:
        return {"factor": 1.0, "reason": "no fit joints"}
    factor = H / tpl_h
    n = 0
    for j in joints:
        if cmds.attributeQuery("fat", node=j, exists=True):
            cmds.setAttr(j + ".fat", cmds.getAttr(j + ".fat") * factor)
            n += 1
    return {"template_height": round(tpl_h, 2), "mesh_height": round(H, 2), "factor": round(factor, 3), "joints": n}


def _drop_fingers(keep: Sequence[str] = ()) -> Dict[str, Any]:
    """Delete the finger chains (and Cup) the hand analysis did not place;
    `keep` are fit joint names that stay."""
    keep = set(keep)
    roots = [j for j in ac.fit_joints()
             if (("Finger" in j and j.endswith("1")) or j == "Cup") and j not in keep and cmds.objExists(j)]
    # Cup stays whenever one of its fingers stays; a kept Ring/Pinky under a
    # deleted Cup would vanish with it.
    if "Cup" in roots and any(k in keep for k in ("RingFinger1", "PinkyFinger1")):
        roots.remove("Cup")
    if roots:
        cmds.delete(roots)
    out: Dict[str, Any] = {"deleted_chains": roots, "kept": sorted(keep)}
    # AS builds no FK control for a leaf joint, and the elbow's twist setup
    # then dies looking for WristBM. An *End child fixes that without adding
    # a controller (same pattern as HeadEnd / ToesEnd in the templates).
    if cmds.objExists("Wrist") and not cmds.listRelatives("Wrist", children=True, type="joint"):
        cmds.select("Wrist", replace=True)
        end = cmds.joint(name="WristEnd")
        out["wrist_end_created"] = end
        out["wrist_end_attrs"] = _copy_fit_attrs("HeadEnd", end) if cmds.objExists("HeadEnd") else []
    out["remaining"] = len(ac.fit_joints())
    return out


def _apply_positions(P: Dict[str, Any]) -> Dict[str, Any]:
    """Set world positions parent-first so a child's world position is set
    after its parent moved. Unknown fit joints are reported, not guessed."""
    applied, skipped = [], []
    for j in ac.fit_joints():  # already depth-sorted
        if j in P:
            cmds.xform(j, worldSpace=True, translation=[float(v) for v in P[j]])
            applied.append(j)
        else:
            skipped.append(j)
    return {"applied": applied, "skipped": skipped}


# Where along the torso's front-to-back span each centre joint sits.
DEPTH_FRAC = {"Root": 0.45, "Hip": 0.45, "Spine1": 0.4, "Spine2": 0.35, "Chest": 0.35, "Neck": 0.5,
              "Head": 0.45, "Jaw": 0.6}
LIMB_DEPTH = ("Scapula", "Shoulder", "Elbow", "Wrist", "Knee", "Ankle")


def _center_depth_from_rays(mesh: str, P: Dict[str, Any]) -> Dict[str, Any]:
    """Re-derive the z of the spine/hip joints from the mesh itself: the
    longest solid span along Z at each joint's (x, y). Silhouette heuristics
    cannot tell a belly from a braid; a ray through the body can."""
    shell = ac.body_shell_copy(mesh)
    bb = cmds.exactWorldBoundingBox(mesh)
    H = bb[4] - bb[1]
    report: Dict[str, Any] = {}
    try:
        for name, frac in DEPTH_FRAC.items():
            if name not in P:
                continue
            x, y, z_old = P[name]
            segs = ac.interior_segments(shell, x, y)
            lifted = 0.0
            # Between two baggy legs the crotch ray hits nothing (chibi case
            # 3: Root 4 cm outside). Climb until the column is solid; the
            # pelvis joint may sit a little higher, never in mid air.
            while not segs and name in ("Root", "Hip") and lifted < 0.25 * H:
                lifted += 0.01 * H
                segs = [s for s in ac.interior_segments(shell, x, y + lifted) if s[1] - s[0] > 0.06 * H]
            if not segs:
                report[name] = {"segments": 0, "kept": round(z_old, 2)}
                continue
            zb, zf = segs[0]
            z_new = zb + frac * (zf - zb)
            P[name] = [x, y + lifted, z_new]
            if name == "Root" and lifted > 0 and "Neck" in P:
                # The spine was spaced from the old root: re-space it between
                # the lifted pelvis and the neck (its own depth pass follows).
                for sp, f in (("Spine1", 0.24), ("Spine2", 0.48), ("Chest", 0.73)):
                    if sp in P:
                        P[sp][1] = P["Root"][1] + f * (P["Neck"][1] - P["Root"][1])
            report[name] = {"span": [round(zb, 2), round(zf, 2)], "z_before": round(z_old, 2),
                            "z_after": round(z_new, 2), "segments": len(segs), "lifted": round(lifted, 2)}
        # Limb joints: the Z ray at (x, y) crosses the limb (and maybe the
        # torso next to it). A joint already inside a solid span and clear of
        # its walls stays; one outside, or hugging a wall (wrist 0.2 cm out on
        # a gloved character, shoulder 17 cm behind a thick jacket), goes to the middle
        # of the nearest span.
        for name in LIMB_DEPTH:
            if name not in P:
                continue
            x, y, z_old = P[name]
            segs = ac.interior_segments(shell, x, y)
            if not segs:
                report[name] = {"segments": 0, "kept": round(z_old, 2)}
                continue
            margin = 0.012 * H
            inside = [s for s in segs if s[0] + margin <= z_old <= s[1] - margin]
            if inside:
                report[name] = {"kept": round(z_old, 2), "span": [round(v, 2) for v in inside[0]]}
                continue
            near = min(segs, key=lambda s: 0.0 if s[0] <= z_old <= s[1] else min(abs(z_old - s[0]), abs(z_old - s[1])))
            z_new = 0.5 * (near[0] + near[1])
            P[name] = [x, y, z_new]
            report[name] = {"span": [round(near[0], 2), round(near[1], 2)], "z_before": round(z_old, 2),
                            "z_after": round(z_new, 2), "segments": len(segs)}
    finally:
        cmds.delete(shell)
    return report


def _fingers_mode(fingers: Any) -> str:
    if fingers is True:
        return "full"
    if fingers is False or fingers is None:
        return "none"
    mode = str(fingers).lower()
    if mode not in ("auto", "full", "none"):
        raise ValueError("fingers must be auto|full|none, got {!r}".format(fingers))
    return mode


def _hand_step(mesh: str, P: Dict[str, Any], H: float, mode: str) -> Dict[str, Any]:
    """Finger lobes from the mesh -> finger fit joints + the real wrist.
    Returns the report; mutates P (Wrist, finger joints)."""
    V = ac.world_points(mesh)
    tris = ac.mesh_triangles(mesh)
    hand = hg.analyze_hand(V, tris, P["Wrist"], P["Elbow"], H)
    report = hg.hand_report(hand, H)
    report["mode"] = mode
    if mode == "full" and hand["kind"] != "fingers":
        report["note"] = "fingers=full on a mitten: single MiddleFinger chain (no lobes to place five on)"
    FP = hg.finger_fit_positions(hand, H)
    if hand.get("wrist_shift_frac_H", 0.0) > 0.0:
        report["wrist_before"] = [round(v, 2) for v in P["Wrist"]]
        P["Wrist"] = list(hand["wrist"])
        report["wrist_rule"] = hand.get("wrist_rule")
    if FP and hand.get("region_n", 0) > 0:
        _comps, sov = ac.shells(mesh)
        shell_tris = hg.hand_shell_tris(tris, sov, hand["_region_idx"])
        report["nudged"] = hg.nudge_inside(V, shell_tris, hand, FP)
        report["inside"] = hg.joints_inside(V, shell_tris, FP)
    else:
        report["inside"] = {}
    P.update(FP)
    report["joints"] = sorted(FP)
    return report


def _eye_step(mesh: str, P: Dict[str, Any], H: float) -> Dict[str, Any]:
    """Eye joint from the eyeball shells when the mesh has them; the
    proportional placement stays otherwise."""
    V = ac.world_points(mesh)
    comps, sov = ac.shells(mesh)
    sov = np.asarray(sov)
    open_ratio = ac.shell_open_ratios(mesh, comps, sov)
    stats = []
    for i in range(len(comps)):
        pts = V[sov == i]
        if len(pts) == 0:
            stats.append({"n": 0, "open": 1.0, "centre": [0, 0, 0], "bbox": [0, 0, 0]})
            continue
        stats.append({"n": int(len(pts)), "open": float(open_ratio[i]), "centre": pts.mean(0).tolist(),
                      "bbox": (pts.max(0) - pts.min(0)).tolist()})
    eye = mg.eye_from_shells(stats, H, P["Neck"][1], P["Head"][2])
    if eye is None:
        return {"found": False, "shells": len(stats)}
    before = list(P["Eye"])
    c = eye["centre"]
    P["Eye"] = [c[0], c[1], c[2]]
    P["EyeEnd"] = [c[0], c[1], c[2] + eye["radius"]]
    return {"found": True, "centre": [round(v, 2) for v in c], "radius": round(eye["radius"], 2),
            "before": [round(v, 2) for v in before],
            "shift": round(float(np.linalg.norm(np.asarray(c) - np.asarray(before))), 2)}


def main(mesh: str = "Mesh", template: str = "bipedBendy.ma", fingers: Any = "auto",
         evidence_dir: Optional[str] = None, compact: bool = True, **_kw) -> Dict[str, Any]:
    if not cmds.objExists(mesh):
        raise ValueError("mesh {!r} not found".format(mesh))
    mode = _fingers_mode(fingers)
    out: Dict[str, Any] = {"mesh": mesh, "template": template, "fingers": mode}
    out["as"] = ac.as_ready(mesh)
    out["import"] = ac.import_fit_skeleton(template)
    out["fat_scale"] = _scale_fat_to_mesh(mesh)

    read = mk.read(mesh, refine=True, write_back=True)
    out["markers"] = {"symmetrized_right": read["symmetrized_right"],
                      "refine_shift": read["refine_shift"],
                      "left_right_disagreement": read["left_right_disagreement"],
                      "optional_present": read["optional_present"]}
    if "warning" in read:
        out["markers"]["warning"] = read["warning"]

    B, _info = ac.body_points(mesh)
    body = mg.analyze_body(B)
    P = mg.derive_fit_positions(read["symmetrized_right"], B, body, spine2="Spine2" in ac.fit_joints())
    out["depth_fix"] = _center_depth_from_rays(mesh, P)
    # Bend direction last: the ray depth fix just moved the hip in z, which is
    # one end of the hip->ankle line the knee is measured against.
    mg._enforce_bend_direction(P, body["height"])
    # Hand last: it reads the wrist/elbow the body pass settled on, may move
    # the wrist down a sleeve, and adds the finger chains the mesh has.
    hand = None
    if mode != "none":
        hand = _hand_step(mesh, P, body["height"], mode)
        out["hand"] = hand
    if "Eye" in P and "Neck" in P and "Head" in P:
        out["eye"] = _eye_step(mesh, P, body["height"])
    out["fingers_dropped"] = _drop_fingers(keep=hand["joints"] if hand else ())
    out["positions"] = {k: [round(v, 2) for v in p] for k, p in P.items()}
    out["apply"] = _apply_positions(P)
    out["bone_lengths"] = mg.bone_length_report(P, body["height"])

    out["verify"] = verify_fit.main(mesh=mesh, hand=hand, evidence_dir=evidence_dir)
    out["passed"] = out["verify"]["passed"]
    # Markers are spent now. Hide them on success to declutter the viewport;
    # keep them visible on failure so the user can re-drag and re-run.
    out["markers_hidden"] = mk.set_visible(False) if out["passed"] else {"visible": True}
    if evidence_dir:
        ev = ac.Evidence(evidence_dir)
        ev.record("fit", out["passed"], out,
                  images=[r["path"] for r in out["verify"].get("renders", []) if r.get("ok")],
                  expectation=verify_fit.EXPECTATION)
    if compact:
        return mcp_result.slim(out, "fit", evidence_dir, metrics={
            "left_right_disagreement": out["markers"]["left_right_disagreement"],
            "warning": out["markers"].get("warning"),
            "fit_joints_applied": len(out["apply"]["applied"]),
            "hand": {k: hand.get(k) for k in ("kind", "names", "thumb", "wrist_shift_frac_H")} if hand else None,
            "markers_hidden": out["markers_hidden"].get("visible") is False})
    return out
