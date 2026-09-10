"""Pose gallery: put the rig through animator-style poses and keep the evidence.

verify_skin bends one joint at a time; this drives the rig the way an animator
would -- IK hands and feet, a crouch that folds both legs through IK, a torso
twist, a walk step -- and renders each pose front and side. Numbers per pose:
how far the mesh moved, whether anything exploded, and whether every control
returned exactly to rest. The pictures are for a reviewer.

Every control attribute touched is recorded and restored; the FK/IK blends too.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import maya.cmds as cmds

import autorig_common as ac
import marker_geom as mg
import mcp_result

# Enclosed body volume in a pose vs. the rest pose: a candy-wrapper pinch or a
# collapsed joint loses it, an exploded limb or a skinning bulge gains it. This
# is the number that stands in for the reviewer's eye on "does the skin hold
# up" -- it caught dual quaternion inflating the chest 14 % at full shoulder
# abduction, which the visual reviewer had waved through as "plausible".
VOLUME_TOL = 0.08   # LBS worst case measured -4 %; DQ bulge at arms_up was +15 %

EXPECTATION = (
    "Maya viewport in X-ray, front and side view per pose: the mesh see-through with the "
    "skeleton and the rig's helpers (FK/IK/bendy controls) drawn. The pose must read as intended "
    "(named below), the mesh must follow the skeleton smoothly: no tearing, no limb "
    "collapsed to a point, no body part dragged along that should not move, feet "
    "staying on the floor when the pose says they are planted."
)

# (name, description, FKIK blends, {control: {attr: delta}}, review views)
# Deltas are added to the rest value. Translations are in cm on the control's
# own axes (AS IK controls are world-aligned); rotations in degrees.
# AS joints run X along the bone: rotateX is the twist, Y/Z are the bends
# (elbows and knees flex about Z, forward in the sagittal plane). A pose that
# moves along Z is invisible in a front orthographic view, so each pose names
# the view(s) a reviewer must look at.
POSES: Sequence[Dict[str, Any]] = (
    {"name": "elbows_90", "reads_as": "both elbows flexed 90 degrees, forearms and hands swung forward (visible from the side)",
     "blend": {"FKIKArm_R": 0, "FKIKArm_L": 0}, "review": ["side"],
     "set": {"FKElbow_R": {"rotateZ": 90}, "FKElbow_L": {"rotateZ": 90}}},
    # Anatomical, not a scaled delta and not a fixed point in space. A delta
    # tuned on a 185 cm body leaves a short-armed character's hands at chin
    # height; a fixed point above the head is out of reach for a stubby
    # character and the IK stretches until the shoulders crush. Reaching
    # "as high as this arm reaches" asks every rig the same question about
    # its own shoulder, and never stretches the IK.
    {"name": "arms_up", "reads_as": "both arms raised as high as they reach, hands up beside or above the head (IK)",
     "blend": {"FKIKArm_R": 10, "FKIKArm_L": 10}, "review": ["front"], "set": {},
     "reach": {"IKArm_R": {"from": "Shoulder_R", "to": "Wrist_R", "frac": 0.97, "dir": [-0.28, 1.0, 0.0]},
               "IKArm_L": {"from": "Shoulder_L", "to": "Wrist_L", "frac": 0.97, "dir": [0.28, 1.0, 0.0]}}},
    {"name": "knee_lift_R", "reads_as": "right knee lifted and forward, foot off the floor, left leg planted (IK)",
     "blend": {"FKIKLeg_R": 10, "FKIKLeg_L": 10}, "review": ["side", "front"],
     "set": {"IKLeg_R": {"translateY": 45, "translateZ": 25}}},
    {"name": "crouch", "reads_as": "both feet planted, pelvis dropped, both knees bent (IK legs)",
     "blend": {"FKIKLeg_R": 10, "FKIKLeg_L": 10}, "review": ["front", "side"],
     "set": {"RootX_M": {"translateY": -25}}},
    {"name": "torso_twist", "reads_as": "upper body twisted about the spine to one side (one shoulder forward, one back), head turned the other way; hips and legs unchanged",
     "blend": {}, "review": ["front"],
     # Distribute the twist up the spine so it reads as a rotation, not a
     # single-joint shear. Head counter-turns. X runs along the spine bone.
     "set": {"FKSpine1_M": {"rotateX": 20}, "FKSpine2_M": {"rotateX": 25},
             "FKChest_M": {"rotateX": 30}, "FKHead_M": {"rotateX": -55}}},
    # The only pose that exercises the finger chains. AS puts curl/spread on
    # one master control per hand, so it adapts to whatever fingers the mesh
    # has: a curl attribute for a chain the rig does not have is simply
    # absent and skipped. Very few vertices move (two hands), hence its own
    # min_moved_pct; the hand close-up is the view a reviewer needs.
    {"name": "fist", "reads_as": "both hands closed into fists, fingers curled into the palm and the thumb folded over them; arms unchanged",
     "blend": {}, "review": ["hand"], "cameras": ("hand",), "min_moved_pct": 0.5, "optional_attrs": True,
     "set": {"Fingers_R": {"indexCurl": 10, "middleCurl": 10, "ringCurl": 10, "pinkyCurl": 10, "thumbCurl": 7},
             "Fingers_L": {"indexCurl": 10, "middleCurl": 10, "ringCurl": 10, "pinkyCurl": 10, "thumbCurl": 7}}},
    {"name": "walk_step", "reads_as": "right foot forward, left foot back, arms swinging opposite (visible from the side)",
     "blend": {"FKIKLeg_R": 10, "FKIKLeg_L": 10, "FKIKArm_R": 0, "FKIKArm_L": 0}, "review": ["side"],
     "set": {"IKLeg_R": {"translateZ": 30}, "IKLeg_L": {"translateZ": -30},
             "FKShoulder_R": {"rotateZ": -35}, "FKShoulder_L": {"rotateZ": 35}}},
)

TRS = ["translateX", "translateY", "translateZ", "rotateX", "rotateY", "rotateZ"]


def _hand_frame(H: float, side: str = "_R") -> Optional[List[float]]:
    """Bounding box of the right hand's deform joints, for a close-up: a fist
    is a 10 cm event on a 180 cm body and reads as nothing in a full shot."""
    joints = [j for j in (cmds.listRelatives("DeformationSystem", allDescendents=True, type="joint") or [])
              if j.endswith(side) and ("Finger" in j or j.startswith(("Wrist", "Cup")))]
    if len(joints) < 3:
        return None
    P = np.array([cmds.xform(j, query=True, worldSpace=True, translation=True) for j in joints])
    pad = 0.035 * H
    return list(P.min(0) - pad) + list(P.max(0) + pad)


def render_pose(pose: Dict[str, Any], mesh: str, evidence_dir: str, H: float) -> List[Dict[str, Any]]:
    """Evidence pictures for one pose. `cameras` may name "hand", which is a
    framed persp close-up of the right hand rather than an orthographic view
    (a fist is a 10 cm event on a 180 cm body). Shared by pose_gallery and
    rig_profile so the bar and the run are shot the same way."""
    out = []
    for cam in pose.get("cameras") or pose.get("review", ["front"]):
        path = os.path.join(evidence_dir, "pose_{}_{}.png".format(pose["name"], cam))
        if cam == "hand":
            frame = _hand_frame(H)
            if frame is None:
                continue
            out.append(ac.render_evidence(path, mesh, "persp", None, transparent_mesh=False, frame=frame))
        else:
            out.append(ac.render_evidence(path, mesh, cam, None, transparent_mesh=False))
    return out


def _posed_attrs() -> Dict[str, List[str]]:
    """Extra (non-TRS) attributes any pose writes, per control. A pose that
    drives a custom attribute (the fingers' curl) must have it snapshotted
    too, or the restore leaves the rig posed and the next pose measures from
    a fist."""
    extra: Dict[str, List[str]] = {}
    for pose in POSES:
        for ctrl, attrs in pose["set"].items():
            for a in attrs:
                if a not in TRS:
                    extra.setdefault(ctrl, [])
                    if a not in extra[ctrl]:
                        extra[ctrl].append(a)
    return extra


def _snapshot(controls: Sequence[str]) -> Dict[str, Dict[str, float]]:
    extra = _posed_attrs()
    snap = {}
    for c in controls:
        vals = {}
        for a in TRS + ["FKIKBlend"] + extra.get(c, []):
            plug = "{}.{}".format(c, a)
            if cmds.attributeQuery(a, node=c, exists=True) and cmds.getAttr(plug, settable=True):
                vals[a] = cmds.getAttr(plug)
        snap[c] = vals
    return snap


def _restore(snap: Dict[str, Dict[str, float]]) -> int:
    n = 0
    for c, vals in snap.items():
        for a, v in vals.items():
            plug = "{}.{}".format(c, a)
            if abs(cmds.getAttr(plug) - v) > 1e-9:
                cmds.setAttr(plug, v)
                n += 1
    return n


# The pose deltas above were tuned on the reference body (185.5 cm): shoulder->wrist
# 56.6 cm, hip->ankle 62.4 cm. Translations of IK/root controls scale with the
# character's own limb lengths, or a chibi's +90 cm "hands above the head"
# drives the hands through its head and stretches the IK (arms_up read +19 %
# volume, 55 % of vertices moved). Rotations are angles and need no scaling.
REF_ARM, REF_LEG = 56.6, 62.4


def _limb_scales() -> Dict[str, float]:
    """Character limb lengths relative to the reference character."""
    def dist(a: str, b: str) -> Optional[float]:
        if cmds.objExists(a) and cmds.objExists(b):
            pa = np.array(cmds.xform(a, query=True, worldSpace=True, translation=True))
            pb = np.array(cmds.xform(b, query=True, worldSpace=True, translation=True))
            return float(np.linalg.norm(pa - pb))
        return None

    arm = dist("Shoulder_R", "Wrist_R") or dist("Shoulder", "Wrist") or REF_ARM
    leg = dist("Hip_R", "Ankle_R") or dist("Hip", "Ankle") or REF_LEG
    return {"arm": arm / REF_ARM, "leg": leg / REF_LEG, "arm_cm": round(arm, 1), "leg_cm": round(leg, 1)}


def mesh_frame(mesh: str) -> Dict[str, float]:
    """The character's own frame: floor, height and the body's mid X/Z, so a
    pose can name an absolute place ("above the head") instead of a delta."""
    bb = cmds.exactWorldBoundingBox(mesh)
    return {"H": bb[4] - bb[1], "floor": bb[1], "cx": 0.5 * (bb[0] + bb[3]), "cz": 0.5 * (bb[2] + bb[5])}


def _delta_scale(ctrl: str, attr: str, scales: Dict[str, float]) -> float:
    if not attr.startswith("translate"):
        return 1.0
    if ctrl.startswith("IKArm"):
        return scales["arm"]
    if ctrl.startswith(("IKLeg", "RootX")):
        return scales["leg"]
    return 1.0


def _apply(pose: Dict[str, Any], snap: Dict[str, Dict[str, float]],
           scales: Optional[Dict[str, float]] = None,
           frame: Optional[Dict[str, float]] = None) -> List[str]:
    scales = scales or {"arm": 1.0, "leg": 1.0}
    missing = []
    for ctrl, at in (pose.get("reach") or {}).items():
        # Place an IK control at a fraction of its own limb's reach, in a
        # direction from the limb's root. Measured in the build pose, so it
        # is the same question for a chibi and for a giant.
        if not all(cmds.objExists(n) for n in (ctrl, at["from"], at["to"])):
            missing.append(ctrl)
            continue
        root = np.array(cmds.xform(at["from"], query=True, worldSpace=True, translation=True))
        tip = np.array(cmds.xform(at["to"], query=True, worldSpace=True, translation=True))
        length = float(np.linalg.norm(tip - root))
        d = np.array(at["dir"], dtype=float)
        n = float(np.linalg.norm(d))
        if length < 1e-6 or n < 1e-9:
            missing.append(ctrl + " (degenerate limb)")
            continue
        target = root + (at["frac"] * length / n) * d
        cmds.xform(ctrl, worldSpace=True, translation=[float(v) for v in target])
    for node, blend in pose.get("blend", {}).items():
        if cmds.objExists(node):
            cmds.setAttr(node + ".FKIKBlend", blend)
        else:
            missing.append(node)
    optional = bool(pose.get("optional_attrs"))
    for ctrl, attrs in pose["set"].items():
        if not cmds.objExists(ctrl):
            missing.append(ctrl)
            continue
        for a, d in attrs.items():
            if not cmds.attributeQuery(a, node=ctrl, exists=True):
                # A curl for a finger this mesh does not have: not a failure,
                # the rig matches the mesh.
                if not optional:
                    missing.append("{}.{}".format(ctrl, a))
                continue
            base = snap.get(ctrl, {}).get(a, cmds.getAttr("{}.{}".format(ctrl, a)))
            cmds.setAttr("{}.{}".format(ctrl, a), base + d * _delta_scale(ctrl, a, scales))
    return missing


def main(mesh: str = "Mesh", evidence_dir: Optional[str] = None, poses: Sequence[str] = (),
         render: bool = True, compact: bool = True, dump_dir: Optional[str] = None,
         **_kw) -> Dict[str, Any]:
    if not cmds.objExists("ControlSet"):
        raise RuntimeError("no ControlSet: build the rig first")
    if not cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster"):
        raise RuntimeError("{} has no skinCluster: bind first, a gallery of an unskinned mesh proves nothing".format(mesh))
    # Never measure from a hand-posed scene: rest is the build pose, always.
    out_pose = ac.go_to_build_pose()
    controls = cmds.sets("ControlSet", query=True) or []
    controls += [n for n in ("FKIKArm_R", "FKIKArm_L", "FKIKLeg_R", "FKIKLeg_L") if cmds.objExists(n)]
    snap = _snapshot(controls)
    scales = _limb_scales()
    frame = mesh_frame(mesh)
    rest = ac.world_points(mesh)
    # Orient the windings once and cap every boundary loop (on the reference body: the
    # foot soles); both keep the volume well defined so it only moves when the
    # skin actually pinches, bulges or explodes.
    raw_tris = ac.body_triangles(mesh)
    tris = mg.orient_consistently(raw_tris)
    flipped = int((tris != raw_tris).any(axis=1).sum())
    loops = mg.boundary_loops(tris)
    if dump_dir:  # geometry dump for offline analysis of a pose (numpy, no Maya)
        os.makedirs(dump_dir, exist_ok=True)
        np.save(os.path.join(dump_dir, "rest.npy"), rest)
        np.save(os.path.join(dump_dir, "tris.npy"), tris)
        np.save(os.path.join(dump_dir, "raw_tris.npy"), raw_tris)
    vol_rest = mg.closed_volume(rest, tris, loops)
    bbox = cmds.exactWorldBoundingBox(mesh)
    H, floor = bbox[4] - bbox[1], bbox[1]
    wanted = set(poses) if poses else None

    out: Dict[str, Any] = {"mesh": mesh, "build_pose_reset": out_pose, "controls_snapshotted": len(snap),
                           "volume_rest_cm3": round(vol_rest, 1), "body_triangles": int(len(tris)),
                           "boundary_loops": len(loops), "triangles_flipped": flipped,
                           "limb_scales": scales, "poses": []}
    checks: List[Dict[str, Any]] = []
    renders: List[Dict[str, Any]] = []
    for pose in POSES:
        if wanted and pose["name"] not in wanted:
            continue
        res: Dict[str, Any] = {"name": pose["name"], "reads_as": pose["reads_as"], "review_views": pose.get("review", ["front"])}
        try:
            res["missing_controls"] = _apply(pose, snap, scales, frame)
            posed = ac.world_points(mesh)
            disp = np.linalg.norm(posed - rest, axis=1)
            res["max_disp_frac_H"] = round(float(disp.max()) / H, 3)
            res["mean_disp_frac_H"] = round(float(disp.mean()) / H, 3)
            res["moved_vertices_pct"] = round(float((disp > 0.01 * H).mean()) * 100, 1)
            res["lowest_y_delta"] = round(float(posed[:, 1].min() - floor), 2)
            res["exploded"] = bool(disp.max() > 1.5 * H)
            if dump_dir:
                np.save(os.path.join(dump_dir, "posed_{}.npy".format(pose["name"])), posed)
            vol = mg.closed_volume(posed, tris, loops)
            res["volume_ratio"] = round(vol / vol_rest, 4) if vol_rest else None
            res["volume_ok"] = res["volume_ratio"] is not None and abs(res["volume_ratio"] - 1.0) < VOLUME_TOL
            if render and evidence_dir:
                for r in render_pose(dict(pose, cameras=pose.get("cameras") or ("front", "side")),
                                     mesh, evidence_dir, H):
                    renders.append(r)
                    res.setdefault("renders", []).append(r["path"] if r.get("ok") else r)
        finally:
            res["restored_attrs"] = _restore(snap)
        back = ac.world_points(mesh)
        res["reset_residual"] = round(float(np.linalg.norm(back - rest, axis=1).max()), 4)
        ok = (not res["missing_controls"] and res["moved_vertices_pct"] > pose.get("min_moved_pct", 5)
              and not res["exploded"]
              and res["reset_residual"] < 1e-2 and res.get("volume_ok", True))
        checks.append({"check": "pose_" + pose["name"], "ok": ok,
                       "detail": {k: v for k, v in res.items() if k != "renders"}})
        out["poses"].append(res)

    out["checks"] = checks
    out["passed"] = all(c["ok"] for c in checks)
    out["failed"] = [c for c in checks if not c["ok"]]
    out["renders"] = renders
    out["expectation"] = EXPECTATION
    if evidence_dir:
        ac.Evidence(evidence_dir).record("pose_gallery", out["passed"], out,
                                         images=[r["path"] for r in renders if r.get("ok")],
                                         expectation=EXPECTATION)
    if compact:
        return mcp_result.slim(out, "pose_gallery", evidence_dir, metrics={"poses": len(out["poses"])})
    return out
