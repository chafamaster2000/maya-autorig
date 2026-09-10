"""Print what is open: scene, modified flag, unit, joints/meshes/skin counts."""
import json
import maya.cmds as cmds
meshes = [m for m in cmds.ls(type="mesh", long=True) if not cmds.getAttr(m + ".intermediateObject")]
print("STATUS_JSON " + json.dumps({
    "scene": cmds.file(query=True, sceneName=True), "modified": cmds.file(query=True, modified=True),
    "unit": cmds.currentUnit(query=True, linear=True), "joints": len(cmds.ls(type="joint") or []),
    "meshes": [m.split("|")[-2] for m in meshes][:30], "skinClusters": len(cmds.ls(type="skinCluster") or []),
    "top": cmds.ls(assemblies=True), "plugins_missing": cmds.unknownPlugin(query=True, list=True) or []}))
