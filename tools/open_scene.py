"""Open a scene (force, no save of the current one): run_script tools/open_scene.py argv=[path]"""
import json, sys
import maya.cmds as cmds
p = sys.argv[1]
cmds.file(p, open=True, force=True, ignoreVersion=True)
print("OPEN_JSON " + json.dumps({"scene": cmds.file(query=True, sceneName=True), "meshes": cmds.ls(type="mesh")[:5]}))
