"""One-call auto-rig: mesh in, rigged and skinned character out.

Orchestrates the full pipeline and reports every stage, so a partial failure
tells you exactly where it stopped and what already exists in the scene.

  import_fit_skeleton -> auto_place_fit_joints -> build_advancedskeleton
    -> transfer_skin (when golden_mesh is given) or bind_skin

Stages that fail leave the scene intact for inspection; nothing is rolled back,
because a half-built rig is usually more useful to debug than an empty scene.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _shim import run_main, skill_entry, skill_error_from_exception, skill_success  # noqa: E402
from _as_procs import maya_modules  # noqa: E402

import auto_place_fit_joints  # noqa: E402
import bind_skin  # noqa: E402
import transfer_skin  # noqa: E402


class StageFailure(RuntimeError):
    def __init__(self, stage: str, result: Dict[str, Any]) -> None:
        self.stage = stage
        self.result = result
        super().__init__("{}: {}".format(stage, result.get("message", "failed")))


def _run(stages: List[Dict[str, Any]], name: str, fn: Callable[..., Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    started = time.time()
    result = fn(**kwargs)
    entry = {
        "stage": name,
        "success": bool(result.get("success")),
        "message": result.get("message", ""),
        "seconds": round(time.time() - started, 2),
    }
    stages.append(entry)
    if not entry["success"]:
        raise StageFailure(name, result)
    return result


def _import_fit_skeleton(template: str) -> Dict[str, Any]:
    """Use the upstream tool when present; otherwise drive AS directly."""
    try:
        import import_fit_skeleton  # type: ignore

        return import_fit_skeleton.main(template_name=template, kind="body")
    except ImportError:
        from _as_procs import resolve_proc

        cmds, mel = maya_modules()
        if cmds.objExists("FitSkeleton"):
            return {"success": True, "message": "FitSkeleton already present", "context": {}}
        mel.eval("{};".format(resolve_proc("import_fit_skeleton")))
        ok = cmds.objExists("FitSkeleton")
        return {
            "success": ok,
            "message": "FitSkeleton imported" if ok else "FitSkeleton was not created",
            "context": {"template": template},
        }


def _build_rig() -> Dict[str, Any]:
    try:
        import build_advancedskeleton  # type: ignore

        return build_advancedskeleton.main()
    except ImportError:
        from _as_procs import resolve_proc

        cmds, mel = maya_modules()
        if cmds.currentUnit(query=True, linear=True) not in {"cm", "centimeter"}:
            cmds.currentUnit(linear="cm")
        mel.eval("{};".format(resolve_proc("build_rig")))
        ok = cmds.objExists("Group")
        return {
            "success": ok,
            "message": "Rig built" if ok else "Build finished without creating Group",
            "context": {},
        }


@skill_entry
def main(
    mesh: str = "",
    template: str = "biped",
    golden_mesh: str = "",
    skip_auto_place: bool = False,
    bind_method: str = "geodesic",
    max_influences: int = 4,
    **_params: Any,
) -> Dict[str, Any]:
    stages: List[Dict[str, Any]] = []
    try:
        cmds, _mel = maya_modules()
        if not mesh:
            raise ValueError("mesh is required.")
        if not cmds.objExists(mesh):
            raise ValueError("Mesh '{}' does not exist.".format(mesh))
        if golden_mesh and not cmds.objExists(golden_mesh):
            raise ValueError("golden_mesh '{}' does not exist.".format(golden_mesh))

        _run(stages, "import_fit_skeleton", _import_fit_skeleton, template=template)

        if skip_auto_place:
            stages.append(
                {"stage": "auto_place_fit_joints", "success": True,
                 "message": "skipped by request", "seconds": 0.0}
            )
        else:
            _run(stages, "auto_place_fit_joints", auto_place_fit_joints.main, mesh=mesh)

        _run(stages, "build_advancedskeleton", _build_rig)

        if golden_mesh:
            skin = _run(
                stages, "transfer_skin", transfer_skin.main,
                source_mesh=golden_mesh, target_mesh=mesh,
            )
        else:
            skin = _run(
                stages, "bind_skin", bind_skin.main, mesh=mesh,
                bind_method=bind_method, max_influences=max_influences,
            )

        return skill_success(
            "Auto-rigged '{}' in {} stages ({:.1f}s)".format(
                mesh, len(stages), sum(s["seconds"] for s in stages)
            ),
            prompt=(
                "Auto-placement and weight transfer are both approximations. "
                "Review the fit joints and test extreme poses before animating."
            ),
            mesh=mesh,
            template=template,
            weights_from=golden_mesh or "fresh bind",
            stages=stages,
            skin=skin.get("context", {}),
        )
    except StageFailure as failure:
        return skill_error_from_exception(
            failure,
            message="Auto-rig stopped at '{}'".format(failure.stage),
            prompt=(
                "The scene is left as-is for inspection. Fix the reported cause and "
                "re-run the remaining stages individually."
            ),
            failed_stage=failure.stage,
            stage_error=failure.result.get("context", {}),
            stages=stages,
        )
    except Exception as exc:
        return skill_error_from_exception(
            exc, message="Auto-rig failed before any stage ran",
            prompt="Run discover_procs to validate the AdvancedSkeleton install.",
            stages=stages,
        )


if __name__ == "__main__":
    run_main(main)
