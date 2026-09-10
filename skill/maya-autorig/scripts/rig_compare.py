"""Hold one rig profile against the yardstick and print the grid.

The bar is the artist's rig, profiled with `rig_profile`: its measured
numbers are the thresholds, not numbers anyone made up. Every row says
what was measured, what the bar measured, what we measured, and a verdict.
Hard rows decide `wins`; INFO rows only explain. Pure Python: no Maya, so
it runs offline over two JSON files and in the unit tests.

Tolerances are deliberately *relative to the bar* (x1.25, +0.02): the
gauntlet loop only exits when the auto-rig is as good as the reference on
every hard row, never after N rounds.
"""
from __future__ import annotations

import json
import re
import os
from typing import Any, Dict, List, Optional

import rig_taxonomy as tx

TOL = {"ratio": 1.25, "volume_dev": 0.02, "bleed": 0.005, "moved_pct": 0.5, "symmetry": 0.05,
       "radius_ratio": 0.05, "reset": 0.01}


def _row(group: str, criterion: str, bar: Any, ours: Any, verdict: str, hard: bool = True, note: str = "") -> Dict[str, Any]:
    return {"group": group, "criterion": criterion, "bar": bar, "ours": ours, "verdict": verdict, "hard": hard, "note": note}


def _le(ours: Optional[float], limit: Optional[float]) -> str:
    if ours is None:
        return "FAIL"
    if limit is None:
        return "INFO"
    return "PASS" if ours <= limit else "FAIL"


def _ge(ours: Optional[float], limit: Optional[float]) -> str:
    if ours is None:
        return "FAIL"
    if limit is None:
        return "INFO"
    return "PASS" if ours >= limit else "FAIL"


def _find(items: List[Dict[str, Any]], key: str, value: str) -> Optional[Dict[str, Any]]:
    for it in items or []:
        if it.get(key) == value:
            return it
    return None


def _mesh_fingers(hand: Optional[Dict[str, Any]]) -> Optional[List[str]]:
    """Finger chains the mesh itself has (from fit_from_markers' hand
    report): None when unknown, [] for a mitten."""
    if not hand:
        return None
    if hand.get("kind") != "fingers":
        return ["Middle"]           # the mitten chain
    return list(hand.get("names") or [])


