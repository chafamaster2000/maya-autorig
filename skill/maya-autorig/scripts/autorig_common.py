"""Shared Maya-side helpers for the marker-guided auto-rig.

Everything that touches Maya lives here or in the stage modules; the geometry
itself is in marker_geom (pure numpy). Three responsibilities:

  * mesh access: world-space points, shell decomposition, inside/outside tests
  * AdvancedSkeleton bootstrap without modal dialogs (a modal dialog blocks the
    whole MCP session, so every one AS could raise is pre-empted here)
  * evidence: per-stage JSON + PNG renders in a run directory, so "it worked"
    is a file a reviewer (human or subagent) can open, not a claim
"""

from __future__ import annotations

import collections
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

# AdvancedSkeleton lives in a different place on every platform (and studios
# move it), so it is resolved, never hardcoded: ADVANCEDSKELETON_DIR, then
# MAYA_SCRIPT_PATH, then the per-platform defaults. _as_procs owns that search.
def as_mel_path() -> str:
    """Absolute path of AdvancedSkeleton.mel. Raises with the list of places
    searched when it is not installed."""
    import _as_procs
    base = _as_procs.advancedskeleton_dir()
    for name in _as_procs._ENTRY_NAMES:
        cand = base / name
        if cand.is_file():
            return str(cand)
    raise RuntimeError("no AdvancedSkeleton.mel in {}".format(base))


def evidence_root() -> str:
    """Where a run writes its evidence: `autorig_evidence` inside Maya's
    current project, asked of Maya instead of guessed.

    Guessing is what breaks on Windows. `~/Documents` is wrong the moment
    Documents is redirected -- OneDrive moves it under
    C:\\Users\\<user>\\OneDrive\\Documents, a roaming profile puts it on a
    server, and a localised shell folder can rename it -- and Maya is then
    writing its project somewhere this path never looks. Maya knows where its
    project is, so ask it. On a default install the answer is the same
    ~/Documents/maya/projects/default as before.

    MAYA_AUTORIG_EVIDENCE overrides everything, for a studio that keeps
    evidence off the artist's drive. NOT MAYA_APP_DIR: that is the
    preferences directory, not the projects one.
    """
    override = os.environ.get("MAYA_AUTORIG_EVIDENCE")
    if override:
        return override
    try:
        root = cmds.workspace(query=True, rootDirectory=True)
    except Exception:  # noqa: BLE001 - no Maya (offline import): fall through
        root = ""
    if root:
        return os.path.join(root, "autorig_evidence")
    return os.path.expanduser("~/Documents/maya/projects/default/autorig_evidence")


# Back-compat for callers that read the module attribute. Prefer the function:
# a session that changes project mid-run gets the right answer from it.
EVIDENCE_ROOT = evidence_root()

MARKER_GROUP = "AutoRigMarkers"
TMP_PREFIX = "autorigTmp_"


# --------------------------------------------------------------------------- #
# mesh access
# --------------------------------------------------------------------------- #
def fn_mesh(mesh: str) -> om.MFnMesh:
    sel = om.MSelectionList()
    sel.add(mesh)
    return om.MFnMesh(sel.getDagPath(0).extendToShape())


def world_points(mesh: str) -> np.ndarray:
    pts = fn_mesh(mesh).getPoints(om.MSpace.kWorld)
    return np.array([[p.x, p.y, p.z] for p in pts], dtype=float)


def shells(mesh: str) -> Tuple[List[List[int]], List[int]]:
    """Connected components over shared edges. Returns (faces per shell,
    shell id per vertex)."""
    fn = fn_mesh(mesh)
    edge_faces = collections.defaultdict(list)
    face_verts: Dict[int, List[int]] = {}
    it = om.MItMeshPolygon(fn.object())
    while not it.isDone():
        i = it.index()
        face_verts[i] = list(it.getVertices())
        for e in it.getEdges():
            edge_faces[e].append(i)
        it.next()
    adj = collections.defaultdict(set)
    for fs in edge_faces.values():
        for a in fs:
            for b in fs:
                if a != b:
                    adj[a].add(b)
    n = fn.numPolygons
    shell_of_face = [-1] * n
    comps: List[List[int]] = []
    for start in range(n):
        if shell_of_face[start] != -1:
            continue
        sid = len(comps)
        stack, comp = [start], []
        shell_of_face[start] = sid
        while stack:
            f = stack.pop()
            comp.append(f)
            for nb in adj[f]:
                if shell_of_face[nb] == -1:
                    shell_of_face[nb] = sid
                    stack.append(nb)
        comps.append(comp)
    shell_of_vert = [-1] * fn.numVertices
    for sid, comp in enumerate(comps):
        for f in comp:
            for v in face_verts[f]:
                shell_of_vert[v] = sid
    return comps, shell_of_vert


