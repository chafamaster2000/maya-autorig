"""Evidence that the FitSkeleton is placed sanely, before anything is built.

Checks (all numeric, all recorded):
  * joint set: the body joints, plus exactly the finger chains the hand
    analysis placed (fingers are checked inside their own shell)
  * AS side convention: side joints on -X, centre joints at x = 0 (the build
    errors on a left-side FitSkeleton and on off-centre spine joints)
  * every joint inside the body shell (closest-point normal test)
  * segment lengths within loose anthropometric bands
  * parent above child where anatomy demands it (knee below hip, etc.)
Plus two see-through renders (front, side) with red dots at the joints, for a
reviewer to confirm what the numbers cannot: that the dots sit where the
limbs bend.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import maya.cmds as cmds

import autorig_common as ac
import marker_geom as mg

HAND_EXPECTATION = (
    "Close-up of the right hand in X-ray: one FitSkeleton chain of four joints runs down each modelled "
    "finger from knuckle to fingertip, the thumb chain leaves the wrist side of the palm, and the Wrist "
    "joint sits where the forearm meets the palm. A mitten hand shows a single chain down its middle."
)
EXPECTATION = (
    "Maya viewport in X-ray: the mesh see-through, the FitSkeleton joints drawn as bones "
    "with AdvancedSkeleton's labels, red dots on the markers. Joints must lie inside "
    "the body: one chain down the spine to the head, one arm chain shoulder->elbow->"
    "wrist on the character's RIGHT arm only (viewer's left in the front view), one "
    "leg chain hip->knee->ankle->toes on the right leg only. Nothing floating outside "
    "the silhouette; elbow and knee at the visible bends."
)

CENTER = {"Root", "Spine1", "Spine2", "Chest", "Neck", "Head", "HeadEnd", "Jaw", "JawEnd"}
SIDE = {"Scapula", "Shoulder", "Elbow", "Wrist", "WristEnd", "Hip", "Knee", "Ankle", "Heel", "Toes", "ToesEnd", "Eye", "EyeEnd",
        "FootSideInner", "FootSideOuter"}
ORDER = [("Hip", "Knee"), ("Knee", "Ankle"), ("Shoulder", "Elbow"), ("Elbow", "Wrist"),
         ("Neck", "Chest"), ("Chest", "Root"), ("Head", "Neck")]  # (upper, lower) by y
# Joints that legitimately sit on or outside the skin surface.
SURFACE_OK = {"HeadEnd", "ToesEnd", "WristEnd", "Heel", "Eye", "EyeEnd", "JawEnd", "Toes", "FootSideInner", "FootSideOuter"}


def _is_hand_joint(name: str) -> bool:
    return "Finger" in name or name == "Cup"


def main(mesh: str = "Mesh", hand: Optional[Dict[str, Any]] = None, evidence_dir: Optional[str] = None,
         render: bool = True, fingers: Any = None, **_kw) -> Dict[str, Any]:
    """`hand` is fit_from_markers' hand report (joints placed + inside flags);
    None means no finger chains are expected (legacy `fingers` is ignored)."""
    if not cmds.objExists("FitSkeleton"):
        raise RuntimeError("no FitSkeleton in scene")
    P = ac.fit_positions()
    checks: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {"mesh": mesh, "joint_count": len(P), "positions": P}
    bbox = cmds.exactWorldBoundingBox(mesh)
    H = bbox[4] - bbox[1]

    def check(name: str, ok: bool, detail: Any) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    expected = set(mg.BODY_FIT_JOINTS) - mg.OPTIONAL_FIT_JOINTS   # template decides Spine2 / foot sides
    have = set(P)
    finger = {j for j in have if _is_hand_joint(j)}
    want_fingers = set(hand.get("joints", [])) if hand else set()
    if want_fingers:
        expected.discard("WristEnd")     # the wrist has real children now
    check("joint_set", expected <= have and finger == want_fingers,
          {"missing": sorted(expected - have), "unexpected_fingers": sorted(finger - want_fingers),
           "missing_fingers": sorted(want_fingers - finger)})

    bad_side = {j: P[j][0] for j in SIDE if j in P and P[j][0] > -1e-3}
    bad_center = {j: P[j][0] for j in CENTER if j in P and abs(P[j][0]) > 1e-3}
    check("side_joints_on_negative_x", not bad_side, bad_side)
    check("center_joints_on_x0", not bad_center, bad_center)

    shell = ac.body_shell_copy(mesh)
    try:
        inside = ac.inside_test(shell, {j: P[j] for j in P if not _is_hand_joint(j)})
    finally:
        cmds.delete(shell)
    # Finger joints were tested against the hand's own shell (a hand can be
    # a separate, small shell the body-shell policy leaves out).
    for j, ok in (hand or {}).get("inside", {}).items():
        inside[j] = {"inside": bool(ok), "how": "hand shell winding"}
    outside = {j: r for j, r in inside.items()
               if not r["inside"] and j not in SURFACE_OK and not j.endswith("Finger4")}
    check("joints_inside_body", not outside, outside)
    out["inside"] = inside

    if expected <= have:
        bl = mg.bone_length_report({k: P[k] for k in mg.BODY_FIT_JOINTS if k in P}, H)
        check("segment_lengths", all(v["ok"] for v in bl.values()),
              {k: v for k, v in bl.items() if not v["ok"]})
        out["bone_lengths"] = bl
        bad_order = [(u, l) for u, l in ORDER if P[u][1] <= P[l][1]]
        check("vertical_order", not bad_order, bad_order)
        # IK bend direction: knee in front of hip->ankle, elbow behind shoulder->wrist
        import numpy as _np
        k_off = mg._offset_from_line(_np.array(P["Knee"]), _np.array(P["Hip"]), _np.array(P["Ankle"]))
        e_off = mg._offset_from_line(_np.array(P["Elbow"]), _np.array(P["Shoulder"]), _np.array(P["Wrist"]))
        check("knee_bends_forward", k_off > 0.005 * H, {"knee_z_offset_from_hip_ankle_line": round(k_off, 2)})
        check("elbow_bends_backward", e_off < -0.005 * H, {"elbow_z_offset_from_shoulder_wrist_line": round(e_off, 2)})
        in_bbox = all(bbox[1] - 0.02 * H <= P[j][1] <= bbox[4] + 0.02 * H for j in P)
        check("within_height", in_bbox, {})

    out["checks"] = checks
    out["passed"] = all(c["ok"] for c in checks)
    out["failed"] = [c for c in checks if not c["ok"]]

    if render and evidence_dir:
        pts = {j: P[j] for j in P}
        out["renders"] = [
            ac.render_evidence(os.path.join(evidence_dir, "fit_front.png"), mesh, "front", pts),
            ac.render_evidence(os.path.join(evidence_dir, "fit_side.png"), mesh, "side", pts),
        ]
        hand_joints = [j for j in P if _is_hand_joint(j) or j == "Wrist"]
        if len(hand_joints) > 1:
            # Close-up of the right hand: fingers are 3 cm lobes, invisible
            # in a full-body frame.
            import numpy as _np
            hp = _np.array([P[j] for j in hand_joints])
            pad = 0.04 * H
            frame = list(hp.min(0) - pad) + list(hp.max(0) + pad)
            out["renders"].append(ac.render_evidence(os.path.join(evidence_dir, "fit_hand.png"), mesh, "persp",
                                                     pts, point_radius=0.004 * H, frame=frame))
        out["expectation"] = EXPECTATION
    return out
