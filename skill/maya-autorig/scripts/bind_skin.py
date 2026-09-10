"""Bind a mesh to the AdvancedSkeleton deformation joints, and prove the weights are sane.

Two lessons baked in:

  * Influences come from AS's `DeformSet`, never from every joint under
    DeformationSystem: the *End joints (ToesEnd, WristEnd, JawEnd...) are
    effectors, and with them in the list Maya happily spreads weights across
    the whole body onto them.
  * A skinCluster returning is not a bind succeeding. Geodesic voxel binding
    on a multi-shell OBJ came back in 9 ms with thigh vertices weighted to
    WristEnd. So after binding, weights are checked for locality (the
    dominant influence of a vertex must be near that vertex); if that fails
    the mesh is rebound with closest-in-hierarchy and the fallback reported.

Uses Maya's native skinCluster rather than asSmoothSkin: that proc reads its
settings from the AS UI and is non-deterministic in batch.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import maya.cmds as cmds
import maya.api.OpenMaya as om
from maya.api import OpenMayaAnim as oma

import mcp_result

BIND_METHODS = {"closest_distance": 0, "closest_hierarchy": 1, "heatmap": 2, "geodesic": 3}
SKIN_METHODS = {"linear": 0, "dual_quaternion": 1, "blended": 2}
# Default is linear: bipedGame targets game engines, which evaluate LBS, and
# the pose gallery's volume metric showed dual quaternion inflating the chest
# by 14 % (a 14 cm bulge) at 150 degrees of shoulder abduction -- the known DQ
# bulging artifact -- while LBS stays within -4 % on every pose.
FALLBACK_ORDER = ("geodesic", "closest_hierarchy", "closest_distance")
# Geodesic voxel binding is NOT a skinCluster bindMethod in practice: the
# solver lives in the separate `geomBind` command, applied to an existing
# skinCluster. `skinCluster -bindMethod 3` alone leaves garbage weights (that
# is what put thigh vertices on WristEnd). Voxel grid resolution + validation.
GEODESIC_VOXEL = (256, True)
# Sharper geodesic falloff under Unity's 4 bones per vertex: the gauntlet
# sweep (tools/skin_sweep.py, 2026-09-10) cut the elbow->coat bleed on the
# coated character from 0.048 H to 0.012 H and the reference body's from 0.0066 to 0.0033 with
# no row lost on any case; 1.0 goes further but costs symmetry/smoothness,
# 8 influences did not help the bleed at all (0.040), voxel 512 is 5x slower
# for nothing.
GEODESIC_FALLOFF = 0.5


def deformation_joints(joint_root: str = "DeformationSystem") -> List[str]:
    """AS's own list of skinning joints; falls back to the hierarchy minus effectors."""
    if cmds.objExists("DeformSet"):
        members = [m for m in (cmds.sets("DeformSet", query=True) or []) if cmds.nodeType(m) == "joint"]
        if members:
            return sorted(members)
    if not cmds.objExists(joint_root):
        raise RuntimeError("'{}' not found. Build the rig first.".format(joint_root))
    joints = cmds.listRelatives(joint_root, allDescendents=True, type="joint") or []
    joints = [j for j in joints if "End" not in j]
    if not joints:
        raise RuntimeError("No skinning joints found under '{}'.".format(joint_root))
    return sorted(joints)


