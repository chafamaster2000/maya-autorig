"""Playblast the current viewport with everything visible (rig, controls,
helpers): run_script tools/viewport_capture.py argv=[out_dir, cam=persp|front|side, fit=all|<node>, xray?]"""
import json, os, sys
import maya.cmds as cmds
out_dir = sys.argv[1]; cam = sys.argv[2] if len(sys.argv) > 2 else "persp"; fit = sys.argv[3] if len(sys.argv) > 3 else "all"
os.makedirs(out_dir, exist_ok=True)
# Playblast draws the VISIBLE model panel; editing another one changes nothing.
vis = [p for p in (cmds.getPanel(visiblePanels=True) or []) if cmds.getPanel(typeOf=p) == "modelPanel"]
panel = vis[0] if vis else cmds.getPanel(type="modelPanel")[0]
cmds.setFocus(panel)
cmds.modelPanel(panel, edit=True, camera=cam)
xray = len(sys.argv) > 4 and sys.argv[4] == "xray"
cmds.modelEditor(panel, edit=True, allObjects=True, nurbsCurves=True, joints=True, polymeshes=True, locators=True,
                 displayAppearance="smoothShaded", wireframeOnShaded=False, grid=False, headsUpDisplay=False,
                 xray=xray, jointXray=xray, lineWidth=2.0 if xray else 1.0)
if fit == "all":
    cmds.select(clear=True); cmds.viewFit(cam, all=True, fitFactor=0.95)
else:
    cmds.select(fit); cmds.viewFit(cam, fitFactor=0.95); cmds.select(clear=True)
path = os.path.join(out_dir, "viewport_{}{}.png".format(cam, "_xray" if xray else ""))
cmds.playblast(completeFilename=path, format="image", compression="png", frame=cmds.currentTime(query=True),
               viewer=False, showOrnaments=False, offScreen=True, widthHeight=(1400, 1000), percent=100, forceOverwrite=True,
               editorPanelName=panel)
print("CAPTURE_JSON " + json.dumps({"path": path, "panel": panel, "scene": cmds.file(query=True, sceneName=True)}))
