"""Save the scene under a stage-suffixed name so every stage is recoverable.

Building and binding are the two steps worth undoing wholesale; a saved copy
per stage beats trusting Maya's undo queue across a MEL build.
"""
import os

import maya.cmds as cmds


def main(suffix="fit", **_kw):
    current = cmds.file(query=True, sceneName=True)
    if not current:
        raise RuntimeError("scene has no name; save it once first")
    base, _ext = os.path.splitext(current)
    base = base.rsplit("__", 1)[0]  # strip a previous stage suffix
    target = "{}__{}.mb".format(base, suffix)
    cmds.file(rename=target)
    cmds.file(save=True, type="mayaBinary")
    return {"saved": cmds.file(query=True, sceneName=True), "bytes": os.path.getsize(target)}


def reopen(suffix="fit", **_kw):
    """Discard the live scene and reload the stage checkpoint (a failed AS
    build leaves half a rig behind and AS refuses to build over it)."""
    current = cmds.file(query=True, sceneName=True)
    base, _ext = os.path.splitext(current)
    base = base.rsplit("__", 1)[0]
    target = "{}__{}.mb".format(base, suffix)
    if not os.path.exists(target):
        raise FileNotFoundError(target)
    cmds.file(target, open=True, force=True)
    return {"opened": cmds.file(query=True, sceneName=True),
            "top": cmds.ls(assemblies=True)}