def weight_locality(mesh: str, cluster: str, joints: List[str], sample: int = 120,
                    max_frac_of_height: float = 0.3) -> Dict[str, Any]:
    """For sampled vertices: distance from the vertex to the nearest point on
    the bones (joint -> child segments) of its dominant influence, as a
    fraction of height. Garbage weights show up as distances of half a body."""
    import autorig_common as ac  # local import: keeps this module importable without numpy paths set up

    V = ac.world_points(mesh)
    bb = cmds.exactWorldBoundingBox(mesh)
    H = bb[4] - bb[1]
    segs: Dict[str, List[Tuple[np.ndarray, np.ndarray]]] = {}
    for j in joints:
        p = np.array(cmds.xform(j, query=True, worldSpace=True, translation=True))
        kids = cmds.listRelatives(j, children=True, type="joint") or []
        ends = [np.array(cmds.xform(k, query=True, worldSpace=True, translation=True)) for k in kids] or [p]
        segs[j] = [(p, e) for e in ends]

    def seg_dist(q: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
        ab = b - a
        t = 0.0 if ab @ ab < 1e-9 else float(np.clip((q - a) @ ab / (ab @ ab), 0.0, 1.0))
        return float(np.linalg.norm(q - (a + t * ab)))

    n = len(V)
    idx = list(range(0, n, max(1, n // sample)))
    fracs = []
    worst: List[Dict[str, Any]] = []
    for i in idx:
        infl = cmds.skinPercent(cluster, "{}.vtx[{}]".format(mesh, i), query=True, transform=None) or []
        w = cmds.skinPercent(cluster, "{}.vtx[{}]".format(mesh, i), query=True, value=True) or []
        if not infl:
            continue
        top = infl[int(np.argmax(w))]
        d = min(seg_dist(V[i], a, b) for a, b in segs.get(top, [(V[i], V[i])]))
        f = d / H
        fracs.append(f)
        if f > max_frac_of_height:
            worst.append({"vtx": i, "top": top, "dist_frac_H": round(f, 3)})
    fr = np.array(fracs) if fracs else np.array([1.0])
    return {"sampled": len(fracs), "median_frac_H": round(float(np.median(fr)), 3),
            "p95_frac_H": round(float(np.percentile(fr, 95)), 3),
            "over_limit_pct": round(float((fr > max_frac_of_height).mean() * 100), 1),
            "ok": bool((fr > max_frac_of_height).mean() < 0.05), "worst": worst[:6]}


# A vertex out on the hand must not ride on the forearm. The geodesic solver
# gives hand vertices 20-30 % weight on ElbowPart2 (the forearm twist) because
# it is only ~8 cm away; with four influence slots that weight lands unevenly
# on neighbouring vertices, and raising the arm shears the hand (a gloved character's
# arms_up edges collapsed to 0.12 of their rest length, all of them in the
# hand). The rule is anatomy, not a threshold: past the wrist, the arm is
# behind you.
HAND_JOINT = re.compile(r"^(Wrist|Cup|(Thumb|Index|Middle|Ring|Pinky)Finger\d+)_[LR]$")
FOREARM_JOINT = re.compile(r"^(Elbow|ElbowPart\d+|Shoulder|ShoulderPart\d+|Scapula)_[LR]$")
HAND_MIN_OWN = 0.5      # a vertex is "in the hand" when the hand owns half of it


def prune_hand_weights(mesh: str, cluster: str) -> Dict[str, Any]:
    """Move forearm/shoulder weight off hand vertices onto their own wrist.
    Returns what moved. Idempotent; a rig without fingers is untouched."""
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    s2 = om.MSelectionList()
    s2.add(cluster)
    fn = oma.MFnSkinCluster(s2.getDependNode(0))
    infl = fn.influenceObjects()
    names = [p.partialPathName() for p in infl]
    hand = [i for i, n in enumerate(names) if HAND_JOINT.match(n)]
    fore = [i for i, n in enumerate(names) if FOREARM_JOINT.match(n)]
    if not hand or not fore:
        return {"applied": False, "reason": "no hand or no forearm joints"}
    n_v = cmds.polyEvaluate(mesh, vertex=True)
    comp = om.MFnSingleIndexedComponent().create(om.MFn.kMeshVertComponent)
    om.MFnSingleIndexedComponent(comp).setCompleteData(n_v)
    W = np.array(fn.getWeights(dag, comp)[0]).reshape(n_v, len(names))
    wrist_of = {}
    for side in ("L", "R"):
        if "Wrist_" + side in names:
            wrist_of[side] = names.index("Wrist_" + side)
    own = W[:, hand].sum(1)
    moved, touched = 0.0, 0
    for v in np.where(own >= HAND_MIN_OWN)[0]:
        side = "L" if W[v, [i for i in hand if names[i].endswith("_L")]].sum() >= \
            W[v, [i for i in hand if names[i].endswith("_R")]].sum() else "R"
        wi = wrist_of.get(side)
        if wi is None:
            continue
        stolen = float(W[v, fore].sum())
        if stolen <= 1e-4:
            continue
        W[v, fore] = 0.0
        W[v, wi] += stolen
        moved += stolen
        touched += 1
    if touched:
        rows = W.sum(1, keepdims=True)
        rows[rows < 1e-9] = 1.0
        W /= rows
        fn.setWeights(dag, comp, om.MIntArray(list(range(len(names)))),
                      om.MDoubleArray(W.ravel().tolist()), False)
    return {"applied": bool(touched), "hand_vertices": touched,
            "weight_moved_mean": round(moved / touched, 4) if touched else 0.0,
            "hand_joints": len(hand), "forearm_joints": len(fore)}


def _bind(mesh: str, joints: List[str], bind_method: str, skin_method: str,
          max_influences: int, dropoff_rate: float) -> str:
    kw: Dict[str, Any] = dict(toSelectedBones=True, bindMethod=BIND_METHODS[bind_method],
                              skinMethod=SKIN_METHODS[skin_method], maximumInfluences=max_influences,
                              obeyMaxInfluences=True, dropoffRate=dropoff_rate, removeUnusedInfluence=False,
                              normalizeWeights=1)
    if bind_method == "geodesic":
        # bind closest-distance first (cheap, deterministic), then let the
        # voxel solver overwrite the weights
        kw["bindMethod"] = BIND_METHODS["closest_distance"]
        cluster = cmds.skinCluster(joints, mesh, **kw)[0]
        cmds.geomBind(cluster, bindMethod=3, geodesicVoxelParams=GEODESIC_VOXEL,
                      maxInfluences=max_influences, falloff=GEODESIC_FALLOFF)
        return cluster
    return cmds.skinCluster(joints, mesh, **kw)[0]


def main(mesh: str = "", bind_method: str = "geodesic", skin_method: str = "linear",
         max_influences: int = 4, dropoff_rate: float = 4.0, joint_root: str = "DeformationSystem",
         replace_existing: bool = False, fallback: bool = True, evidence_dir: Optional[str] = None,
         compact: bool = True, **_params: Any) -> Dict[str, Any]:
    if not mesh or not cmds.objExists(mesh):
        raise ValueError("mesh {!r} does not exist".format(mesh))
    if bind_method not in BIND_METHODS:
        raise ValueError("bind_method must be one of {}".format(sorted(BIND_METHODS)))
    if skin_method not in SKIN_METHODS:
        raise ValueError("skin_method must be one of {}".format(sorted(SKIN_METHODS)))

    existing = cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")
    if existing:
        if not replace_existing:
            raise RuntimeError("'{}' already has skinCluster '{}'; pass replace_existing=true".format(mesh, existing[0]))
        cmds.delete(existing)

    joints = deformation_joints(joint_root)
    attempts: List[Dict[str, Any]] = []
    order = [bind_method] + [m for m in FALLBACK_ORDER if m != bind_method] if fallback else [bind_method]
    cluster: Optional[str] = None
    locality: Dict[str, Any] = {}
    used = bind_method
    for method in order:
        try:
            cluster = _bind(mesh, joints, method, skin_method, max_influences, dropoff_rate)
        except RuntimeError as exc:
            attempts.append({"method": method, "error": str(exc).strip()})
            for leftover in cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster"):
                cmds.delete(leftover)
            continue
        pruned = prune_hand_weights(mesh, cluster)
        locality = weight_locality(mesh, cluster, joints)
        attempts.append({"method": method, "cluster": cluster, "locality": locality, "hand_prune": pruned})
        used = method
        if locality["ok"]:
            break
        # garbage weights: throw the cluster away and try the next method
        cmds.delete(cluster)
        cluster = None
    if cluster is None:
        raise RuntimeError("no bind method produced local weights: {}".format(attempts))

    out: Dict[str, Any] = {
        "success": True, "passed": bool(locality.get("ok")),
        "message": "Bound '{}' to {} joints as '{}' via {}".format(mesh, len(joints), cluster, used),
        "mesh": mesh, "skin_cluster": cluster, "influence_count": len(joints),
        "bind_method_requested": bind_method, "bind_method_used": used,
        "fell_back": used != bind_method, "skin_method": skin_method, "max_influences": max_influences,
        "locality": locality, "hand_prune": attempts[-1].get("hand_prune") if attempts else None,
        "attempts": attempts, "replaced": bool(existing),
        "excluded_end_joints": sorted(j for j in (cmds.listRelatives(joint_root, allDescendents=True, type="joint") or []) if j not in joints),
    }
    if evidence_dir:
        import autorig_common as ac
        ac.Evidence(evidence_dir).record("bind", out["passed"], out)
    if compact:
        return mcp_result.slim(out, "bind", evidence_dir, metrics={
            "bind_method_used": used, "fell_back": used != bind_method, "influence_count": len(joints),
            "locality_median_frac_H": locality.get("median_frac_H"),
            "locality_over_limit_pct": locality.get("over_limit_pct")})
    return out
