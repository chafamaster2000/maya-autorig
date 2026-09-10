"""Auto-place FitSkeleton joints by scanning a target mesh.

Wraps AdvancedSkeleton's "Fit Auto Place". This is the Mixamo-like step: instead
of positioning every fit joint by hand, AS scans the model and derives a starting
placement. Treat the result as a starting point -- verify before building.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _shim import run_main, skill_entry, skill_error_from_exception, skill_success  # noqa: E402
from _as_procs import maya_modules, resolve_proc  # noqa: E402


def _fit_joint_positions(cmds: Any) -> Dict[str, List[float]]:
    joints = cmds.listRelatives("FitSkeleton", allDescendents=True, type="joint") or []
    return {j: cmds.xform(j, query=True, worldSpace=True, translation=True) for j in joints}


@skill_entry
def main(
    mesh: str = "",
    auto_scale: bool = True,
    tolerance: float = 0.0,
    **_params: Any,
) -> Dict[str, Any]:
    try:
        cmds, mel = maya_modules()
        if not cmds.objExists("FitSkeleton"):
            raise RuntimeError(
                "FitSkeleton does not exist. Run import_fit_skeleton (or "
                "create_fit_skeleton) before auto-placing."
            )
        if not mesh:
            raise ValueError("mesh is required: the model to scan.")
        if not cmds.objExists(mesh):
            raise ValueError("Mesh '{}' does not exist in the scene.".format(mesh))

        proc = resolve_proc("fit_auto_place")
        before = _fit_joint_positions(cmds)

        # Scale the fit skeleton to the mesh first: auto-placement on a fit
        # skeleton at the wrong scale lands every joint in the wrong place.
        scale_proc = None
        if auto_scale:
            scale_proc = resolve_proc("fit_auto_scale")
            cmds.select(mesh, replace=True)
            mel.eval("{};".format(scale_proc))

        cmds.select(mesh, replace=True)
        mel.eval("{};".format(proc))

        after = _fit_joint_positions(cmds)
        moved = [
            name
            for name, pos in after.items()
            if name not in before
            or max(abs(a - b) for a, b in zip(pos, before[name])) > max(tolerance, 1e-6)
        ]
        if not moved:
            raise RuntimeError(
                "'{}' ran but no fit joint moved. The mesh may be outside the "
                "expected orientation/scale, or this proc is not the auto-place "
                "entry point on your version.".format(proc)
            )

        return skill_success(
            "Auto-placed {} of {} fit joints from '{}'".format(
                len(moved), len(after), mesh
            ),
            prompt=(
                "Auto-placement is a starting point. Inspect the fit joints in the "
                "viewport and correct them before calling build_advancedskeleton."
            ),
            proc=proc,
            scale_proc=scale_proc,
            auto_scale=auto_scale,
            mesh=mesh,
            joints_total=len(after),
            joints_moved=len(moved),
            moved_joints=sorted(moved)[:50],
        )
    except Exception as exc:
        return skill_error_from_exception(
            exc,
            message="Fit auto-place failed",
            prompt="Run discover_procs to confirm the auto-place proc name on this install.",
        )


if __name__ == "__main__":
    run_main(main)
