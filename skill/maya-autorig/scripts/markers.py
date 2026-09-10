"""Mixamo-style markers as Maya locators: propose, let the user drag, read back.

Stage contract:
  propose(mesh, pose)  -> 8 required + 4 optional locators under AutoRigMarkers,
                          pre-placed from geometry, plus a front render.
  read(mesh)           -> current locator positions, refined onto the limb axis
                          (written back so the correction is visible), folded
                          onto -X for the FitSkeleton, with the L/R disagreement.

The user is authoritative on the coordinate along each limb; the refinement
only re-centres across the limb. Optional markers (shoulder, ankle) override
their derivation while they exist; delete them to fall back to derivation.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np

import maya.cmds as cmds

import autorig_common as ac
import marker_geom as mg
import mcp_result

REQUIRED = list(mg.MIXAMO_MARKERS)
OPTIONAL = list(mg.OPTIONAL_MARKERS)
GROUP = ac.MARKER_GROUP
COLOR = {"required": 13, "optional": 17}  # red, yellow


def _loc(name: str) -> str:
    return "mk_" + name


def _make_locator(name: str, pos, size: float, color: int) -> str:
    loc = cmds.spaceLocator(name=_loc(name))[0]
    shape = cmds.listRelatives(loc, shapes=True)[0]
    for ax in "XYZ":
        cmds.setAttr("{}.localScale{}".format(shape, ax), size)
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", color)
    cmds.xform(loc, worldSpace=True, translation=[float(v) for v in pos])
    cmds.parent(loc, GROUP)
    return loc


def propose(mesh: str = "Mesh", pose: str = "A", evidence_dir: Optional[str] = None,
            replace: bool = True, compact: bool = True, **_kw) -> Dict[str, Any]:
    """Create the marker locators from geometry. `pose` is what the user
    declared ("A" or "T"); the geometric classification is only a cross-check."""
    if not cmds.objExists(mesh):
        raise ValueError("mesh {!r} not found".format(mesh))
    # The user declares the pose: A, T, or arms down. It only steers where the
    # placeholders go when the arm detector finds nothing; the markers the
    # user leaves behind are the pose, whatever the detector concluded.
    hint = {"a": "A", "t": "T"}.get(pose.strip().lower(), "down")
    B, shell_info = ac.body_points(mesh)
    body = mg.analyze_body(B)
    prop = mg.propose_markers(B, body, pose_hint=hint)
    H = body["height"]
    detected = body["pose"]

    out: Dict[str, Any] = {"mesh": mesh, "pose_declared": hint, "pose_detected": detected,
                           "arm_angle_deg": body["arm_angle_deg"], "height": round(H, 2),
                           "shells": shell_info, "notes": prop["notes"]}
    out["pose_agrees"] = {"A": detected == "A", "T": detected == "T",
                          "down": detected in ("arms_down", "arms_down_or_undetected")}[hint]
    if not body["arms"].get("L") and not body["arms"].get("R"):
        out["notes"].append("arms not detected ({}): wrist/elbow markers are {}-pose placeholders; drag "
                            "them -- the markers define the arm angle, the detector is only a hint"
                            .format(detected, hint))

    if cmds.objExists(GROUP):
        if not replace:
            raise RuntimeError("{} already exists; pass replace=true to rebuild the markers".format(GROUP))
        cmds.delete(GROUP)
    cmds.group(empty=True, name=GROUP)
    size = 0.02 * H
    created = {}
    for name in REQUIRED:
        created[name] = _make_locator(name, prop["markers"][name], size, COLOR["required"])
    for name in OPTIONAL:
        if name in prop["optional"]:
            created[name] = _make_locator(name, prop["optional"][name], size * 0.8, COLOR["optional"])
    out["locators"] = created
    out["markers"] = {k: [round(v, 2) for v in prop["markers"][k]] for k in REQUIRED}
    out["optional"] = {k: [round(v, 2) for v in prop["optional"][k]] for k in prop["optional"]}

    if evidence_dir:
        pts = {k: prop["markers"][k] for k in REQUIRED}
        pts.update({k: prop["optional"][k] for k in prop["optional"]})
        out["render"] = ac.render_evidence(os.path.join(evidence_dir, "markers_front.png"), mesh,
                                           camera="front", points=pts, transparent_mesh=True)
    if compact:
        return mcp_result.slim(out, "markers", evidence_dir, metrics={
            "pose_declared": hint, "pose_detected": out["pose_detected"], "pose_agrees": out["pose_agrees"],
            "arm_angle_deg": out["arm_angle_deg"], "height": out["height"],
            "locators": len(created), "notes": out["notes"]})
    return out


def set_visible(visible: bool = True) -> Dict[str, Any]:
    """Show or hide the whole marker group. Markers are consumed by
    fit_from_markers; hiding them afterwards declutters the viewport without
    losing them -- they stay in the scene for a re-adjust, and propose()
    rebuilds them from scratch anyway."""
    if not cmds.objExists(GROUP):
        return {"exists": False, "visible": None}
    if cmds.getAttr(GROUP + ".visibility", settable=True):
        cmds.setAttr(GROUP + ".visibility", bool(visible))
    return {"exists": True, "visible": bool(visible),
            "locators": len(cmds.listRelatives(GROUP, children=True) or [])}


REFINED_ATTR = "autorigRefined"


def _stored_refined(name: str) -> Optional[List[float]]:
    """Position this locator was last refined to, or None if never refined."""
    loc = _loc(name)
    if not cmds.attributeQuery(REFINED_ATTR, node=loc, exists=True):
        return None
    v = cmds.getAttr("{}.{}".format(loc, REFINED_ATTR))
    return [float(x) for x in v[0]] if v else None


def _store_refined(name: str, q: List[float]) -> None:
    loc = _loc(name)
    if not cmds.attributeQuery(REFINED_ATTR, node=loc, exists=True):
        cmds.addAttr(loc, longName=REFINED_ATTR, attributeType="double3")
        for ax in "XYZ":
            cmds.addAttr(loc, longName=REFINED_ATTR + ax, attributeType="double", parent=REFINED_ATTR)
    cmds.setAttr("{}.{}".format(loc, REFINED_ATTR), *[float(v) for v in q], type="double3")


def _axis_for(name: str, P: Dict[str, List[float]]) -> Optional[np.ndarray]:
    """Limb direction used to re-centre a marker across (not along) the limb."""
    side = name[-1]
    if name.startswith(("wrist", "elbow")):
        e, w = P.get("elbow_" + side), P.get("wrist_" + side)
        return np.array(e) - np.array(w) if e and w else None
    if name.startswith("shoulder"):
        s, e = P.get("shoulder_" + side), P.get("elbow_" + side)
        return np.array(s) - np.array(e) if s and e else None
    if name.startswith("knee"):
        k, a = P.get("knee_" + side), P.get("ankle_" + side)
        return np.array(k) - np.array(a) if k and a else np.array([0.0, 1.0, 0.0])
    if name.startswith("ankle"):
        return np.array([0.0, 1.0, 0.0])
    return None


def read(mesh: str = "Mesh", refine: bool = True, write_back: bool = True,
         evidence_dir: Optional[str] = None, **_kw) -> Dict[str, Any]:
    """Read the locators the user has (possibly) moved, refine, symmetrize."""
    if not cmds.objExists(GROUP):
        raise RuntimeError("{} not found; run propose first".format(GROUP))
    missing = [n for n in REQUIRED if not cmds.objExists(_loc(n))]
    if missing:
        raise RuntimeError("missing required markers: {}".format(missing))

    raw: Dict[str, List[float]] = {}
    for name in REQUIRED + OPTIONAL:
        if cmds.objExists(_loc(name)):
            raw[name] = [float(v) for v in cmds.xform(_loc(name), query=True, worldSpace=True, translation=True)]

    B, _info = ac.body_points(mesh)
    H = float(B[:, 1].max() - B[:, 1].min())
    refined: Dict[str, List[float]] = {}
    shifts: Dict[str, float] = {}
    for name, p in raw.items():
        if not refine or name in ("chin", "groin"):
            refined[name] = p
            continue
        stored = _stored_refined(name)
        if stored is not None and np.linalg.norm(np.array(p) - np.array(stored)) < 1e-3:
            # Untouched since its last refine. Re-refining creeps a few mm per
            # run (the slab is centred on the marker), which changed the fit --
            # and the review hash -- between identical reruns for no reason.
            refined[name], shifts[name] = p, 0.0
            continue
        q, s = mg.refine_limb_marker(B, p, H, _axis_for(name, raw))
        refined[name], shifts[name] = q, round(s, 2)
        if write_back:
            if s > 1e-3:
                cmds.xform(_loc(name), worldSpace=True, translation=q)
            _store_refined(name, q)

    sym, asym = mg.symmetrize_to_right(refined)
    out: Dict[str, Any] = {
        "mesh": mesh, "height": round(H, 2),
        "raw": {k: [round(v, 2) for v in p] for k, p in raw.items()},
        "refined": {k: [round(v, 2) for v in p] for k, p in refined.items()},
        "refine_shift": shifts,
        "symmetrized_right": {k: [round(v, 2) for v in p] for k, p in sym.items()},
        "left_right_disagreement": {k: round(v, 2) for k, v in asym.items()},
        "optional_present": [n for n in OPTIONAL if n in raw],
    }
    big = {k: v for k, v in asym.items() if v > 0.04 * H}
    if big:
        out["warning"] = "L/R markers disagree by more than 4% of height: {} (FitSkeleton is one-sided; they get averaged)".format(big)
    if evidence_dir:
        out["render"] = ac.render_evidence(os.path.join(evidence_dir, "markers_read_front.png"), mesh,
                                           camera="front", points=refined, transparent_mesh=True)
    return out
