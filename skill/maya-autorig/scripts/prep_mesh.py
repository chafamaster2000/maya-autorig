"""Import a raw character (OBJ/FBX) and normalise it for the auto-rig.

New scene, import, bake the importer's unit scale, strip any skeleton/skin
that came along, keep the main body mesh (or unite everything), ground it,
centre it on X, freeze, zero pivots. The AS model checker rejects anything
less. Returns (mesh, info).
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import maya.cmds as cmds


def _mesh_xforms():
    live = [m for m in cmds.ls(type="mesh", long=True) if not cmds.getAttr(m + ".intermediateObject")]
    return sorted({cmds.listRelatives(m, parent=True, fullPath=True)[0] for m in live})


def prepare(path: str, scale_xz: float = 1.0, target_h: Optional[float] = None,
            name: str = "Mesh", extras: str = "keep") -> Tuple[str, Dict[str, Any]]:
    """`extras`: what to do with meshes that are not the body (a bag, a bomb,
    a weapon). 'keep' (default) leaves them as their own meshes, moved and
    scaled with the body, for `attach_props` to bind rigidly after the build;
    'drop' deletes them (the body alone, what the gauntlet grades); 'unite'
    merges everything into one mesh, which makes a prop deform like flesh."""
    cmds.file(new=True, force=True)
    before = set(cmds.ls(assemblies=True, long=True))
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        cmds.file(path, i=True, type="FBX", ignoreVersion=True, mergeNamespacesOnClash=True, namespace="f")
    else:
        cmds.file(path, i=True, type="OBJ", ignoreVersion=True,
                  mergeNamespacesOnClash=True, namespace="f", options="mo=1")
    # The FBX importer switches the scene unit to the file's (metres): every
    # bbox then reads 100x too small and AS's unit guard refuses to build.
    cmds.currentUnit(linear="cm")
    new = [n for n in cmds.ls(assemblies=True, long=True) if n not in before]
    if not [n for n in new if cmds.listRelatives(n, allDescendents=True, type="mesh")]:
        raise RuntimeError("no mesh imported from {}".format(path))
    # An FBX may carry a skeleton/skin: bake the current pose into the mesh
    # (delete history) and drop the joints -- the auto-rig wants a bare body.
    for m in _mesh_xforms():
        cmds.delete(m, constructionHistory=True)
        if cmds.listRelatives(m, parent=True):
            cmds.parent(m, world=True)          # the long path changes here...
    # The importer expresses its unit conversion as a transform scale (x100
    # for a metre file); an absolute xform(scale) later would replace it and
    # shrink the body 100x. Bake it first.
    for m in _mesh_xforms():
        cmds.makeIdentity(m, apply=True, translate=True, rotate=True, scale=True)
    keep = set(_mesh_xforms())                   # ...so recompute before pruning
    stray = [n for n in cmds.ls(assemblies=True, long=True) if n not in before and n not in keep]
    if stray:
        cmds.delete(stray)
    xf = [n for n in cmds.ls(assemblies=True, long=True) if n not in before]
    dropped: list = []
    props: list = []
    if len(xf) > 1 and extras == "unite":
        mesh = cmds.polyUnite(xf, constructionHistory=False, name=name)[0]
    elif len(xf) > 1:
        counts = {n: cmds.polyEvaluate(n, vertex=True) for n in xf}
        main = max(counts, key=counts.get)
        others = [n for n in xf if n != main]
        if extras == "drop":
            dropped = [{"name": n.split("|")[-1], "verts": counts[n]} for n in others]
            cmds.delete(others)
        else:
            props = [{"name": n.split("|")[-1], "verts": counts[n]} for n in others]
        mesh = cmds.rename(main, name)
    else:
        mesh = cmds.rename(xf[0], name)
    cmds.delete(mesh, constructionHistory=True)
    # Props ride with the body through every normalisation below, or they end
    # up at the raw import's scale and position while the body is grounded.
    def _all():
        return [mesh] + [n for n in (p["name"] for p in props) if cmds.objExists(n)]
    bb = cmds.exactWorldBoundingBox(mesh)
    h = bb[4] - bb[1]
    s = 1.0
    if target_h and h > 1e-6:
        s = target_h / h
        for n in _all():
            cmds.xform(n, scale=(s, s, s), worldSpace=True)
            cmds.makeIdentity(n, apply=True, translate=True, rotate=True, scale=True)
    bb = cmds.exactWorldBoundingBox(mesh)
    # Feet on the floor, body centred on X and Z: a character exported 64 cm
    # off the origin in Z breaks every depth heuristic that assumes the body
    # straddles z=0 (a gloved character shipped with z in [41, 87]).
    for n in _all():
        cmds.move(-(bb[0] + bb[3]) / 2.0, -bb[1], -(bb[2] + bb[5]) / 2.0, n, relative=True)
    if abs(scale_xz - 1.0) > 1e-6:
        for n in _all():
            cmds.makeIdentity(n, apply=True, translate=True)
            cmds.xform(n, scale=(scale_xz, 1.0, scale_xz), worldSpace=True)
    # Freeze everything and zero the pivots: the AS model checker rejects any
    # non-zero translate/rotate/pivot or non-unit scale on the mesh.
    for n in _all():
        cmds.makeIdentity(n, apply=True, translate=True, rotate=True, scale=True)
        cmds.xform(n, zeroTransformPivots=True)
        cmds.delete(n, constructionHistory=True)
    cmds.select(clear=True)
    bb = cmds.exactWorldBoundingBox(mesh)
    return mesh, {"source": path, "height": round(bb[4] - bb[1], 2), "bbox": [round(v, 2) for v in bb],
                  "scaled_by": round(s, 5), "scale_xz": scale_xz, "verts": cmds.polyEvaluate(mesh, vertex=True),
                  "dropped_meshes": dropped, "props": props, "extras": extras}