# A shell counts as body when it holds at least this fraction of the mesh's
# vertices. Eyes, teeth, belts, soles fall under it; a head modelled as its
# own shell (chibi hood: 24 % of the vertices) or gloves (4 % each) do not.
# Taking only the largest shell lost the head of a stylised short character (H read 130 of 159
# cm) and the hands of a gloved character (the arm "ended" at the cuff).
BODY_SHELL_MIN_FRAC = 0.03
# ...and it must be a solid: a shell with many boundary edges is a cloth
# piece (coat, hood, collar: 6-11 % open edges; closed shells read < 1.5 %).
# Cloth in the silhouette raised a coated character's shoulder 12 cm into the
# collar and breaks every ray/volume measurement.
BODY_SHELL_MAX_OPEN = 0.03


def shell_open_ratios(mesh: str, comps: List[List[int]], sov: np.ndarray) -> List[float]:
    """Fraction of boundary edges (used by one triangle only) per shell."""
    fn = fn_mesh(mesh)
    _c, verts = fn.getTriangles()
    T = np.array(list(verts), dtype=int).reshape(-1, 3)
    E = np.vstack([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    E.sort(axis=1)
    uniq, cnt = np.unique(E, axis=0, return_counts=True)
    shell_of_edge = sov[uniq[:, 0]]
    out = []
    for sid in range(len(comps)):
        m = shell_of_edge == sid
        out.append(float((cnt[m] == 1).sum()) / max(1, int(m.sum())))
    return out


def body_shell_ids(mesh: str) -> Tuple[List[int], List[List[int]], np.ndarray]:
    comps, sov = shells(mesh)
    sov = np.array(sov)
    counts = np.bincount(sov[sov >= 0], minlength=len(comps))
    total = float(max(1, len(sov)))
    open_ratio = shell_open_ratios(mesh, comps, sov)
    keep = [i for i, c in enumerate(counts)
            if c / total >= BODY_SHELL_MIN_FRAC and open_ratio[i] <= BODY_SHELL_MAX_OPEN]
    if not keep:
        keep = [int(np.argmax(counts))]
    return keep, comps, sov


def body_points(mesh: str) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Vertices of the body shells (see BODY_SHELL_MIN_FRAC). Accessories
    (belts, eyes, soles) are small separate shells and would pollute every
    silhouette heuristic; a separate head or hands must stay in."""
    V = world_points(mesh)
    keep, comps, sov = body_shell_ids(mesh)
    mask = np.isin(sov, keep)
    info = {"shell_count": len(comps), "body_shells": keep,
            "body_faces": sum(len(comps[i]) for i in keep), "total_faces": sum(len(c) for c in comps),
            "body_vertices": int(mask.sum()), "total_vertices": int(len(V))}
    return V[mask], info


def mesh_triangles(mesh: str) -> np.ndarray:
    """Every triangle of the mesh (all shells), vertex ids, shape (n, 3)."""
    _counts, ids = fn_mesh(mesh).getTriangles()
    return np.array(list(ids), dtype=int).reshape(-1, 3)


def body_triangles(mesh: str) -> np.ndarray:
    """Triangles (vertex-id triples, indexing world_points order) of the
    largest shell only. A volume measurement must see the body and not the
    open accessory shells (eyes, soles), whose enclosed 'volume' is undefined
    and shifts with every translation."""
    fn = fn_mesh(mesh)
    _counts, verts = fn.getTriangles()
    T = np.array(list(verts), dtype=int).reshape(-1, 3)
    keep_ids, _comps, sov = body_shell_ids(mesh)
    ok = np.isin(sov, keep_ids)
    return T[ok[T[:, 0]] & ok[T[:, 1]] & ok[T[:, 2]]]


def body_shell_copy(mesh: str) -> str:
    """Temporary mesh holding only the largest shell, for inside/outside tests
    where an accessory's back face would otherwise flip the answer."""
    keep_ids, comps, _sov = body_shell_ids(mesh)
    dup = cmds.duplicate(mesh, name=TMP_PREFIX + "bodyShell")[0]
    others = [f for sid, comp in enumerate(comps) if sid not in keep_ids for f in comp]
    if others:
        cmds.delete(["{}.f[{}]".format(dup, f) for f in others])
    cmds.setAttr(dup + ".visibility", 0)
    return dup


def _ray_params(fn: om.MFnMesh, origin, direction, max_param: float = 1e5) -> List[float]:
    """Sorted, de-duplicated ray parameters of every hit along one ray."""
    res = fn.allIntersections(om.MFloatPoint(*origin), om.MFloatVector(*direction),
                              om.MSpace.kWorld, max_param, False)
    if res is None:  # API 2.0 returns None, not empty arrays, on a miss
        return []
    params = sorted(float(t) for t in res[1])
    out: List[float] = []
    for t in params:  # a hit on a shared edge is reported once per face
        if not out or t - out[-1] > 1e-3:
            out.append(t)
    return out


_WINDING_CACHE: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}


def _winding_geometry(shell_mesh: str) -> Tuple[np.ndarray, np.ndarray]:
    """World points + consistently oriented triangles of a (temporary) shell
    mesh, cached per node name for the duration of a stage."""
    key = shell_mesh + "@" + str(cmds.polyEvaluate(shell_mesh, vertex=True))
    if key not in _WINDING_CACHE:
        import marker_geom as mg
        fn = fn_mesh(shell_mesh)
        _c, verts = fn.getTriangles()
        T = np.array(list(verts), dtype=int).reshape(-1, 3)
        _WINDING_CACHE.clear()
        _WINDING_CACHE[key] = (world_points(shell_mesh), mg.orient_consistently(T))
    return _WINDING_CACHE[key]


def inside_test(shell_mesh: str, points: Dict[str, Sequence[float]]) -> Dict[str, Dict[str, Any]]:
    """Generalised winding number inside test (|w| > 0.5).

    Ray parity (odd/even hits, majority over three axes) needed closed
    shells: game characters are built from open, intersecting pieces (a vest
    without a back, pants overlapping a torso) and parity called a stylised short character's
    Root and Spine 'outside' while they sat in the middle of the body. The
    winding number degrades gracefully there. Reports the value so a reviewer
    can see how decided the answer was.
    """
    import marker_geom as mg
    V, T = _winding_geometry(shell_mesh)
    fn = fn_mesh(shell_mesh)
    names = list(points)
    Q = np.array([points[n] for n in names], dtype=float).reshape(-1, 3)
    w = mg.winding_numbers(V, T, Q) if len(Q) else np.zeros(0)
    out = {}
    for name, p, wi in zip(names, Q, w):
        closest, _n, _f = fn.getClosestPointAndNormal(om.MPoint(*p), om.MSpace.kWorld)
        dist = om.MVector(om.MPoint(*p) - closest).length()
        out[name] = {"inside": bool(abs(wi) > 0.5), "winding": round(float(wi), 3),
                     "surface_distance": round(dist, 2)}
    return out


def interior_segments(shell_mesh: str, x: float, y: float) -> List[Tuple[float, float]]:
    """(z_enter, z_exit) of every solid span along Z through (x, y), longest
    first, sampled with the winding number (see inside_test). The longest
    span at torso height is the torso; a braid or cape behind the back is its
    own, shorter span."""
    import marker_geom as mg
    V, T = _winding_geometry(shell_mesh)
    bb = cmds.exactWorldBoundingBox(shell_mesh)
    H = bb[4] - bb[1]
    n = int(max(60, min(160, (bb[5] - bb[2]) / max(0.004 * H, 0.2))))
    return mg.solid_spans_z(V, T, x, y, bb[2] - 1.0, bb[5] + 1.0, samples=n)


# --------------------------------------------------------------------------- #
# AdvancedSkeleton bootstrap (dialog-free)
# --------------------------------------------------------------------------- #
def as_ready(mesh: str) -> Dict[str, Any]:
    """Source AS, pre-empt its first-run and unit dialogs, open the window and
    point Body>Pre>Skin at the mesh. Raises instead of letting a dialog appear."""
    out: Dict[str, Any] = {"guards": []}
    if not int(mel.eval('exists "asGetScriptLocation"')):
        # MEL wants forward slashes even on Windows.
        mel.eval('source "{}";'.format(as_mel_path().replace("\\", "/")))
    unit = cmds.currentUnit(query=True, linear=True)
    if unit not in ("cm", "centimeter"):
        raise RuntimeError("linear unit is {}; AS needs centimeters (it would raise a dialog)".format(unit))
    if not cmds.optionVar(exists="asHaveRanThisVersion") or not cmds.optionVar(query="asHaveRanThisVersion"):
        cmds.optionVar(intValue=("asHaveRanThisVersion", 1))
        out["guards"].append("asHaveRanThisVersion pre-set (skips shelf-button dialog)")
    blocked = model_checker_blockers(mesh)
    if blocked:
        raise RuntimeError("asModelChecker would raise a dialog: " + "; ".join(blocked))
    if not cmds.textField("asBodySkinTextField", exists=True):
        mel.eval("AdvancedSkeleton;")
    if not cmds.textField("asBodySkinTextField", exists=True):
        raise RuntimeError("AdvancedSkeleton window did not build")
    cmds.select(mesh, replace=True)
    mel.eval("asChooseBodyInput asBodySkinTextField;")
    out["skin_field"] = cmds.textField("asBodySkinTextField", query=True, text=True)
    return out


def model_checker_blockers(mesh: str) -> List[str]:
    blocked = []
    t = cmds.getAttr(mesh + ".translate")[0]
    r = cmds.getAttr(mesh + ".rotate")[0]
    s = cmds.getAttr(mesh + ".scale")[0]
    rp = cmds.getAttr(mesh + ".rotatePivot")[0]
    sp = cmds.getAttr(mesh + ".scalePivot")[0]
    if any(v != 0 for v in t + r + rp + sp) or any(v != 1 for v in s):
        blocked.append("non-frozen transforms on {}".format(mesh))
    ignored = {"skinCluster", "blendShape", "tweak", "deltaMush", "shadingEngine"}
    hist = cmds.listHistory(mesh, pruneDagObjects=True, interestLevel=2) or []
    bad = [h for h in hist if cmds.objectType(h) not in ignored
           and not h.startswith(("asResetTransform", "rl4Embedded"))]
    if bad:
        blocked.append("construction history: {}".format(bad))
    return blocked


def import_fit_skeleton(template: str = "bipedBendy.ma") -> Dict[str, Any]:
    """asFitSkeletonImport reads the template from the asFitFiles optionMenu."""
    if cmds.objExists("FitSkeleton"):
        return {"imported": False, "reason": "FitSkeleton already in scene"}
    if not cmds.optionMenu("asFitFiles", exists=True):
        raise RuntimeError("AS window not built; call as_ready first")
    items = cmds.optionMenu("asFitFiles", query=True, itemListLong=True) or []
    labels = [cmds.menuItem(i, query=True, label=True) for i in items]
    if template not in labels:
        raise ValueError("template {!r} not among {}".format(template, labels))
    cmds.optionMenu("asFitFiles", edit=True, value=template)
    mel.eval("asFitSkeletonImport;")
    if not cmds.objExists("FitSkeleton"):
        raise RuntimeError("asFitSkeletonImport did not create FitSkeleton")
    return {"imported": True, "template": template}


def fit_joints() -> List[str]:
    js = cmds.listRelatives("FitSkeleton", allDescendents=True, type="joint") or []
    js.sort(key=lambda j: len(cmds.ls(j, long=True)[0].split("|")))
    return js


def fit_positions() -> Dict[str, List[float]]:
    return {j: [round(v, 3) for v in cmds.xform(j, query=True, worldSpace=True, translation=True)]
            for j in fit_joints()}


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #
class Evidence:
    """One directory per run; each stage writes <stage>.json (+ PNGs) and a
    line in summary.json. `expectation` is the text a reviewer gets with the
    image: what the picture must show for the stage to count as passed."""

    def __init__(self, run_dir: Optional[str] = None, scene_tag: Optional[str] = None):
        if run_dir is None:
            scene = cmds.file(query=True, sceneName=True, shortName=True) or "untitled"
            tag = scene_tag or os.path.splitext(scene)[0]
            run_dir = os.path.join(evidence_root(), tag, time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(run_dir, exist_ok=True)
        self.dir = run_dir
        self.summary_path = os.path.join(run_dir, "summary.json")
        self.summary: List[Dict[str, Any]] = []
        if os.path.exists(self.summary_path):
            try:
                self.summary = json.load(open(self.summary_path))
            except Exception:  # noqa: BLE001 - a corrupt summary must not block a run
                self.summary = []

    def record(self, stage: str, passed: bool, data: Dict[str, Any],
               images: Optional[List[str]] = None, expectation: str = "") -> Dict[str, Any]:
        entry = {"stage": stage, "passed": bool(passed), "time": time.strftime("%H:%M:%S"),
                 "json": os.path.join(self.dir, stage + ".json"),
                 "images": images or [], "expectation": expectation}
        with open(entry["json"], "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        self.summary = [e for e in self.summary if e["stage"] != stage] + [entry]
        with open(self.summary_path, "w") as fh:
            json.dump(self.summary, fh, indent=2)
        return entry


def _frame_camera(camera: str, bbox: Sequence[float], width: int, height: int, margin: float = 1.12) -> float:
    """Frame an orthographic camera on the bbox; returns the orthographic width
    in cm (0.0 for an unknown camera), which is what turns pixels back into cm."""
    x0, y0, z0, x1, y1, z1 = bbox
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    h = y1 - y0
    if camera == "front":
        span = x1 - x0
        cmds.xform(camera, worldSpace=True, translation=(cx, cy, z1 + 500), rotation=(0, 0, 0))
    elif camera == "side":
        span = z1 - z0
        cmds.xform(camera, worldSpace=True, translation=(x1 + 500, cy, cz), rotation=(0, 90, 0))
    else:
        return 0.0
    ow = margin * max(span, h * width / float(height))
    cmds.setAttr(camera + "Shape.orthographicWidth", ow)
    return float(ow)


# Reference render size the scale is expressed against.
REFERENCE_WIDTH, REFERENCE_HEIGHT = 700, 900


# Evidence render size. Image tokens scale with pixels and the reviewer only
# needs to see joint dots and silhouettes: 490x630 is ~half the pixels of the
# original 700x900 and still legible. Callers can pass width/height explicitly.
RENDER_WIDTH, RENDER_HEIGHT = 490, 630


# How evidence pictures are made. "viewport": playblast of the visible model
# panel in X-ray with joints and control curves drawn -- what a rigger looks
# at: the points and the helpers, through the mesh. "render": the old
# mayaSoftware render (opaque or ghosted mesh + dots), kept as the fallback
# for a Maya without a model panel (mayapy, batch).
EVIDENCE_MODE = "viewport"
VIEWPORT_FLAGS = ("camera", "xray", "jointXray", "grid", "headsUpDisplay", "nurbsCurves", "joints",
                  "polymeshes", "locators", "displayAppearance", "lineWidth")


def visible_model_panel() -> Optional[str]:
    try:
        vis = [p for p in (cmds.getPanel(visiblePanels=True) or []) if cmds.getPanel(typeOf=p) == "modelPanel"]
    except Exception:  # noqa: BLE001 - no UI at all
        return None
    return vis[0] if vis else None


def _viewport_capture(path_png: str, camera: str, width: int, height: int, mesh: str,
                      frame: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Playblast the visible model panel through `camera` in X-ray. Playblast
    draws the VISIBLE panel, whatever panel one edits, so the flags go on
    that one and are restored afterwards. `frame` (xmin, ymin, zmin, xmax,
    ymax, zmax) fits the persp camera on that box instead of the mesh: the
    hand close-up."""
    panel = visible_model_panel()
    if not panel:
        raise RuntimeError("no visible model panel")
    saved = {}
    for k in VIEWPORT_FLAGS:
        try:
            saved[k] = cmds.modelEditor(panel, query=True, **{k: True})
        except Exception:  # noqa: BLE001 - flag unknown on this Maya
            pass
    try:
        cmds.modelEditor(panel, edit=True, camera=camera, xray=True, jointXray=True, grid=False,
                         headsUpDisplay=False, nurbsCurves=True, joints=True, polymeshes=True, locators=True,
                         displayAppearance="smoothShaded", lineWidth=2.0)
        if camera not in ("front", "side", "top"):
            sel = cmds.ls(selection=True)
            if frame is not None:
                # viewFit ignores hidden nodes, so place the camera by hand:
                # look at the box centre from the front-right-above, far
                # enough for the box's bounding sphere to fill the view.
                import math
                c = [(frame[i] + frame[i + 3]) * 0.5 for i in range(3)]
                radius = 0.5 * max(frame[i + 3] - frame[i] for i in range(3))
                fov = min(cmds.camera(camera, query=True, horizontalFieldOfView=True),
                          cmds.camera(camera, query=True, verticalFieldOfView=True))
                dist = max(1e-3, radius) / math.tan(math.radians(fov) * 0.5) * 1.1
                # Look from the side the subject is on, so the rest of the
                # body stays BEHIND it: a right hand (x < 0) framed from the
                # character's left has a thigh in front of it.
                sx = -0.85 if c[0] < 0 else (0.85 if c[0] > 0 else -0.45)
                d = (sx, 0.35, 0.85)
                n = math.sqrt(sum(v * v for v in d))
                eye = [c[i] + dist * d[i] / n for i in range(3)]
                cmds.viewPlace(camera, eye=eye, lookAt=c, upDirection=(0, 1, 0))
            else:
                cmds.select(mesh)
                cmds.viewFit(camera, fitFactor=0.95)
            cmds.select(sel if sel else None, replace=True) if sel else cmds.select(clear=True)
        os.makedirs(os.path.dirname(path_png), exist_ok=True)
        cmds.playblast(completeFilename=path_png, format="image", compression="png",
                       frame=cmds.currentTime(query=True), viewer=False, showOrnaments=False, offScreen=True,
                       widthHeight=(width, height), percent=100, forceOverwrite=True, editorPanelName=panel)
    finally:
        restore = {k: v for k, v in saved.items() if k != "camera"}
        try:
            cmds.modelEditor(panel, edit=True, camera=saved.get("camera", "persp"), **restore)
        except Exception:  # noqa: BLE001 - never let a restore failure hide the evidence
            pass
    return {"mode": "viewport", "panel": panel}


def render_evidence(path_png: str, mesh: str, camera: str = "front",
                    points: Optional[Dict[str, Sequence[float]]] = None,
                    point_radius: Optional[float] = None, transparent_mesh: bool = True,
                    width: int = RENDER_WIDTH, height: int = RENDER_HEIGHT, extra_visible: Sequence[str] = (),
                    frame: Optional[Sequence[float]] = None) -> Dict[str, Any]:
    """Software-render the mesh (optionally see-through) with red spheres at
    the given points, from an orthographic camera framed on the mesh.

    Joints live inside the body, so an opaque render proves nothing; the
    see-through copy is what makes placement visible. Temporary nodes are all
    prefixed and deleted afterwards, the original mesh is never modified.
    """
    tmp: List[str] = []
    hidden: List[str] = []
    result: Dict[str, Any] = {"path": path_png, "camera": camera}
    use_viewport = EVIDENCE_MODE == "viewport" and visible_model_panel() is not None
    try:
        bbox = cmds.exactWorldBoundingBox(mesh)
        H = bbox[4] - bbox[1]
        ow = _frame_camera(camera, bbox, width, height) if camera in ("front", "side", "top") else None
        # The scale is saved with every render: a reviewer's pixel measurement
        # maps back to cm, and a bigger re-render (width=700, height=900) is
        # one call away when detail is needed.
        result.update({"width": width, "height": height,
                       "render_scale": round(width / float(REFERENCE_WIDTH), 3),
                       "cm_per_px": round(ow / float(width), 4) if ow else None})

        # everything else in the viewport is noise for the reviewer. Keep the
        # mesh's own top assembly whatever the caller passed (short name, long
        # path, a mesh parented under a group): hiding it rendered 1 KB blank
        # PNGs that a blind critic rightly refused to judge.
        long_mesh = (cmds.ls(mesh, long=True) or [mesh])[0]
        mesh_top = long_mesh.split("|")[1] if long_mesh.startswith("|") else long_mesh
        keep = {mesh, long_mesh, mesh_top, "persp", "top", "front", "side"} | set(extra_visible)
        # Viewport evidence keeps the rig visible on purpose: joints, fit
        # joints, markers and helpers are the point of the picture.
        for node in ([] if use_viewport else cmds.ls(assemblies=True)):
            if node in keep or "|" + node in keep:
                continue
            vis = node + ".visibility"
            # AS locks FitSkeleton.visibility; joints don't render anyway
            if cmds.getAttr(vis) and cmds.getAttr(vis, settable=True):
                cmds.setAttr(vis, 0)
                hidden.append(node)

        if transparent_mesh and not use_viewport:
            ghost = cmds.duplicate(mesh, name=TMP_PREFIX + "ghost")[0]
            tmp.append(ghost)
            sh = cmds.shadingNode("lambert", asShader=True, name=TMP_PREFIX + "ghostShader")
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=TMP_PREFIX + "ghostSG")
            cmds.connectAttr(sh + ".outColor", sg + ".surfaceShader", force=True)
            cmds.setAttr(sh + ".color", 0.75, 0.75, 0.8, type="double3")
            cmds.setAttr(sh + ".transparency", 0.72, 0.72, 0.72, type="double3")
            cmds.sets(ghost, edit=True, forceElement=sg)
            tmp += [sh, sg]
            cmds.setAttr(mesh + ".visibility", 0)
            hidden.append(mesh)

        if points:
            r = point_radius or 0.011 * H
            sh = cmds.shadingNode("surfaceShader", asShader=True, name=TMP_PREFIX + "dotShader")
            sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=TMP_PREFIX + "dotSG")
            cmds.connectAttr(sh + ".outColor", sg + ".surfaceShader", force=True)
            cmds.setAttr(sh + ".outColor", 1.0, 0.05, 0.05, type="double3")
            tmp += [sh, sg]
            grp = cmds.group(empty=True, name=TMP_PREFIX + "dots")
            tmp.append(grp)
            for name, p in points.items():
                s = cmds.polySphere(radius=r, subdivisionsX=10, subdivisionsY=8,
                                    name=TMP_PREFIX + "dot_" + name)[0]
                cmds.xform(s, worldSpace=True, translation=list(p))
                cmds.sets(s, edit=True, forceElement=sg)
                cmds.parent(s, grp)

        if use_viewport:
            result.update(_viewport_capture(path_png, camera, width, height, mesh, frame=frame))
        else:
            result["mode"] = "render"
            cmds.setAttr("defaultRenderGlobals.currentRenderer", "mayaSoftware", type="string")
            cmds.setAttr("defaultRenderGlobals.imageFormat", 32)  # png
            cmds.setAttr("defaultRenderGlobals.enableDefaultLight", 1)
            cmds.setAttr("defaultResolution.width", width)
            cmds.setAttr("defaultResolution.height", height)
            cmds.setAttr("defaultResolution.deviceAspectRatio", width / float(height))
            img = cmds.render(camera, xresolution=width, yresolution=height)
            os.makedirs(os.path.dirname(path_png), exist_ok=True)
            shutil.copyfile(img, path_png)
        result["ok"] = os.path.getsize(path_png) > 0
        result["bytes"] = os.path.getsize(path_png)
    except Exception as exc:  # noqa: BLE001 - evidence failure is reported, never fatal
        result["ok"] = False
        result["error"] = "{}: {}".format(type(exc).__name__, exc)
    finally:
        for node in hidden:
            if cmds.objExists(node):
                cmds.setAttr(node + ".visibility", 1)
        if tmp:
            cmds.delete([t for t in tmp if cmds.objExists(t)])
    return result


# --------------------------------------------------------------------------- #
# rig state
# --------------------------------------------------------------------------- #
def go_to_build_pose() -> Dict[str, Any]:
    """Put every AS control back to the build pose and say how far off it was.

    AS stores the build pose as a MEL string of setAttr commands on
    `buildPose.udAttr` (that is all asGoToBuildPose does, minus UI lookups).
    Measurements taken from a scene someone was posing by hand are worthless,
    so every verification stage calls this first and reports what it found.
    """
    if not cmds.objExists("buildPose") or not cmds.attributeQuery("udAttr", node="buildPose", exists=True):
        return {"available": False, "reset_controls": 0}
    controls = [c for c in (cmds.sets("ControlSet", query=True) or []) if cmds.objExists(c)] \
        if cmds.objExists("ControlSet") else []
    before = {c: cmds.xform(c, query=True, worldSpace=True, matrix=True) for c in controls}
    for attr in ("udAttr", "udExtraAttr"):
        if cmds.attributeQuery(attr, node="buildPose", exists=True):
            script = cmds.getAttr("buildPose." + attr) or ""
            if script.strip():
                mel.eval(script)
    moved = [c for c in controls
             if max(abs(a - b) for a, b in zip(before[c], cmds.xform(c, query=True, worldSpace=True, matrix=True))) > 1e-4]
    return {"available": True, "reset_controls": len(moved), "was_posed": bool(moved), "moved_sample": moved[:8]}