def compare(bar: Dict[str, Any], ours: Dict[str, Any], critic: Optional[Dict[str, str]] = None,
            hand: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    bs, os_ = bar["skeleton"], ours["skeleton"]
    mesh_fingers = _mesh_fingers(hand)

    # ---- A. skeleton: the generic humanoid under the artist's extras -------
    for g in tx.GROUPS:
        want, have = set(bs["core_bases"].get(g, [])), set(os_["core_bases"].get(g, []))
        missing, extra = sorted(want - have), sorted(have - want)
        note = ""
        if g == "hand" and mesh_fingers is not None:
            # A finger the mesh does not have is not a missing joint: the
            # rig matches the mesh, the bar's hand is the bar's mesh.
            absent = [m for m in missing if "Finger" in m and re.sub(r"Finger\d+$", "", m) not in mesh_fingers]
            if absent:
                missing = [m for m in missing if m not in absent]
                note = "mesh has {} finger lobes ({}); not in mesh: {}".format(
                    len(mesh_fingers), ", ".join(mesh_fingers), ", ".join(sorted({re.sub(r"\d+$", "", a) for a in absent})))
        if not note:
            note = ("missing " + ", ".join(missing)) if missing else ("also " + ", ".join(extra) if extra else "")
        rows.append(_row("skeleton", "core joints: " + g, len(want), len(have),
                         "PASS" if not missing else "FAIL", True, note))
    # Anatomy from deform + fit joints: AS keeps heel / foot sides in the
    # FitSkeleton only (they drive the foot roll, they never skin).
    bf = tx.features(bs["deform_joints"] + (bs.get("fit_joints") or []))
    of = tx.features(os_["deform_joints"] + (os_.get("fit_joints") or []))
    want_chains = len(bf["finger_chains"])
    chain_note = "{} vs {} finger joints".format(bf["finger_joints"], of["finger_joints"])
    if mesh_fingers is not None and len(mesh_fingers) < want_chains:
        want_chains = len(mesh_fingers)
        chain_note += "; mesh has {} lobes, the bar's mesh 5".format(len(mesh_fingers))
    rows.append(_row("skeleton", "finger chains", len(bf["finger_chains"]), len(of["finger_chains"]),
                     _ge(len(of["finger_chains"]), want_chains), True, chain_note))
    rows.append(_row("skeleton", "twist parts", bf["n_twist_parts"], of["n_twist_parts"],
                     _ge(of["n_twist_parts"], bf["n_twist_parts"]), True, ", ".join(bf["twist_parts"])))
    for flag in ("has_scapula", "has_cup", "has_eyes", "has_jaw", "has_heel", "has_foot_sides"):
        rows.append(_row("skeleton", flag.replace("has_", ""), bf[flag], of[flag],
                         "PASS" if (of[flag] or not bf[flag]) else "FAIL", True))
    rows.append(_row("skeleton", "optional anatomy (belly...)", bf["optional"], of["optional"], "INFO", False))
    rows.append(_row("skeleton", "extras (clothes, hair, props)", len(bf["extras"]), len(of["extras"]), "INFO", False,
                     "the target is the humanoid: extras are the artist's, not the bar"))
    rows.append(_row("skeleton", "deform joints total", bs["n_deform"], os_["n_deform"], "INFO", False))
    bc, oc = bar["controls"], ours["controls"]
    rows.append(_row("controls", "FK/IK switches", bc["fkik_switches"], oc["fkik_switches"],
                     "PASS" if set(bc["fkik_switches"]) <= set(oc["fkik_switches"]) else "FAIL", True))
    rows.append(_row("controls", "foot roll attrs on IKLeg", bc["foot_roll"], oc["foot_roll"],
                     "PASS" if (oc["foot_roll"] or not bc["foot_roll"]) else "FAIL", True))
    rows.append(_row("controls", "bendy controls", bc["bendy"], oc["bendy"], _ge(oc["bendy"], bc["bendy"]), True))
    want_fc = bc["fingers"]
    fc_note = ""
    if mesh_fingers is not None and bf["finger_chains"] and len(mesh_fingers) < len(bf["finger_chains"]):
        want_fc = int(bc["fingers"] * len(mesh_fingers) / float(len(bf["finger_chains"])))
        fc_note = "scaled to the mesh's {} chains: {}".format(len(mesh_fingers), want_fc)
    rows.append(_row("controls", "finger controls", bc["fingers"], oc["fingers"], _ge(oc["fingers"], want_fc), True, fc_note))
    for flag in ("eye_aim", "cup", "scapula"):
        rows.append(_row("controls", flag, bc[flag], oc[flag], "PASS" if (oc[flag] or not bc[flag]) else "FAIL", True))
    rows.append(_row("controls", "controls total", bc["count"], oc["count"], "INFO", False))

    # ---- B. skin -----------------------------------------------------------
    bk, ok_ = bar["skin"], ours["skin"]
    # The cluster's maxInfluences setting is not enforced unless
    # maintainMaxInfluences is on; the bar's mesh carries up to 11 with the
    # setting at 8. Compare what the vertices actually carry.
    bmax, omax = bk["influences_per_vertex"]["max"], ok_["influences_per_vertex"]["max"]
    rows.append(_row("skin", "max influences per vertex (actual)", bmax, omax, _le(omax, bmax), True,
                     "cluster setting {} vs {}".format(bk["max_influences"], ok_["max_influences"])))
    rows.append(_row("skin", "mean influences per vertex", bk["influences_per_vertex"]["mean"], ok_["influences_per_vertex"]["mean"], "INFO", False))
    ws = ok_["weight_sum"]
    rows.append(_row("skin", "weights normalised", "1.0", "{}..{}".format(ws["min"], ws["max"]),
                     "PASS" if abs(ws["min"] - 1) < 1e-3 and abs(ws["max"] - 1) < 1e-3 else "FAIL", True))
    # The artist's own skin leaves a few core joints unused (Shoulder_R drives
    # nothing on the bar: the twist parts carry it), so this is bar-relative too.
    rows.append(_row("skin", "unused core joints", len(bk["unused_core_joints"]), len(ok_["unused_core_joints"]),
                     _le(len(ok_["unused_core_joints"]), len(bk["unused_core_joints"])), True, ", ".join(ok_["unused_core_joints"][:8])))
    # A head joint placed in a hat or a hairdo leaves the face to the neck:
    # the head must own more skin than the neck, as it does on the bar.
    def _dom(sk, name):
        for c in sk.get("coverage", []):
            if c["joint"] == name:
                return c["dominant"]
        return 0
    bh, bn, oh, on = _dom(bk, "Head_M"), _dom(bk, "Neck_M"), _dom(ok_, "Head_M"), _dom(ok_, "Neck_M")
    rows.append(_row("skin", "head owns more skin than neck", "{} vs {}".format(bh, bn), "{} vs {}".format(oh, on),
                     "PASS" if (oh > on or bh <= bn) else "FAIL", True, "hat/beard/hair fused to the head push the head joint up"))
    bl, ol = bk.get("locality") or {}, ok_.get("locality") or {}
    rows.append(_row("skin", "locality p95 (frac H)", bl.get("p95_frac_H"), ol.get("p95_frac_H"),
                     _le(ol.get("p95_frac_H"), (bl.get("p95_frac_H") or 0) * TOL["ratio"]), True, "dominant weight near its bone"))
    rows.append(_row("skin", "locality median (frac H)", bl.get("median_frac_H"), ol.get("median_frac_H"), "INFO", False))
    bsy, osy = bk["symmetry"], ok_["symmetry"]
    rows.append(_row("skin", "L/R symmetry L1 mean", bsy["l1_mean"], osy["l1_mean"],
                     _le(osy["l1_mean"], (bsy["l1_mean"] or 0) + TOL["symmetry"]), True,
                     "{} mirrored pairs".format(osy["pairs"])))
    bsm, osm = bk["smoothness"], ok_["smoothness"]
    rows.append(_row("skin", "smoothness L1 p95 (per edge)", bsm["l1_p95"], osm["l1_p95"],
                     _le(osm["l1_p95"], bsm["l1_p95"] * TOL["ratio"]), True))
    rows.append(_row("skin", "smoothness L1 max", bsm["l1_max"], osm["l1_max"], "INFO", False))

    # ---- C. deformation ----------------------------------------------------
    bd, od = bar.get("deformation") or {}, ours.get("deformation") or {}
    for bt in bd.get("bends", []):
        ot = _find(od.get("bends", []), "control", bt["control"]) or {}
        name = bt["control"]
        rows.append(_row("deform", name + " bleed outside limb (frac H)", bt.get("rest_max_disp_frac_H"), ot.get("rest_max_disp_frac_H"),
                         _le(ot.get("rest_max_disp_frac_H"), (bt.get("rest_max_disp_frac_H") or 0) + TOL["bleed"]), True))
        rows.append(_row("deform", name + " rest vertices moved (%)", bt.get("rest_moved_pct"), ot.get("rest_moved_pct"),
                         _le(ot.get("rest_moved_pct"), (bt.get("rest_moved_pct") or 0) + TOL["moved_pct"]), True))
        rows.append(_row("deform", name + " edge strain p99", bt.get("strain_p99"), ot.get("strain_p99"),
                         _le(ot.get("strain_p99"), (bt.get("strain_p99") or 0) * TOL["ratio"]), True))
        rows.append(_row("deform", name + " volume ratio", bt.get("volume_ratio"), ot.get("volume_ratio"),
                         _le(abs((ot.get("volume_ratio") or 9) - 1), abs((bt.get("volume_ratio") or 1) - 1) + TOL["volume_dev"]), True))
        rows.append(_row("deform", name + " limb moved (frac H)", bt.get("limb_max_disp_frac_H"), ot.get("limb_max_disp_frac_H"),
                         "PASS" if (ot.get("limb_max_disp_frac_H") or 0) > 0.05 else "FAIL", True, "the limb must actually bend"))
    btw, otw = bd.get("twist") or {}, od.get("twist") or {}
    if btw:
        rows.append(_row("deform", "wrist twist 90: forearm radius ratio (min)", btw.get("radius_ratio_min"), otw.get("radius_ratio_min"),
                         _ge(otw.get("radius_ratio_min"), (btw.get("radius_ratio_min") or 1) - TOL["radius_ratio"]), True,
                         "candy-wrapper pinch without twist joints"))
        rows.append(_row("deform", "wrist twist 90: edge strain p99", btw.get("strain_p99"), otw.get("strain_p99"),
                         _le(otw.get("strain_p99"), (btw.get("strain_p99") or 0) * TOL["ratio"]), True))
    for bp in bd.get("poses", []):
        op = _find(od.get("poses", []), "name", bp["name"]) or {}
        n = bp["name"]
        rows.append(_row("deform", n + " volume ratio", bp.get("volume_ratio"), op.get("volume_ratio"),
                         _le(abs((op.get("volume_ratio") or 9) - 1), abs((bp.get("volume_ratio") or 1) - 1) + TOL["volume_dev"]), True))
        rows.append(_row("deform", n + " edge strain p99", bp.get("strain_p99"), op.get("strain_p99"),
                         _le(op.get("strain_p99"), (bp.get("strain_p99") or 0) * TOL["ratio"]), True))
        rows.append(_row("deform", n + " edge compression p01", bp.get("compress_p01"), op.get("compress_p01"),
                         _ge(op.get("compress_p01"), (bp.get("compress_p01") or 0) / TOL["ratio"]), True))
        rows.append(_row("deform", n + " reset residual (cm)", bp.get("reset_residual"), op.get("reset_residual"),
                         _le(op.get("reset_residual"), TOL["reset"]), True))
        rows.append(_row("deform", n + " vertices moved (%)", bp.get("moved_vertices_pct"), op.get("moved_vertices_pct"), "INFO", False,
                         "missing controls: " + ", ".join(op.get("missing_controls", [])) if op.get("missing_controls") else ""))

    # ---- D. blind A/B by the critic ---------------------------------------
    for bp in bd.get("poses", []):
        n = bp["name"]
        v = (critic or {}).get(n)
        rows.append(_row("critic", n + " blind A/B vs bar", "bar", v or "pending",
                         "PASS" if v in ("ours", "tie") else ("FAIL" if v == "bar" else "INFO"), v is not None,
                         "a fresh-context critic picks the better render without knowing which is which"))

    hard = [r for r in rows if r["hard"]]
    failed = [r for r in hard if r["verdict"] != "PASS"]
    by_group: Dict[str, Dict[str, int]] = {}
    for r in hard:
        g = by_group.setdefault(r["group"], {"pass": 0, "fail": 0})
        g["pass" if r["verdict"] == "PASS" else "fail"] += 1
    return {"bar": bar.get("label"), "ours": ours.get("label"), "rows": rows,
            "hard_total": len(hard), "hard_passed": len(hard) - len(failed),
            "score": round((len(hard) - len(failed)) / float(len(hard)), 3) if hard else None,
            "by_group": by_group, "wins": not failed,
            "failed": [{"group": r["group"], "criterion": r["criterion"], "bar": r["bar"], "ours": r["ours"], "note": r["note"]} for r in failed]}


def to_markdown(result: Dict[str, Any]) -> str:
    lines = ["# Gauntlet: `{}` vs bar `{}`".format(result["ours"], result["bar"]), "",
             "**{}** hard rows: {}/{} pass ({:.0%}).".format("WINS" if result["wins"] else "LOSES",
                                                              result["hard_passed"], result["hard_total"], result["score"] or 0), ""]
    lines += ["| group | criterion | bar | ours | verdict | note |", "|---|---|---|---|---|---|"]
    for r in result["rows"]:
        mark = {"PASS": "✅", "FAIL": "❌", "INFO": "·"}[r["verdict"]]
        lines.append("| {} | {} | {} | {} | {} {} | {} |".format(
            r["group"], r["criterion"], _fmt(r["bar"]), _fmt(r["ours"]), mark, r["verdict"] if r["hard"] else "info", r["note"]))
    return "\n".join(lines) + "\n"


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return "{:.4g}".format(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v) if v else "–"
    return str(v)


BARS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bars")


def resolve_bar(bar: str) -> str:
    """A bar is a name (bars/<name>_profile.json next to the skill) or a path
    to any rig_profile.json. No bar ships with the skill: a yardstick is a
    rig you trust, so profile one of your own with
    rig_profile(save_as_bar=true) and it becomes available by its label."""
    if os.path.isfile(bar):
        return bar
    cand = os.path.join(BARS_DIR, "{}_profile.json".format(bar))
    if os.path.isfile(cand):
        return cand
    raise FileNotFoundError(
        "no bar named {!r}. A yardstick is a rig you trust: open one and run "
        "rig_profile(save_as_bar=true, label={!r}), which writes {}. "
        "Bars available now: {}".format(bar, bar, cand, list_bars() or "none"))


def list_bars() -> List[str]:
    if not os.path.isdir(BARS_DIR):
        return []
    return sorted(f[:-len("_profile.json")] for f in os.listdir(BARS_DIR) if f.endswith("_profile.json"))


def main(bar_path: str = "", ours_path: str = "", out_path: Optional[str] = None, critic_path: Optional[str] = None,
         fit_path: Optional[str] = None, **_kw) -> Dict[str, Any]:
    """`bar_path`: the yardstick, by label or path. `fit_path`: the run's
    fit.json; its hand report tells which finger chains the mesh has, so a
    four-lobe glove is not scored as a missing pinky."""
    if not bar_path:
        raise ValueError("bar_path is required: name a bar in bars/ or pass a path to a rig_profile.json")
    bar_path = resolve_bar(bar_path)
    if not ours_path:
        raise ValueError("ours_path: the rig_profile.json to hold against the bar")
    with open(bar_path) as fh:
        bar = json.load(fh)
    with open(ours_path) as fh:
        ours = json.load(fh)
    critic = None
    if critic_path:
        with open(critic_path) as fh:
            critic = json.load(fh)
    hand = None
    if fit_path and os.path.exists(fit_path):
        with open(fit_path) as fh:
            hand = json.load(fh).get("hand")
    res = compare(bar, ours, critic, hand=hand)
    if out_path:
        with open(out_path, "w") as fh:
            json.dump(res, fh, indent=1)
        with open(out_path[:-5] + ".md" if out_path.endswith(".json") else out_path + ".md", "w") as fh:
            fh.write(to_markdown(res))
        res["out_path"] = out_path
        res["grid_md"] = out_path[:-5] + ".md" if out_path.endswith(".json") else out_path + ".md"
    res["bar_path"] = bar_path
    return res
