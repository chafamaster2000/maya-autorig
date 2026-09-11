"""One call from corrected markers to a verified, skinned rig, with evidence.

The only human step in the pipeline is moving the marker locators; everything
after that is deterministic and re-runnable, so it lives in one orchestrator:

    fit_from_markers -> checkpoint(fit) -> build_rig -> checkpoint(build)
        -> bind_skin -> verify_skin -> checkpoint(skin)

Every stage writes its JSON (and renders) into `evidence_dir`; `summary.json`
there is the pass/fail ledger. A failing stage stops the run and leaves the
scene as-is for inspection, the checkpoint of the previous stage being the
recovery point (`checkpoint.reopen`).

Use `markers.propose` first (once), let the user drag the locators, then
call `run`. `start_from="fit"` reopens the fit checkpoint and redoes
everything after it, which is how a heuristic fix gets re-validated.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import maya.cmds as cmds

import autorig_common as ac
import bind_skin
import build_rig
import checkpoint
import attach_props
import fit_from_markers
import markers
import pose_gallery
import verify_skin
import review

STAGES = ("fit", "build", "bind", "props", "skin")


def run(mesh: str = "Mesh", template: str = "bipedBendy.ma", fingers: Any = "auto",
        evidence_dir: Optional[str] = None, start_from: str = "fit",
        bind_method: str = "geodesic", skin_method: str = "linear",
        max_influences: int = 4, stop_on_fail: bool = True, gallery: bool = True, **_kw) -> Dict[str, Any]:
    ev = ac.Evidence(evidence_dir)
    evidence_dir = ev.dir
    ledger: List[Dict[str, Any]] = []
    out: Dict[str, Any] = {"mesh": mesh, "evidence_dir": evidence_dir, "stages": ledger}

    def stage(name: str, fn, **kw) -> Dict[str, Any]:
        t0 = time.time()
        try:
            res = fn(**kw)
            passed = bool(res.get("passed", res.get("success", True)))
            err = None
        except Exception as exc:  # noqa: BLE001 - reported in the ledger, then re-raised via stop
            res, passed, err = {"error": "{}: {}".format(type(exc).__name__, exc)}, False, str(exc)
        entry = {"stage": name, "passed": passed, "seconds": round(time.time() - t0, 2), "error": err}
        ledger.append(entry)
        if not name.startswith(("checkpoint_", "reopen_")):
            ev.record(name, passed, res,
                      images=[r["path"] for r in _renders(res) if r.get("ok")],
                      expectation=_expectation(res))
        if not passed and stop_on_fail:
            out["stopped_at"] = name
            out["passed"] = False
            raise _Stop()
        return res

    try:
        # Every stage restarts from the checkpoint that precedes it.
        previous = {"fit": "fit", "build": "fit", "bind": "build"}.get(start_from)
        if previous and (start_from != "fit" or cmds.objExists("Group")):
            stage("reopen_" + previous, checkpoint.reopen, suffix=previous)
        # compact=False: the harness needs the full dicts (renders, expectation)
        # to record evidence; only its own return value is slim.
        if start_from in ("fit",):
            stage("fit", fit_from_markers.main, mesh=mesh, template=template, fingers=fingers,
                  evidence_dir=evidence_dir, compact=False)
            stage("checkpoint_fit", checkpoint.main, suffix="fit")
        if start_from in ("fit", "build"):
            stage("build", build_rig.main, mesh=mesh, evidence_dir=evidence_dir, compact=False)
            stage("checkpoint_build", checkpoint.main, suffix="build")
        if start_from in ("fit", "build", "bind"):
            stage("bind", bind_skin.main, mesh=mesh, bind_method=bind_method, skin_method=skin_method,
                  max_influences=max_influences, replace_existing=True, compact=False)
            # Props ride a single bone; nothing to do when the scene has none.
            stage("props", attach_props.main, mesh=mesh, evidence_dir=evidence_dir, compact=False)
        stage("skin", verify_skin.main, mesh=mesh, evidence_dir=evidence_dir, compact=False)
        stage("checkpoint_skin", checkpoint.main, suffix="skin")
        # The person's part is over here: the markers are spent, the rig is
        # bound and verified. X-ray off, mesh selectable, markers hidden. The
        # pose gallery below is a report -- a pose that reads badly is fixed
        # in weights or heuristics, never by re-dragging markers -- so it does
        # not keep the mode. A failed stage above does: the markers stay at
        # hand for a re-drag and a re-run from `fit`.
        out["marker_mode"] = markers.exit_mode(mesh)
        if gallery:
            stage("pose_gallery", pose_gallery.main, mesh=mesh, evidence_dir=evidence_dir, compact=False)
        out["passed"] = all(e["passed"] for e in ledger)
    except _Stop:
        pass
    out["summary"] = ev.summary_path
    out["scene"] = cmds.file(query=True, sceneName=True)
    # One review per run: everything the reviewer needs in one manifest, and
    # whether a review is needed at all (unchanged content + last PASS -> skip).
    try:
        m = review.manifest(evidence_dir)
        out["review"] = {"manifest": m.get("manifest"), "image_count": m["image_count"],
                         "content_hash": m["content_hash"], "skip_review": m["skip_review"]}
    except Exception as exc:  # noqa: BLE001 - the manifest must never fail a passed run
        out["review"] = {"error": str(exc)}
    return out


def _renders(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = res.get("renders")
    if r is None and isinstance(res.get("verify"), dict):
        r = res["verify"].get("renders")
    return r or []


def _expectation(res: Dict[str, Any]) -> str:
    """fit and build keep their expectation under the nested verify result;
    recording "" for them left the reviewer with no brief for those images."""
    exp = res.get("expectation")
    if not exp and isinstance(res.get("verify"), dict):
        exp = res["verify"].get("expectation")
    return exp or ""


class _Stop(Exception):
    pass
