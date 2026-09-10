"""Transfer skin weights from an approved 'golden' character onto a new mesh.

This is the speed unlock. Mixamo is fast because it retargets weights that were
already solved once on a canonical rig, instead of solving them per character.
Bind one character properly, approve it, and every later character inherits it.

Uses Maya's native copySkinWeights: AdvancedSkeleton's asCopySkin takes no
arguments and reads the UI, so it is not reproducible in batch.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, List

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _shim import run_main, skill_entry, skill_error_from_exception, skill_success  # noqa: E402
from _as_procs import maya_modules  # noqa: E402

SURFACE_ASSOCIATIONS = {"closest_point", "ray_cast", "closest_component"}
_SURFACE_ARG = {
    "closest_point": "closestPoint",
    "ray_cast": "rayCast",
    "closest_component": "closestComponent",
}


def _skin_cluster(cmds: Any, mesh: str) -> str:
    clusters = cmds.ls(cmds.listHistory(mesh) or [], type="skinCluster")
    if not clusters:
        raise RuntimeError("'{}' has no skinCluster.".format(mesh))
    return clusters[0]


@skill_entry
def main(
    source_mesh: str = "",
    target_mesh: str = "",
    surface_association: str = "closest_point",
    influence_association: str = "label",
    bind_target_if_needed: bool = True,
    joint_root: str = "DeformationSystem",
    **_params: Any,
) -> Dict[str, Any]:
    try:
        cmds, _mel = maya_modules()
        if not source_mesh or not target_mesh:
            raise ValueError("source_mesh and target_mesh are both required.")
        for name in (source_mesh, target_mesh):
            if not cmds.objExists(name):
                raise ValueError("Mesh '{}' does not exist.".format(name))
        if source_mesh == target_mesh:
            raise ValueError("source_mesh and target_mesh must differ.")
        if surface_association not in SURFACE_ASSOCIATIONS:
            raise ValueError(
                "surface_association must be one of {}".format(sorted(SURFACE_ASSOCIATIONS))
            )

        source_cluster = _skin_cluster(cmds, source_mesh)
        source_joints: List[str] = cmds.skinCluster(source_cluster, query=True, influence=True)

        bound_now = False
        try:
            target_cluster = _skin_cluster(cmds, target_mesh)
        except RuntimeError:
            if not bind_target_if_needed:
                raise
            from bind_skin import deformation_joints

            joints = deformation_joints(joint_root) or source_joints  # old call passed cmds: TypeError swallowed -> unbound target
            target_cluster = cmds.skinCluster(
                joints, target_mesh, toSelectedBones=True, bindMethod=0, skinMethod=0,  # linear, the project default
                maximumInfluences=4, obeyMaxInfluences=True,
            )[0]
            bound_now = True

        # influenceAssociation is ordered: fall back through the list per influence.
        cmds.copySkinWeights(
            sourceSkin=source_cluster,
            destinationSkin=target_cluster,
            noMirror=True,
            surfaceAssociation=_SURFACE_ARG[surface_association],
            influenceAssociation=[influence_association, "closestJoint", "oneToOne"],
        )

        target_joints = cmds.skinCluster(target_cluster, query=True, influence=True)
        return skill_success(
            "Transferred weights from '{}' to '{}'".format(source_mesh, target_mesh),
            prompt=(
                "Weight transfer is geometric: verify shoulders, hips and any area "
                "where the two silhouettes diverge, and adjust by hand there."
            ),
            source_mesh=source_mesh,
            target_mesh=target_mesh,
            source_skin_cluster=source_cluster,
            target_skin_cluster=target_cluster,
            source_influences=len(source_joints),
            target_influences=len(target_joints),
            target_was_bound_here=bound_now,
            surface_association=surface_association,
        )
    except Exception as exc:
        return skill_error_from_exception(
            exc,
            message="Skin weight transfer failed",
            prompt="Confirm the source mesh is skinned and both meshes share a joint namespace.",
        )


if __name__ == "__main__":
    run_main(main)
