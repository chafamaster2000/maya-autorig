"""Build the AdvancedSkeleton rig from the placed FitSkeleton, with evidence.

The UI button calls asReBuildAdvancedSkeleton, which on a first build just
forwards to asBuildAdvancedSkeleton after some checks. Every one of those
checks that can raise a modal dialog is reproduced here as a plain error
first, because a dialog deadlocks the MCP session:

  * asDetectPreviousFails: empty AllSet, or |Group next to |FitSkeleton
  * unit dialog: linear unit must be cm
  * underscore in a fit joint name, old bendyJoints attribute
Reserved-name collisions (Group, MotionSystem, ...) are MEL errors, which
surface as Python exceptions, so they need no special handling.

Verification compares the deformation joints AS created against the fit
joints: same positions on the right side, mirrored on the left, centre joints
on x=0. That is the proof the build used our placement and nothing else.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import maya.cmds as cmds
import maya.mel as mel

import autorig_common as ac
import mcp_result

RESERVED = ("Group", "MotionSystem", "DeformationSystem", "Geometry", "Main")
REQUIRED_NODES = ("Group", "MotionSystem", "DeformationSystem", "Main", "DeformSet", "ControlSet", "AllSet")
# fit joint -> deform joint suffix. End joints are effectors, not deformers.
CENTER_JOINTS = ("Root", "Spine1", "Spine2", "Chest", "Neck", "Head", "Jaw")
SIDE_JOINTS = ("Scapula", "Shoulder", "Elbow", "Wrist", "Hip", "Knee", "Ankle", "Toes", "Eye")
SAMPLE_CONTROLS = ("FKShoulder_R", "FKElbow_R", "FKWrist_R", "FKHip_R", "FKKnee_R", "FKAnkle_R",
                   "FKShoulder_L", "FKElbow_L", "FKHead_M", "FKChest_M", "RootX_M", "IKArm_R", "IKLeg_R")

EXPECTATION = (
    "See-through character with red dots at every deformation joint of the built rig, "
    "on BOTH sides now: spine chain up to the head, both arms shoulder->elbow->wrist, "
    "both legs hip->knee->ankle->toes. Dots must sit inside the body and be mirror-"
    "symmetric left/right. No dot floating outside the silhouette."
)


def guards(mesh: str) -> List[str]:
    problems = []
    unit = cmds.currentUnit(query=True, linear=True)
    if unit not in ("cm", "centimeter"):
        problems.append("linear unit is {} (AS would raise the unit dialog)".format(unit))
    if not cmds.objExists("FitSkeleton"):
        problems.append("no FitSkeleton in scene")
    for n in RESERVED:
        if cmds.objExists(n):
            problems.append("reserved node {!r} already exists (rig already built? rename or delete)".format(n))
    if cmds.objExists("AllSet") and not cmds.sets("AllSet", query=True):
        problems.append("empty AllSet from a failed previous build (undo it first)")
    if cmds.objExists("|Group") and cmds.objExists("|FitSkeleton"):
        problems.append("|Group next to |FitSkeleton: a previous build failed (undo it first)")
    if cmds.objExists("prefix_Group"):
        problems.append("prefix_Group found: a previous build failed (undo it first)")
    for j in ac.fit_joints():
        if "_" in j and not j.endswith("_NonSymmetry"):
            problems.append("underscore in fit joint name {!r} (AS would ask to confirm)".format(j))
        if cmds.attributeQuery("bendyJoints", node=j, exists=True) and cmds.getAttr(j + ".bendyJoints"):
            problems.append("old bendyJoints attribute on {!r} (AS would ask to update)".format(j))
    blocked = ac.model_checker_blockers(mesh)
    problems += blocked
    return problems


def verify(mesh: str, fit_before: Dict[str, List[float]], evidence_dir: str = None,
           render: bool = True, tol: float = 0.5) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    missing = [n for n in REQUIRED_NODES if not cmds.objExists(n)]
    check("rig_nodes_exist", not missing, {"missing": missing})
    deform = cmds.listRelatives("DeformationSystem", allDescendents=True, type="joint") or [] \
        if cmds.objExists("DeformationSystem") else []
    check("deform_joints_present", len(deform) >= 20, {"count": len(deform)})
    ctrl_missing = [c for c in SAMPLE_CONTROLS if not cmds.objExists(c)]
    check("sample_controls_exist", not ctrl_missing, {"missing": ctrl_missing})

    # deform joints must be where the fit joints were (right) / mirrored (left)
    pos: Dict[str, List[float]] = {}
    drift: Dict[str, float] = {}
    for j in deform:
        pos[j] = [round(v, 2) for v in cmds.xform(j, query=True, worldSpace=True, translation=True)]
    for f in CENTER_JOINTS:
        d = f + "_M"
        if f in fit_before and d in pos:
            drift[d] = round(_dist(pos[d], fit_before[f]), 2)
    for f in SIDE_JOINTS:
        if f not in fit_before:
            continue
        fx, fy, fz = fit_before[f]
        if f + "_R" in pos:
            drift[f + "_R"] = round(_dist(pos[f + "_R"], [fx, fy, fz]), 2)
        if f + "_L" in pos:
            drift[f + "_L"] = round(_dist(pos[f + "_L"], [-fx, fy, fz]), 2)
    bad = {k: v for k, v in drift.items() if v > tol}
    check("deform_joints_match_fit", bool(drift) and not bad, {"max_drift": max(drift.values()) if drift else None, "over_tol": bad})
    off_center = {j: pos[j][0] for j in pos if j.endswith("_M") and abs(pos[j][0]) > 1e-3}
    check("center_joints_on_x0", not off_center, off_center)

    out: Dict[str, Any] = {"checks": checks, "passed": all(c["ok"] for c in checks),
                           "failed": [c for c in checks if not c["ok"]],
                           "deform_joint_count": len(deform), "drift": drift,
                           "controls_total": len(cmds.sets("ControlSet", query=True) or []) if cmds.objExists("ControlSet") else 0}
    if render and evidence_dir and pos:
        out["renders"] = [
            ac.render_evidence(os.path.join(evidence_dir, "build_front.png"), mesh, "front", pos),
            ac.render_evidence(os.path.join(evidence_dir, "build_side.png"), mesh, "side", pos),
        ]
        out["expectation"] = EXPECTATION
    return out


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def main(mesh: str = "Mesh", evidence_dir: str = None, compact: bool = True, **_kw) -> Dict[str, Any]:
    out: Dict[str, Any] = {"mesh": mesh}
    problems = guards(mesh)
    if problems:
        raise RuntimeError("build blocked: " + "; ".join(problems))
    ac.as_ready(mesh)
    fit_before = ac.fit_positions()
    out["fit_joint_count"] = len(fit_before)
    mel.eval("asBuildAdvancedSkeleton;")
    out["verify"] = verify(mesh, fit_before, evidence_dir)
    out["passed"] = out["verify"]["passed"]
    if evidence_dir:
        ac.Evidence(evidence_dir).record(
            "build", out["passed"], out,
            images=[r["path"] for r in out["verify"].get("renders", []) if r.get("ok")],
            expectation=EXPECTATION)
    if compact:
        return mcp_result.slim(out, "build", evidence_dir, metrics={"fit_joint_count": out.get("fit_joint_count")})
    return out
