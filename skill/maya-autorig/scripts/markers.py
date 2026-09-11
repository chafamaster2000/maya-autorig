"""Mixamo-style markers as Maya locators: propose, let the user drag, read back.

`propose` also puts the viewport in *marker mode* (`ui=True`, the default):
X-ray on, the body and its props on a reference display layer so a click
lands on a locator and never on the mesh, the camera framed from the front,
the Move tool armed on the first marker, and L/R pairs mirrored both ways
(drag either side, the other follows) while `AutoRigMarkers.mirror` is on.
`harness.run` leaves the mode once the skin stage passed; a failed stage
before that keeps it, so the markers stay at hand for a re-drag and a re-run
(the pose gallery after it is a report, not a gate). `gauntlet` never enters
it: there is no human step in that loop.

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
            replace: bool = True, compact: bool = True, ui: bool = True, **_kw) -> Dict[str, Any]:
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
        exit_mode()                      # kills the mirror jobs and drops the layer first
        cmds.delete(GROUP)
    cmds.group(empty=True, name=GROUP)
    size = 0.02 * H
    created = {}
    # The centre-line markers start on the centre line (the mesh is centred
    # on X by prep_mesh) and stay free to move, as in Mixamo.
    for name in ("chin", "groin"):
        if name in prop["markers"]:
            prop["markers"][name] = [0.0] + [float(v) for v in prop["markers"][name][1:]]
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
    # Last, on purpose: the render above creates and deletes temp nodes (which
    # replaces the selection) and toggles X-ray around its playblast.
    out["ui"] = enter_mode(mesh) if ui else {"mode": False}
    if compact:
        return mcp_result.slim(out, "markers", evidence_dir, metrics={
            "pose_declared": hint, "pose_detected": out["pose_detected"], "pose_agrees": out["pose_agrees"],
            "arm_angle_deg": out["arm_angle_deg"], "height": out["height"],
            "locators": len(created), "notes": out["notes"], "ui": out["ui"]})
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


# --------------------------------------------------------------------------- #
# Marker mode: what the viewport looks like while a person drags the markers
# --------------------------------------------------------------------------- #
LAYER = "autorig_markers"
MIRROR_ATTR = "mirror"
JOBS_ATTR = "autorigJobs"
PREV_XRAY_ATTR = "autorigPrevXray"
PANEL_ATTR = "autorigPanel"
PAIRS = ("wrist", "elbow", "knee", "shoulder", "ankle")
_MIRRORING = False          # re-entrancy guard: a mirrored set fires the twin's job
_SUSPENDED = False          # read()'s write-back must not cross-copy refinements


class _mirror_suspended:
    def __enter__(self):
        global _SUSPENDED
        self._was, _SUSPENDED = _SUSPENDED, True

    def __exit__(self, *_exc):
        global _SUSPENDED
        _SUSPENDED = self._was


def _twin(name: str) -> Optional[str]:
    if name.endswith("_L"):
        return name[:-2] + "_R"
    if name.endswith("_R"):
        return name[:-2] + "_L"
    return None


def _on_marker_moved(name: str) -> None:
    """scriptJob callback: copy this marker to its twin, mirrored in X."""
    global _MIRRORING
    if _MIRRORING or _SUSPENDED:
        return
    twin = _twin(name)
    if not twin or not cmds.objExists(GROUP) or not cmds.objExists(_loc(name)) or not cmds.objExists(_loc(twin)):
        return
    if cmds.attributeQuery(MIRROR_ATTR, node=GROUP, exists=True) and not cmds.getAttr(GROUP + "." + MIRROR_ATTR):
        return
    _MIRRORING = True
    try:
        x, y, z = cmds.xform(_loc(name), query=True, worldSpace=True, translation=True)
        cmds.xform(_loc(twin), worldSpace=True, translation=(-x, y, z))
    finally:
        _MIRRORING = False


def _on_mirror_toggled() -> None:
    """Turning the mirror back on re-syncs L from R: R (-X) is the side the
    fit is built from, so it is the side that wins."""
    if not cmds.objExists(GROUP) or not cmds.getAttr(GROUP + "." + MIRROR_ATTR):
        return
    for base in PAIRS:
        if cmds.objExists(_loc(base + "_R")) and cmds.objExists(_loc(base + "_L")):
            _on_marker_moved(base + "_R")


_CALLBACKS: Dict[str, List[int]] = {}   # group name -> MMessage callback ids (session-bound)


def _arm_mirror() -> List[int]:
    """One API attribute-changed callback per paired locator (both sides, so
    either can be dragged) plus one on the switch. API callbacks, not
    scriptJobs: a scriptJob runs when Maya is next idle, which never came
    between two gateway calls in a batch (the twin stayed put through a
    move, a toggle and an idle flush), and an artist's drag wants the twin
    to move *with* the cursor anyway. Session-bound by nature: they die with
    the scene and are rebuilt by `propose`, which is fine -- the markers are
    spent once the fit has read them."""
    import maya.api.OpenMaya as om

    def node(name: str):
        sel = om.MSelectionList()
        sel.add(name)
        return sel.getDependNode(0)

    ids: List[int] = []
    for base in PAIRS:
        for side in ("_L", "_R"):
            name = base + side
            if not (cmds.objExists(_loc(name)) and cmds.objExists(_loc(_twin(name)))):
                continue

            def on_change(msg, plug, other, data, n=name):
                if not (msg & om.MNodeMessage.kAttributeSet):
                    return
                if plug.partialName(useLongNames=True).split(".")[-1] not in ("translate", "translateX", "translateY", "translateZ"):
                    return
                _on_marker_moved(n)

            ids.append(om.MNodeMessage.addAttributeChangedCallback(node(_loc(name)), on_change))

    def on_toggle(msg, plug, other, data):
        if (msg & om.MNodeMessage.kAttributeSet) and plug.partialName(useLongNames=True).endswith(MIRROR_ATTR):
            _on_mirror_toggled()

    ids.append(om.MNodeMessage.addAttributeChangedCallback(node(GROUP), on_toggle))
    _CALLBACKS[GROUP] = ids
    return ids


def _kill_jobs() -> int:
    """Remove this session's mirror callbacks. Ids are pointers, valid only
    in the session that made them, so they live in a module dict, never in
    the scene; the attribute on the group only records how many there were."""
    import maya.api.OpenMaya as om

    killed = 0
    for cid in _CALLBACKS.pop(GROUP, []):
        try:
            om.MMessage.removeCallback(cid)
            killed += 1
        except Exception:  # noqa: BLE001 - already gone with its node
            pass
    if cmds.objExists(GROUP) and cmds.attributeQuery(JOBS_ATTR, node=GROUP, exists=True):
        cmds.setAttr(GROUP + "." + JOBS_ATTR, "", type="string")
    return killed


def _frame_front(mesh: str, panel: str) -> Optional[str]:
    """Look at the character from the front through the panel's camera. Placed
    by hand: viewFit on a persp camera keeps whatever angle it had."""
    cam = cmds.modelEditor(panel, query=True, camera=True)
    if not cam:
        return None
    try:
        if cmds.getAttr(cam + ".orthographic"):
            cmds.select(mesh)
            cmds.viewFit(cam, fitFactor=0.9)
            cmds.select(clear=True)
            return cam
        import math
        bb = cmds.exactWorldBoundingBox(mesh)
        c = [(bb[i] + bb[i + 3]) * 0.5 for i in range(3)]
        h = max(bb[4] - bb[1], bb[3] - bb[0], 1e-3)
        fov = min(cmds.camera(cam, query=True, horizontalFieldOfView=True),
                  cmds.camera(cam, query=True, verticalFieldOfView=True))
        dist = 0.5 * h / math.tan(math.radians(fov) * 0.5) * 1.15
        cmds.viewPlace(cam, eye=(c[0], c[1], bb[5] + dist), lookAt=c, upDirection=(0, 1, 0))
        return cam
    except Exception:  # noqa: BLE001 - framing is a convenience, never a failure
        return None


def _prop_meshes(mesh: str) -> List[str]:
    try:
        import attach_props
        return attach_props.prop_meshes(mesh)
    except Exception:  # noqa: BLE001
        return []


def enter_mode(mesh: str = "Mesh") -> Dict[str, Any]:
    """X-ray on, mesh + props unselectable (reference layer), mirror on, camera
    from the front, Move tool on the first marker. Previous viewport state is
    stored on the group so `exit_mode` restores exactly that, even after the
    scene was closed and reopened in between."""
    if not cmds.objExists(GROUP):
        return {"mode": False, "why": "no " + GROUP}
    for attr, kind, default in ((MIRROR_ATTR, "bool", True), (JOBS_ATTR, "string", ""),
                                (PREV_XRAY_ATTR, "bool", False), (PANEL_ATTR, "string", "")):
        if not cmds.attributeQuery(attr, node=GROUP, exists=True):
            if kind == "string":
                cmds.addAttr(GROUP, longName=attr, dataType="string")
                cmds.setAttr(GROUP + "." + attr, default, type="string")
            else:
                cmds.addAttr(GROUP, longName=attr, attributeType="bool", defaultValue=default, keyable=True)
    out: Dict[str, Any] = {"mode": True, "mirror": bool(cmds.getAttr(GROUP + "." + MIRROR_ATTR))}

    # The layer: reference display type (2) draws but cannot be picked.
    members = [m for m in [mesh] + _prop_meshes(mesh) if cmds.objExists(m)]
    if not cmds.objExists(LAYER):
        cmds.createDisplayLayer(name=LAYER, empty=True, noRecurse=True)
    cmds.setAttr(LAYER + ".displayType", 2)
    if members:
        cmds.editDisplayLayerMembers(LAYER, *members, noRecurse=True)
    out["layer"] = {"name": LAYER, "members": members}

    # X-ray on the visible panel; remember what it was.
    panel = ac.visible_model_panel()
    if panel:
        try:
            prev = bool(cmds.modelEditor(panel, query=True, xray=True))
            cmds.setAttr(GROUP + "." + PREV_XRAY_ATTR, prev)
            cmds.setAttr(GROUP + "." + PANEL_ATTR, panel, type="string")
            cmds.modelEditor(panel, edit=True, xray=True, locators=True)
            out["xray"] = {"panel": panel, "was": prev, "now": True}
            out["camera"] = _frame_front(mesh, panel)
        except Exception as exc:  # noqa: BLE001
            out["xray"] = {"panel": panel, "error": str(exc)}
    else:
        out["xray"] = {"panel": None, "why": "no visible model panel (batch?)"}

    _kill_jobs()
    jobs = _arm_mirror()
    cmds.setAttr(GROUP + "." + JOBS_ATTR, str(len(jobs)), type="string")
    out["mirror_callbacks"] = len(jobs)

    try:
        cmds.setToolTo("moveSuperContext")
        first = _loc("chin") if cmds.objExists(_loc("chin")) else None
        if first:
            cmds.select(first, replace=True)
        out["selected"] = first
    except Exception:  # noqa: BLE001 - no UI
        out["selected"] = None
    return out


def exit_mode(mesh: str = "Mesh", **_kw) -> Dict[str, Any]:
    """Back to a scene you can pose: layer gone (mesh selectable again), X-ray
    as it was, mirror jobs killed, markers hidden, nothing selected, and the
    camera back on the whole character -- the pose gallery's last frame is a
    hand close-up, which is what the person was otherwise left looking at."""
    out: Dict[str, Any] = {"mode": False}
    out["jobs_killed"] = _kill_jobs()
    if cmds.objExists(LAYER):
        cmds.delete(LAYER)
        out["layer_deleted"] = True
    if cmds.objExists(GROUP):
        panel = cmds.getAttr(GROUP + "." + PANEL_ATTR) if cmds.attributeQuery(PANEL_ATTR, node=GROUP, exists=True) else ""
        panel = panel if panel and cmds.modelEditor(panel, query=True, exists=True) else ac.visible_model_panel()
        if panel:
            prev = bool(cmds.getAttr(GROUP + "." + PREV_XRAY_ATTR)) if cmds.attributeQuery(PREV_XRAY_ATTR, node=GROUP, exists=True) else False
            try:
                cmds.modelEditor(panel, edit=True, xray=prev)
                out["xray"] = {"panel": panel, "now": prev}
            except Exception as exc:  # noqa: BLE001
                out["xray"] = {"panel": panel, "error": str(exc)}
            if cmds.objExists(mesh):
                out["camera"] = _frame_front(mesh, panel)
        out["markers"] = set_visible(False)
    cmds.select(clear=True)
    return out


def mode(action: str = "exit", mesh: str = "Mesh", **_kw) -> Dict[str, Any]:
    """Tool entry: enter or leave marker mode by hand (e.g. after a failed
    harness you gave up on, or to re-drag after a fit)."""
    if action == "enter":
        if cmds.objExists(GROUP):
            set_visible(True)
        return enter_mode(mesh)
    return exit_mode(mesh)


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
                # Each side is refined against its own side of the mesh; the
                # mirror jobs must not copy one side's refinement over the other.
                with _mirror_suspended():
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
