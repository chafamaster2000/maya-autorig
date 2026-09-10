"""Measure an AdvancedSkeleton rig + skin the same way whatever built it.

Runs on the open scene: the artist's yardstick rig or the auto-rig's output,
and writes one JSON profile so `rig_compare` can hold the second against the
first. Everything is a number a script can compare; the pictures (pose
renders) are only for the blind A/B critic.

  skeleton    deform joints sorted by rig_taxonomy (core / optional / extra),
              anatomy features (fingers, twists, scapula, heel...), controls
  skin        full weight matrix via OpenMaya: influences per vertex, weight
              sums, per-joint coverage, locality of the dominant weight,
              left/right symmetry, smoothness across edges
  deformation the pose_gallery poses + FK bends (elbow, knee 60 deg) + a
              forearm twist: enclosed volume ratio, edge strain, bleed outside
              the bent limb, forearm radius under twist, reset residual
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

import autorig_common as ac
import marker_geom as mg
import mcp_result
import pose_gallery as pg
import rig_taxonomy as tx

BEND_TESTS = (
    {"control": "FKElbow_R", "angle": 60.0, "region": "arm_R", "fkik": "FKIKArm_R"},
    {"control": "FKKnee_R", "angle": 60.0, "region": "leg_R", "fkik": "FKIKLeg_R"},
)
TWIST_TEST = {"control": "FKWrist_R", "attr": "rotateX", "angle": 90.0, "fkik": "FKIKArm_R",
              "segment": ("Elbow_R", "Wrist_R")}


# --------------------------------------------------------------------------- #
# skin data
# --------------------------------------------------------------------------- #
def skinned_meshes() -> List[Tuple[str, str, int]]:
    out = []
    for sc in cmds.ls(type="skinCluster") or []:
        geo = cmds.skinCluster(sc, query=True, geometry=True) or []
        for g in geo:
            xf = cmds.listRelatives(g, parent=True, fullPath=True)
            if xf and cmds.nodeType(g) == "mesh":
                out.append((xf[0], sc, cmds.polyEvaluate(xf[0], vertex=True)))
    return sorted(out, key=lambda t: -t[2])


def weight_matrix(mesh: str, cluster: str) -> Tuple[np.ndarray, List[str]]:
    shape = cmds.listRelatives(mesh, shapes=True, noIntermediate=True, fullPath=True)[0]
    sel = om.MSelectionList()
    sel.add(shape)
    dag = sel.getDagPath(0)
    sel2 = om.MSelectionList()
    sel2.add(cluster)
    fn = oma.MFnSkinCluster(sel2.getDependNode(0))
    nv = cmds.polyEvaluate(mesh, vertex=True)
    fnc = om.MFnSingleIndexedComponent()
    comp = fnc.create(om.MFn.kMeshVertComponent)
    fnc.setCompleteData(nv)
    w, n = fn.getWeights(dag, comp)
    infl = [p.partialPathName().split("|")[-1] for p in fn.influenceObjects()]
    return np.array(w, dtype=float).reshape(nv, int(n)), infl


def mesh_edges(mesh: str) -> np.ndarray:
    _c, verts = ac.fn_mesh(mesh).getTriangles()
    T = np.array(list(verts), dtype=int).reshape(-1, 3)
    E = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    E.sort(axis=1)
    return np.unique(E, axis=0)


def mirror_map(V: np.ndarray, H: float, chunk: int = 512) -> Tuple[np.ndarray, np.ndarray]:
    """For every vertex: index of the nearest vertex to its X-mirror, and that distance."""
    n = len(V)
    F = V * np.array([-1.0, 1.0, 1.0])
    idx = np.zeros(n, dtype=int)
    dist = np.zeros(n)
    for s in range(0, n, chunk):
        Q = F[s:s + chunk]
        d = ((Q[:, None, :] - V[None, :, :]) ** 2).sum(axis=2)
        k = d.argmin(axis=1)
        idx[s:s + chunk] = k
        dist[s:s + chunk] = np.sqrt(d[np.arange(len(Q)), k])
    return idx, dist


def _joint_pos(j: str) -> np.ndarray:
    return np.array(cmds.xform(j, query=True, worldSpace=True, translation=True))


def _seg_dist(Q: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    den = float(ab @ ab)
    if den < 1e-9:
        return np.linalg.norm(Q - a, axis=1)
    t = np.clip(((Q - a) @ ab) / den, 0.0, 1.0)
    return np.linalg.norm(Q - (a + t[:, None] * ab), axis=1)


def skin_profile(mesh: str, cluster: str, H: float) -> Dict[str, Any]:
    W, infl = weight_matrix(mesh, cluster)
    V = ac.world_points(mesh)
    nv, ni = W.shape
    kinds = [tx.classify(i)["kind"] for i in infl]
    nz = W > 1e-4
    per_vertex = nz.sum(axis=1)
    hist = {int(k): int(v) for k, v in zip(*np.unique(per_vertex, return_counts=True))}
    sums = W.sum(axis=1)
    dom = W.argmax(axis=1)
    cover = []
    for k, name in enumerate(infl):
        cover.append({"joint": name, "kind": kinds[k], "dominant": int((dom == k).sum()),
                      "over_05": int((W[:, k] > 0.05).sum()), "weight_sum": round(float(W[:, k].sum()), 2)})
    unused_core = [c["joint"] for c in cover if c["kind"] in ("core", "optional") and c["weight_sum"] < 0.5]
    # locality: distance from a vertex to the bone(s) of its dominant influence
    segs: Dict[int, List[Tuple[np.ndarray, np.ndarray]]] = {}
    for k, name in enumerate(infl):
        if not cmds.objExists(name):
            continue
        p = _joint_pos(name)
        kids = cmds.listRelatives(name, children=True, type="joint") or []
        ends = [_joint_pos(c) for c in kids] or [p]
        segs[k] = [(p, e) for e in ends]
    loc = np.full(nv, np.nan)
    for k in np.unique(dom):
        if k not in segs:
            continue
        rows = np.where(dom == k)[0]
        loc[rows] = np.min(np.stack([_seg_dist(V[rows], a, b) for a, b in segs[k]]), axis=0)
    locf = loc[~np.isnan(loc)] / H
    # symmetry: right-side vertices against their mirrored partner, weights
    # re-indexed through the _R <-> _L influence pairing
    midx, mdist = mirror_map(V, H)
    partner = np.full(ni, -1, dtype=int)
    by_name = {n: k for k, n in enumerate(infl)}
    for k, name in enumerate(infl):
        if name.endswith("_R"):
            partner[k] = by_name.get(name[:-2] + "_L", -1)
        elif name.endswith("_L"):
            partner[k] = by_name.get(name[:-2] + "_R", -1)
        else:
            partner[k] = k
    right = np.where((V[:, 0] < -0.01 * H) & (mdist < 0.02 * H))[0]
    sym = np.array([])
    if len(right):
        Wm = np.zeros_like(W[right])
        for k in range(ni):
            if partner[k] >= 0:
                Wm[:, k] = W[midx[right], partner[k]]
        sym = np.abs(W[right] - Wm).sum(axis=1)
    # smoothness: L1 weight change across mesh edges
    E = mesh_edges(mesh)
    smooth = np.abs(W[E[:, 0]] - W[E[:, 1]]).sum(axis=1)
    return {
        "mesh": mesh.split("|")[-1], "cluster": cluster, "verts": int(nv), "influences": int(ni),
        "influence_kinds": {k: kinds.count(k) for k in set(kinds)},
        "skinning_method": int(cmds.getAttr(cluster + ".skinningMethod")),
        "max_influences": int(cmds.getAttr(cluster + ".maxInfluences")),
        "influences_per_vertex": {"hist": hist, "mean": round(float(per_vertex.mean()), 2),
                                  "max": int(per_vertex.max())},
        "weight_sum": {"min": round(float(sums.min()), 4), "max": round(float(sums.max()), 4)},
        "coverage": cover, "unused_core_joints": unused_core,
        "locality": {"median_frac_H": round(float(np.median(locf)), 4), "p95_frac_H": round(float(np.percentile(locf, 95)), 4),
                     "over_030_pct": round(float((locf > 0.3).mean() * 100), 2)} if len(locf) else None,
        "symmetry": {"pairs": int(len(right)), "l1_mean": round(float(sym.mean()), 4) if len(sym) else None,
                     "l1_p95": round(float(np.percentile(sym, 95)), 4) if len(sym) else None,
                     "unpaired_influences": [infl[k] for k in range(ni) if partner[k] < 0]},
        "smoothness": {"edges": int(len(E)), "l1_mean": round(float(smooth.mean()), 4),
                       "l1_p95": round(float(np.percentile(smooth, 95)), 4), "l1_max": round(float(smooth.max()), 4)},
    }


# --------------------------------------------------------------------------- #
# deformation battery
# --------------------------------------------------------------------------- #
def _strain(rest: np.ndarray, posed: np.ndarray, E: np.ndarray) -> Dict[str, float]:
    lr = np.linalg.norm(rest[E[:, 0]] - rest[E[:, 1]], axis=1)
    lp = np.linalg.norm(posed[E[:, 0]] - posed[E[:, 1]], axis=1)
    ok = lr > 1e-6
    r = lp[ok] / lr[ok]
    return {"strain_p99": round(float(np.percentile(r, 99)), 3), "strain_max": round(float(r.max()), 3),
            "compress_p01": round(float(np.percentile(r, 1)), 3), "compress_min": round(float(r.min()), 3)}


def _region_masks(V: np.ndarray, H: float) -> Dict[str, np.ndarray]:
    """Same coarse regions as verify_skin, but from the deform joints (both
    rigs have Elbow_R/Knee_R/Neck_M/Shoulder_R whatever placed them)."""
    def y_of(j, default):
        return _joint_pos(j)[1] if cmds.objExists(j) else default
    x, y = V[:, 0], V[:, 1]
    y0 = float(V[:, 1].min())
    elbow_y, knee_y = y_of("Elbow_R", y0 + 0.6 * H), y_of("Knee_R", y0 + 0.28 * H)
    neck_y = y_of("Neck_M", y0 + 0.8 * H)
    shoulder_x = _joint_pos("Shoulder_R")[0] if cmds.objExists("Shoulder_R") else -0.1 * H
    arm_R = (x < shoulder_x - 0.05 * H) & (y < elbow_y + 0.05 * H)
    leg_R = (x < 0) & (x > -0.25 * H) & (y < knee_y + 0.02 * H)
    rest_arm = ~((x < shoulder_x + 0.03 * H) & (y < elbow_y + 0.12 * H) & (y > elbow_y - 0.6 * H) & (x < -0.1 * H))
    rest_leg = ~((x < 0.02 * H) & (x > -0.3 * H) & (y < knee_y + 0.12 * H))
    return {"arm_R": arm_R, "leg_R": leg_R, "rest_arm_R": rest_arm, "rest_leg_R": rest_leg,
            "head": y > neck_y + 0.03 * H}


def _set_blend(node: Optional[str], value: float) -> Optional[float]:
    if node and cmds.objExists(node) and cmds.attributeQuery("FKIKBlend", node=node, exists=True):
        was = cmds.getAttr(node + ".FKIKBlend")
        cmds.setAttr(node + ".FKIKBlend", value)
        return was
    return None


def bend_test(mesh: str, rest: np.ndarray, E: np.ndarray, masks: Dict[str, np.ndarray], H: float,
              t: Dict[str, Any], tris, loops, vol_rest: float, evidence_dir: Optional[str], render: bool) -> Dict[str, Any]:
    ctrl, angle, region = t["control"], float(t["angle"]), t["region"]
    res: Dict[str, Any] = {"control": ctrl, "angle": angle, "region": region}
    if not cmds.objExists(ctrl):
        res["error"] = "control not found"
        return res
    blend_was = _set_blend(t.get("fkik"), 0)
    rest_rot = cmds.getAttr(ctrl + ".rotate")[0]
    best = None
    for axis in ("Y", "Z", "X"):
        try:
            cmds.setAttr(ctrl + ".rotate", *rest_rot, type="double3")
            cmds.setAttr("{}.rotate{}".format(ctrl, axis), rest_rot["XYZ".index(axis)] + angle)
        except RuntimeError:
            continue
        posed = ac.world_points(mesh)
        disp = np.linalg.norm(posed - rest, axis=1)
        score = float(disp[masks[region]].mean()) if masks[region].any() else 0.0
        if best is None or score > best[1]:
            best = (axis, score, posed, disp)
    if best is None:
        cmds.setAttr(ctrl + ".rotate", *rest_rot, type="double3")
        if blend_was is not None:
            _set_blend(t.get("fkik"), blend_was)
        res["error"] = "no rotatable axis"
        return res
    axis, _s, posed, disp = best
    cmds.setAttr(ctrl + ".rotate", *rest_rot, type="double3")
    cmds.setAttr("{}.rotate{}".format(ctrl, axis), rest_rot["XYZ".index(axis)] + angle)
    rm = masks["rest_" + region]
    res.update({"axis": axis,
                "limb_max_disp_frac_H": round(float(disp[masks[region]].max()) / H, 4) if masks[region].any() else 0.0,
                "rest_max_disp_frac_H": round(float(disp[rm].max()) / H, 4) if rm.any() else 0.0,
                "rest_moved_pct": round(float((disp[rm] > 0.01 * H).mean() * 100), 2) if rm.any() else 0.0,
                "head_max_disp_frac_H": round(float(disp[masks["head"]].max()) / H, 4) if masks["head"].any() else 0.0,
                "volume_ratio": round(mg.closed_volume(posed, tris, loops) / vol_rest, 4) if vol_rest else None})
    res.update(_strain(rest, posed, E))
    if render and evidence_dir:
        for cam in ("front", "side"):
            r = ac.render_evidence(os.path.join(evidence_dir, "bend_{}_{}.png".format(ctrl, cam)), mesh, cam, None,
                                   transparent_mesh=False)
            res.setdefault("renders", []).append(r["path"] if r.get("ok") else r)
    cmds.setAttr(ctrl + ".rotate", *rest_rot, type="double3")
    if blend_was is not None:
        _set_blend(t.get("fkik"), blend_was)
    back = ac.world_points(mesh)
    res["reset_residual"] = round(float(np.linalg.norm(back - rest, axis=1).max()), 4)
    return res


def twist_test(mesh: str, rest: np.ndarray, E: np.ndarray, H: float, tris, loops, vol_rest: float,
               evidence_dir: Optional[str], render: bool) -> Dict[str, Any]:
    """Twist the wrist 90 degrees: without twist joints the forearm pinches
    (candy wrapper). Measured as the smallest ratio posed/rest of the mean
    radial distance of the forearm vertices in slabs along the bone."""
    t = TWIST_TEST
    ctrl = t["control"]
    res: Dict[str, Any] = {"control": ctrl, "attr": t["attr"], "angle": t["angle"]}
    a_j, b_j = t["segment"]
    if not (cmds.objExists(ctrl) and cmds.objExists(a_j) and cmds.objExists(b_j)):
        res["error"] = "control or segment joints missing"
        return res
    a, b = _joint_pos(a_j), _joint_pos(b_j)
    ab = b - a
    L = float(np.linalg.norm(ab))
    u = ab / L
    rel = rest - a
    tt = rel @ u
    radial = np.linalg.norm(rel - tt[:, None] * u, axis=1)
    forearm = (tt > 0.15 * L) & (tt < 0.85 * L) & (radial < 0.12 * H)
    blend_was = _set_blend(t.get("fkik"), 0)
    plug = "{}.{}".format(ctrl, t["attr"])
    was = cmds.getAttr(plug)
    try:
        cmds.setAttr(plug, was + t["angle"])
        posed = ac.world_points(mesh)
        prel = posed - a
        ptt = prel @ u
        pradial = np.linalg.norm(prel - ptt[:, None] * u, axis=1)
        ratios = []
        for lo in np.linspace(0.2, 0.7, 6):
            slab = forearm & (tt > lo * L) & (tt < (lo + 0.1) * L)
            if slab.sum() >= 4:
                ratios.append(float(pradial[slab].mean() / max(radial[slab].mean(), 1e-6)))
        res.update({"forearm_vertices": int(forearm.sum()), "radius_ratio_min": round(min(ratios), 4) if ratios else None,
                    "radius_ratio_slabs": [round(r, 3) for r in ratios],
                    "volume_ratio": round(mg.closed_volume(posed, tris, loops) / vol_rest, 4) if vol_rest else None})
        res.update(_strain(rest, posed, E))
        if render and evidence_dir:
            r = ac.render_evidence(os.path.join(evidence_dir, "twist_{}_front.png".format(ctrl)), mesh, "front", None,
                                   transparent_mesh=False)
            res["renders"] = [r["path"] if r.get("ok") else r]
    finally:
        cmds.setAttr(plug, was)
        if blend_was is not None:
            _set_blend(t.get("fkik"), blend_was)
    back = ac.world_points(mesh)
    res["reset_residual"] = round(float(np.linalg.norm(back - rest, axis=1).max()), 4)
    return res


def gallery(mesh: str, rest: np.ndarray, E: np.ndarray, H: float, floor: float, tris, loops, vol_rest: float,
            evidence_dir: Optional[str], render: bool) -> List[Dict[str, Any]]:
    controls = cmds.sets("ControlSet", query=True) or []
    controls += [n for n in ("FKIKArm_R", "FKIKArm_L", "FKIKLeg_R", "FKIKLeg_L") if cmds.objExists(n)]
    snap = pg._snapshot(controls)
    scales = pg._limb_scales()
    frame = pg.mesh_frame(mesh)
    out = []
    for pose in pg.POSES:
        res: Dict[str, Any] = {"name": pose["name"]}
        try:
            res["missing_controls"] = pg._apply(pose, snap, scales, frame)
            posed = ac.world_points(mesh)
            disp = np.linalg.norm(posed - rest, axis=1)
            res.update({"max_disp_frac_H": round(float(disp.max()) / H, 3),
                        "moved_vertices_pct": round(float((disp > 0.01 * H).mean()) * 100, 1),
                        "lowest_y_delta": round(float(posed[:, 1].min() - floor), 2),
                        "exploded": bool(disp.max() > 1.5 * H),
                        "volume_ratio": round(mg.closed_volume(posed, tris, loops) / vol_rest, 4) if vol_rest else None})
            res.update(_strain(rest, posed, E))
            if render and evidence_dir:
                for r in pg.render_pose(pose, mesh, evidence_dir, H):
                    res.setdefault("renders", []).append(r["path"] if r.get("ok") else r)
        finally:
            res["restored_attrs"] = pg._restore(snap)
        back = ac.world_points(mesh)
        res["reset_residual"] = round(float(np.linalg.norm(back - rest, axis=1).max()), 4)
        out.append(res)
    return out


# --------------------------------------------------------------------------- #
def _controls() -> Dict[str, Any]:
    ctrls = [c for c in (cmds.sets("ControlSet", query=True) or []) if cmds.objExists(c)] if cmds.objExists("ControlSet") else []
    names = [c.split("|")[-1] for c in ctrls]
    def has(n):
        return cmds.objExists(n)
    ik_leg_attrs = [a for a in (cmds.listAttr("IKLeg_R", userDefined=True) or [])] if has("IKLeg_R") else []
    return {
        "count": len(names),
        "fk": sum(1 for n in names if n.startswith("FK") and not n.startswith("FKIK")),
        "ik": sum(1 for n in names if n.startswith("IK")),
        "bendy": sum(1 for n in names if n.startswith("Bend")),
        "fingers": sum(1 for n in names if "Finger" in n),
        "fkik_switches": [n for n in ("FKIKArm_R", "FKIKArm_L", "FKIKLeg_R", "FKIKLeg_L", "FKIKSpine_M") if has(n)],
        "eye_aim": has("AimEye_M"), "cup": has("FKCup_R"), "scapula": has("FKScapula_R"),
        "ik_leg_attrs": ik_leg_attrs, "foot_roll": any(a.lower().startswith(("roll", "heel", "toe")) for a in ik_leg_attrs),
    }


def main(mesh: Optional[str] = None, evidence_dir: Optional[str] = None, label: Optional[str] = None,
         render: bool = True, deformation: bool = True, compact: bool = True, save_as_bar: bool = False,
         **_kw) -> Dict[str, Any]:
    t0 = time.time()
    if not cmds.objExists("DeformationSystem"):
        raise RuntimeError("no DeformationSystem: not an AdvancedSkeleton rig")
    skinned = skinned_meshes()
    if mesh:
        cand = [s for s in skinned if s[0].split("|")[-1] == mesh or s[0] == mesh]
        if not cand:
            raise RuntimeError("{} is not skinned".format(mesh))
        mesh, cluster, _nv = cand[0]
    elif skinned:
        mesh, cluster, _nv = skinned[0]
    else:
        raise RuntimeError("no skinned mesh in the scene")
    scene = cmds.file(query=True, sceneName=True)
    label = label or os.path.splitext(os.path.basename(scene))[0] or "untitled"
    if evidence_dir is None:
        evidence_dir = os.path.join(ac.EVIDENCE_ROOT, "profiles", label)
    os.makedirs(evidence_dir, exist_ok=True)
    pose_reset = ac.go_to_build_pose()

    deform = sorted(m for m in (cmds.sets("DeformSet", query=True) or []) if cmds.nodeType(m) == "joint") \
        if cmds.objExists("DeformSet") else [j for j in cmds.listRelatives("DeformationSystem", allDescendents=True, type="joint") or [] if "End" not in j]
    deform = [d.split("|")[-1] for d in deform]
    fit = [j.split("|")[-1] for j in (cmds.listRelatives("FitSkeleton", allDescendents=True, type="joint") or [])] \
        if cmds.objExists("FitSkeleton") else []
    bbox = cmds.exactWorldBoundingBox(mesh)
    H, floor = bbox[4] - bbox[1], bbox[1]
    core_pos = {}
    for j in deform:
        c = tx.classify(j)
        if c["kind"] in ("core", "optional") and cmds.objExists(j):
            p = _joint_pos(j)
            core_pos[j] = [round(float(p[0]) / H, 4), round(float(p[1] - floor) / H, 4), round(float(p[2]) / H, 4)]

    prof: Dict[str, Any] = {
        "label": label, "scene": scene, "mesh": mesh.split("|")[-1], "height_cm": round(H, 2),
        "build_pose_reset": pose_reset,
        "skeleton": {"deform_joints": deform, "n_deform": len(deform), "kinds": tx.sort_names(deform),
                     "core_bases": tx.core_bases(deform), "features": tx.features(deform),
                     "fit_joints": fit, "fit_kinds": tx.sort_names(fit) if fit else None,
                     "core_positions_frac_H": core_pos,
                     "n_joints_total": len(cmds.ls(type="joint") or []),
                     "ik_handles": len(cmds.ls(type="ikHandle") or [])},
        "controls": _controls(),
        "skinned_meshes": [{"mesh": m.split("|")[-1], "verts": n} for m, _c, n in skinned],
    }
    prof["skin"] = skin_profile(mesh, cluster, H)
    if deformation:
        rest = ac.world_points(mesh)
        E = mesh_edges(mesh)
        tris = mg.orient_consistently(ac.body_triangles(mesh))
        loops = mg.boundary_loops(tris)
        vol_rest = mg.closed_volume(rest, tris, loops)
        masks = _region_masks(rest, H)
        prof["deformation"] = {
            "volume_rest_cm3": round(vol_rest, 1), "body_triangles": int(len(tris)),
            "bends": [bend_test(mesh, rest, E, masks, H, t, tris, loops, vol_rest, evidence_dir, render) for t in BEND_TESTS],
            "twist": twist_test(mesh, rest, E, H, tris, loops, vol_rest, evidence_dir, render),
            "poses": gallery(mesh, rest, E, H, floor, tris, loops, vol_rest, evidence_dir, render),
        }
    prof["seconds"] = round(time.time() - t0, 2)
    prof["evidence_dir"] = evidence_dir
    path = os.path.join(evidence_dir, "rig_profile.json")
    ac.Evidence(evidence_dir).record("rig_profile", True, prof)
    import json
    with open(path, "w") as fh:
        json.dump(prof, fh, indent=1, default=str)
    prof["profile_path"] = path
    if save_as_bar:
        # Promote this rig to a yardstick the skill ships with: rig_compare
        # resolves the label as a bar name from now on.
        import shutil
        bars = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bars")
        os.makedirs(bars, exist_ok=True)
        prof["bar_path"] = os.path.join(bars, "{}_profile.json".format(label))
        shutil.copy(path, prof["bar_path"])
    if compact:
        sk = prof["skin"]
        return {"success": True, "label": label, "mesh": prof["mesh"], "profile_path": path, "seconds": prof["seconds"],
                "bar_path": prof.get("bar_path"),
                "n_deform": len(deform), "kinds": {k: len(v) for k, v in prof["skeleton"]["kinds"].items()},
                "features": prof["skeleton"]["features"], "controls": prof["controls"]["count"],
                "skin": {"max_influences": sk["max_influences"], "mean_infl": sk["influences_per_vertex"]["mean"],
                         "locality_p95": (sk["locality"] or {}).get("p95_frac_H"), "symmetry_l1": sk["symmetry"]["l1_mean"],
                         "smooth_p95": sk["smoothness"]["l1_p95"], "unused_core": sk["unused_core_joints"]}}
    return prof
