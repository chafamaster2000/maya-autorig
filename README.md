# maya-autorig

Marker-guided automatic rigging for Maya, on top of **AdvancedSkeleton**,
driven from an agent through [DCC-MCP](https://github.com/dcc-mcp/dcc-mcp-maya).

Point it at a character mesh and it places the fit skeleton, builds the rig,
binds the skin and **proves** the result: every stage writes JSON and a
viewport capture, and the pipeline refuses to advance on a stage that failed
its own checks.

```
markers.propose  ->  fit_from_markers  ->  build_rig  ->  bind_skin
                            |                                |
                       verify_fit                       verify_skin
                                                        attach_props
                                                        pose_gallery
```

Written for game characters: 4 bones per vertex (Unity's Standard quality),
linear skinning, no dependency on a T-pose.

## What it does that a one-click auto-rigger does not

- **The markers are the pose.** Eight body markers (chin, groin, wrists,
  elbows, knees) are proposed from the mesh and can be dragged; the arm angle
  comes from where they are, so A-pose, T-pose and arms-down all work without
  a detector deciding for you.
- **Fingers come from the mesh.** The hand is sliced along its own axis and
  finger lobes are found as connected components of the mesh, so a five-finger
  hand gets five chains, a four-lobe glove gets four, and a mitten gets one.
  The thumb is identified separately, and the wrist is re-derived from the
  palm when the arm walk stopped inside a sleeve.
- **Eyes come from the mesh** too, when the model has eyeballs: a mirror pair
  of small closed shells inside the head beats any proportional guess.
- **Props ride a bone.** A bag, a bomb or a weapon keeps its own mesh and gets
  a one-influence bind to the bone whose segment runs closest; anything
  resting on the ground rides the root instead.
- **Nothing is asserted without evidence.** Each stage records what it
  measured, and the pictures are viewport X-ray captures with the joints and
  controls drawn through the mesh, meant to be read by a cheap reviewer
  model rather than loaded into your main context.
- **Grading is comparative.** The gauntlet holds a run against a yardstick rig
  *you* supply, row by row, with tolerances derived from that rig's own
  numbers. There is no invented threshold and no yardstick in this repo.

> **En castellano y paso a paso: [`GUIA.md`](GUIA.md).**

## Install

Full chain (packages, Maya adapter, skill, Claude Code) in
[`docs/INSTALL.md`](docs/INSTALL.md).

**Windows**

```powershell
powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1 -DryRun
powershell -ExecutionPolicy Bypass -File tools\install_windows.ps1
```

**macOS / Linux**

```bash
python3 -m pip install --user dcc-mcp-maya
dcc-mcp-maya install --yes
tools/install_skill.sh
```

Then open Maya and, from your agent, `load_skill("maya-autorig")`.

The installer brings the whole MCP chain: `dcc-mcp-core`, `dcc-mcp-server`
(the gateway), `dcc-mcp-maya` (the Maya adapter and its `userSetup.py`), this
skill, and the Claude Code entry. It does **not** install Maya, Python, or
AdvancedSkeleton, and it does not start a gateway by hand: the sidecar inside
Maya launches one when Maya opens. See
[`docs/INSTALL.md`](docs/INSTALL.md) for the full table.

## Use

```
markers_propose(mesh="Mesh", pose="A")     # drag the mk_* locators if needed
harness_run(mesh="Mesh")                   # fit -> build -> bind -> verify -> props
```

or the whole thing from a raw file:

```
gauntlet_run(source="C:/path/character.fbx", pose="A")
```

Evidence lands in `~/Documents/maya/projects/default/autorig_evidence/<scene>/<run>/`
(override with `MAYA_AUTORIG_EVIDENCE`).

## Requirements

- Maya 2022 or newer, with **AdvancedSkeleton 6.x** installed (it is content,
  not a package; set `ADVANCEDSKELETON_DIR` if it lives somewhere unusual).
- `dcc-mcp-maya` (pulls `dcc-mcp-core` and the `dcc-mcp-server` gateway).
- numpy, which ships inside Maya.

## Layout

```
skill/maya-autorig/     the skill: tools.yaml + scripts/ (the whole pipeline)
tools/                  installers and operator helpers
docs/INSTALL.md         the MCP chain, end to end, per platform
```

## Notes

The rig itself is built by AdvancedSkeleton, which is commercial software by
Animation Studios and is not included or redistributed here. This project
automates driving it.

MIT licensed. See [`LICENSE`](LICENSE).
