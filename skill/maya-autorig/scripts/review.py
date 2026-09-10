"""One visual review per run, not one per stage -- and none when nothing changed.

The renders are the only thing a script cannot judge, so they go to a reviewer
subagent. That was the dominant cost of a run: ~55k tokens per review, seven
reviews per run (one per stage, plus re-reviews after every tweak), with the
cost fixed per call rather than per image. Two levers, both here:

  * `manifest` collects every render of a run with its stage's expectation
    into ONE file, so the reviewer is called once with all images.
  * It compares what determines the pictures (fit joint positions, bind
    settings) with the last verdict recorded by `mark`; if nothing moved
    beyond a tolerance and the last verdict was PASS, `skip_review` is true
    and the agent does not review at all. A tolerance, not a hash: identical
    reruns still differ by float noise, and a real marker drag is >= 1 cm.

Pure Python, no Maya: it only reads the evidence JSON the stages wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

REVIEWER_MODEL = "sonnet"
POSITION_TOL_CM = 0.75   # below this, a joint "did not move" for review purposes
BIND_KEYS = ("bind_method_used", "skin_method", "max_influences", "influence_count")
INSTRUCTIONS = (
    "Review ALL the images listed in this manifest in ONE pass. For each stage, judge the "
    "images against that stage's `expectation` text and answer PASS or FAIL with one short "
    "reason. Do not describe the images; do not return them. End with a single line "
    "'OVERALL: PASS' or 'OVERALL: FAIL (reasons)'. Use the cheapest capable model ({})."
).format(REVIEWER_MODEL)
SLIM_KEYS = ("evidence_dir", "content_hash", "all_stages_passed", "image_count",
             "skip_review", "max_position_delta_cm", "position_tol_cm",
             "reviewer_model", "render", "manifest")


def _load(path: str) -> Any:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _marker_path(evidence_dir: str) -> str:
    # One marker per scene tag, next to its run directories.
    return os.path.join(os.path.dirname(os.path.abspath(evidence_dir)), "last_reviewed.json")


def _content(evidence_dir: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """What drives the renders: fit joint positions + bind settings."""
    fit = _load(os.path.join(evidence_dir, "fit.json")) or {}
    bind = _load(os.path.join(evidence_dir, "bind.json")) or {}
    return fit.get("positions") or None, {k: bind.get(k) for k in BIND_KEYS}


def content_hash(evidence_dir: str) -> Optional[str]:
    """Informational fingerprint of the content (exact). None without a fit."""
    positions, bind = _content(evidence_dir)
    if not positions:
        return None
    blob = json.dumps({"positions": positions, "bind": bind}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def _unchanged(last: Optional[Dict[str, Any]], positions: Optional[Dict[str, Any]],
               bind: Dict[str, Any], h: Optional[str]) -> Tuple[bool, Optional[float]]:
    """Did the content stay put since the last verdict? Positions compare with
    a tolerance; bind settings must match exactly."""
    if not last or not positions:
        return False, None
    lp = last.get("positions")
    if not lp:  # marker from before positions were stored: exact hash only
        return bool(h) and last.get("content_hash") == h, None
    if set(lp) != set(positions) or last.get("bind") != bind:
        return False, None
    delta = max(abs(float(a) - float(b)) for k in positions for a, b in zip(positions[k], lp[k]))
    return delta < POSITION_TOL_CM, round(delta, 3)


def _stage_expectation(evidence_dir: str, stage: str) -> str:
    """Fallback brief for a stage whose summary entry has none (older runs
    recorded "" for fit/build, whose text lives under the nested verify)."""
    data = _load(os.path.join(evidence_dir, stage + ".json")) or {}
    exp = data.get("expectation")
    if not exp and isinstance(data.get("verify"), dict):
        exp = data["verify"].get("expectation")
    return exp or ""


def _render_info(evidence_dir: str, stages: Sequence[str]) -> Optional[Dict[str, Any]]:
    """The saved render scale, read from the first render that recorded it:
    size, scale against the 700x900 reference, and cm per pixel. With it a
    pixel measurement on a review image maps back to cm, and a larger
    re-render is one `render_evidence(width=700, height=900)` call away."""
    for stage in stages:
        data = _load(os.path.join(evidence_dir, stage + ".json")) or {}
        renders = data.get("renders") or (data.get("verify") or {}).get("renders") or []
        if isinstance(data.get("render"), dict):
            renders = [data["render"]] + list(renders)
        for r in renders:
            if isinstance(r, dict) and r.get("width"):
                return {"width": r["width"], "height": r.get("height"),
                        "render_scale": r.get("render_scale"), "cm_per_px": r.get("cm_per_px"),
                        "rerender_full": "render_evidence(..., width=700, height=900)"}
    return None


def manifest(evidence_dir: str, stages: Sequence[str] = (), compact: bool = True,
             **_kw) -> Dict[str, Any]:
    """Collect every render of the run with its expectation; decide if a review
    is even needed. Writes <evidence_dir>/review_manifest.json.

    `compact` (default) returns only the verdict-relevant fields plus the
    manifest path: the image list is for the reviewer subagent, which reads
    the file, not for the main context."""
    summary = _load(os.path.join(evidence_dir, "summary.json")) or []
    wanted = set(stages or ())
    items: List[Dict[str, Any]] = []
    for e in summary:
        if wanted and e.get("stage") not in wanted:
            continue
        if not e.get("images"):
            continue
        items.append({"stage": e["stage"], "passed": bool(e.get("passed")),
                      "images": list(e["images"]),
                      "expectation": e.get("expectation") or _stage_expectation(evidence_dir, e["stage"])})
    positions, bind = _content(evidence_dir)
    h = content_hash(evidence_dir)
    last = _load(_marker_path(evidence_dir))
    unchanged, delta = _unchanged(last, positions, bind, h)
    all_passed = bool(summary) and all(bool(e.get("passed")) for e in summary)
    skip = unchanged and all_passed and (last or {}).get("verdict") == "PASS"
    out: Dict[str, Any] = {
        "evidence_dir": evidence_dir,
        "content_hash": h,
        "all_stages_passed": all_passed,
        "image_count": sum(len(i["images"]) for i in items),
        "stages": items,
        "last_reviewed": {k: v for k, v in (last or {}).items() if k != "positions"} or None,
        "max_position_delta_cm": delta,
        "position_tol_cm": POSITION_TOL_CM,
        "skip_review": skip,
        "reviewer_model": REVIEWER_MODEL,
        "render": _render_info(evidence_dir, [i["stage"] for i in items]),
        "instructions": INSTRUCTIONS,
    }
    path = os.path.join(evidence_dir, "review_manifest.json")
    try:
        with open(path, "w") as fh:
            json.dump(out, fh, indent=2)
        out["manifest"] = path
    except OSError:
        pass
    if compact:
        return {k: out[k] for k in SLIM_KEYS if k in out}
    return out


def mark(evidence_dir: str, verdict: str = "PASS", notes: str = "", **_kw) -> Dict[str, Any]:
    """Record the reviewer's verdict with the content it judged (positions and
    bind settings), so the next run can tell whether anything moved."""
    verdict = verdict.upper()
    if verdict not in ("PASS", "FAIL"):
        raise ValueError("verdict must be PASS or FAIL")
    positions, bind = _content(evidence_dir)
    data = {"content_hash": content_hash(evidence_dir), "verdict": verdict, "notes": notes,
            "evidence_dir": evidence_dir, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "positions": positions, "bind": bind}
    path = _marker_path(evidence_dir)
    with open(path, "w") as fh:
        json.dump(data, fh, indent=2)
    # The judged content is stored for the next comparison, not echoed back:
    # 22 joints x 3 floats is ~800 tokens the caller has no use for.
    slim = {k: v for k, v in data.items() if k not in ("positions", "bind")}
    slim["marker"] = path
    slim["joints_stored"] = len(positions or {})
    return slim


# --------------------------------------------------------------------------- #
# Gauntlet critic: blind A/B of our pose renders against the bar's
# --------------------------------------------------------------------------- #
CRITIC_INSTRUCTIONS = (
    "You are the critic of a rigging gauntlet. Each pair below shows two DIFFERENT characters "
    "in the SAME pose, one rigged and skinned by an artist, one by an auto-rig; you are not told "
    "which is which and the characters' looks are irrelevant. Judge ONLY rigging and skinning: "
    "does the pose read as described, do joints bend in plausible places, is the skin smooth "
    "(no pinching, tearing, collapsed volume, candy-wrapper twist, body parts dragged along). "
    "For every pair answer exactly one of A, B or tie. A tie is allowed only when, after comparing both "
    "images carefully, you find no difference in pose readability, joint placement or skin quality; if one "
    "reads the pose better or deforms more cleanly, pick it. Return "
    "ONLY a JSON object {{\"<pair id>\": \"A\"|\"B\"|\"tie\", ...}} and nothing else. Model: {}"
).format(REVIEWER_MODEL)


# Offline copy of pose_gallery.POSES[*]["reads_as"] (that module imports Maya).
POSE_READS = {
    "elbows_90": "both elbows flexed 90 degrees, forearms and hands swung forward (visible from the side)",
    "arms_up": "both hands raised above the head (IK)",
    "knee_lift_R": "right knee lifted and forward, foot off the floor, left leg planted (IK)",
    "crouch": "both feet planted, pelvis dropped, both knees bent (IK legs)",
    "torso_twist": "upper body twisted about the spine to one side, head turned the other way; hips and legs unchanged",
    "walk_step": "right foot forward, left foot back, arms swinging opposite (visible from the side)",
    "fist": "both hands closed into fists, fingers curled into the palm and the thumb folded over them; arms unchanged",
}


def critic_manifest(bar_dir: str, ours_dir: str, seed: int = 0, **_kw) -> Dict[str, Any]:
    """Pair every pose render of the bar with ours, shuffle which is A and
    which is B per pair, write <ours_dir>/critic_manifest.json (for the
    critic) and <ours_dir>/critic_key.json (the answer key, never shown to
    the critic). Pure: only lists files."""
    import random

    rnd = random.Random(seed)
    bar_prof = _load(os.path.join(bar_dir, "rig_profile.json")) or {}
    ours_prof = _load(os.path.join(ours_dir, "rig_profile.json")) or {}
    reads = dict(POSE_READS)
    try:
        import pose_gallery  # live descriptions when inside Maya
        reads.update({p["name"]: p["reads_as"] for p in pose_gallery.POSES})
    except Exception:  # noqa: BLE001 - offline: the copy above serves
        pass
    # Blind means blind: the critic gets anonymous copies (<id>_A.png,
    # <id>_B.png), never the source paths, which name the bar and the run.
    import shutil

    blind_dir = os.path.join(ours_dir, "critic")
    os.makedirs(blind_dir, exist_ok=True)
    pairs, key = [], {}
    for name in sorted(os.listdir(bar_dir)):
        if not (name.startswith("pose_") and name.endswith(".png")):
            continue
        ours = os.path.join(ours_dir, name)
        if not os.path.exists(ours):
            continue
        pose = name[len("pose_"):].rsplit("_", 1)[0]
        pid = name[len("pose_"):-4]
        a_is_bar = rnd.random() < 0.5
        a, b = (os.path.join(bar_dir, name), ours) if a_is_bar else (ours, os.path.join(bar_dir, name))
        a_blind, b_blind = os.path.join(blind_dir, pid + "_A.png"), os.path.join(blind_dir, pid + "_B.png")
        shutil.copy(a, a_blind)
        shutil.copy(b, b_blind)
        pairs.append({"id": pid, "pose": pose, "reads_as": reads.get(pose, ""), "A": a_blind, "B": b_blind})
        key[pid] = {"A": "bar" if a_is_bar else "ours", "B": "ours" if a_is_bar else "bar", "pose": pose}
    out = {"bar": bar_prof.get("label"), "ours": ours_prof.get("label"), "instructions": CRITIC_INSTRUCTIONS,
           "reviewer_model": REVIEWER_MODEL, "pairs": pairs}
    mpath, kpath = os.path.join(ours_dir, "critic_manifest.json"), os.path.join(ours_dir, "critic_key.json")
    with open(mpath, "w") as fh:
        json.dump(out, fh, indent=2)
    with open(kpath, "w") as fh:
        json.dump(key, fh, indent=2)
    return {"manifest": mpath, "key": kpath, "pairs": len(pairs), "reviewer_model": REVIEWER_MODEL}


def critic_verdicts(ours_dir: str, picks: Dict[str, str], **_kw) -> Dict[str, Any]:
    """Decode the critic's A/B/tie picks with the key into per-pose verdicts
    (ours | bar | tie); a pose with several views takes the worst view.
    Writes <ours_dir>/critic.json for rig_compare."""
    key = _load(os.path.join(ours_dir, "critic_key.json")) or {}
    per_pose: Dict[str, str] = {}
    rank = {"bar": 0, "tie": 1, "ours": 2}
    for pid, pick in picks.items():
        k = key.get(pid)
        if not k:
            continue
        pick = str(pick).strip().upper()
        verdict = "tie" if pick == "TIE" else k.get(pick, "tie")
        pose = k["pose"]
        per_pose[pose] = min(per_pose.get(pose, "ours"), verdict, key=lambda v: rank[v])
    path = os.path.join(ours_dir, "critic.json")
    with open(path, "w") as fh:
        json.dump(per_pose, fh, indent=2)
    wins = sum(1 for v in per_pose.values() if v in ("ours", "tie"))
    return {"critic": path, "verdicts": per_pose, "poses": len(per_pose), "not_worse_than_bar": wins}
