"""Mixamo-style marker proposal and fit-joint derivation from raw vertices.

Pure numpy, no Maya imports, so every heuristic here can be exercised offline
against a vertex dump and unit-tested. Conventions (Maya defaults): Y up, the
character faces +Z, the character's LEFT is +X. AdvancedSkeleton's FitSkeleton
holds only the RIGHT side (-X) and mirrors on build, so derive_fit_positions
returns every side joint on -X.

The 8 Mixamo markers are the contract: chin, groin, wrist/elbow/knee per side.
Everything else in the fit skeleton is derived from those plus the mesh.
Optional markers (shoulder, ankle) override their derivation when present.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

Vec = List[float]

MIXAMO_MARKERS = (
    "chin", "groin",
    "wrist_L", "wrist_R", "elbow_L", "elbow_R", "knee_L", "knee_R",
)
OPTIONAL_MARKERS = ("shoulder_L", "shoulder_R", "ankle_L", "ankle_R")

# Joints of bipedGame.ma that stay when fingers are dropped, hierarchy order.
BODY_FIT_JOINTS = (
    "Root", "Spine1", "Spine2", "Chest", "Neck", "Head", "HeadEnd", "Eye", "EyeEnd",
    "Jaw", "JawEnd", "Scapula", "Shoulder", "Elbow", "Wrist", "WristEnd",
    "Hip", "Knee", "Ankle", "Heel", "Toes", "ToesEnd", "FootSideInner", "FootSideOuter",
)
# Present in one template but not the other: bipedGame has Spine2 and no foot
# sides; bipedBendy (the artist's yardstick) has Root/Spine1/Chest plus
# inbetween joints, and FootSideInner/Outer for the foot roll.
OPTIONAL_FIT_JOINTS = {"Spine2", "FootSideInner", "FootSideOuter"}


# --------------------------------------------------------------------------- #
# slicing helpers
# --------------------------------------------------------------------------- #
def _slice(V: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return V[(V[:, 1] >= lo) & (V[:, 1] < hi)]


def _gap_spanning_zero(xs: np.ndarray) -> float:
    """Size of the empty interval in sorted xs that contains x=0 (0 if filled)."""
    if len(xs) < 2:
        return 0.0
    k = int(np.searchsorted(xs, 0.0))
    if k <= 0 or k >= len(xs):
        return 0.0
    return float(xs[k] - xs[k - 1])


def _outer_cluster(P: np.ndarray, side: int, min_gap: float) -> Optional[np.ndarray]:
    """Points of a slice beyond the largest x-gap on one side, if that gap is wide.

    In A/T pose the arm is separated from the torso by empty space in each
    horizontal slice; the outermost cluster past that gap is the arm.
    """
    Q = P[P[:, 0] * side > 0]
    if len(Q) < 2:
        return None
    order = np.argsort(np.abs(Q[:, 0]))
    Q = Q[order]
    gaps = np.diff(np.abs(Q[:, 0]))
    k = int(gaps.argmax())
    if gaps[k] < min_gap:
        return None
    return Q[k + 1:]


def _point_spacing(V: np.ndarray, y0: float, dy: float, slices: int, min_pts: int = 6) -> float:
    """Mesh resolution as a length: median gap between x-sorted points of a
    horizontal slice, over all slices. Every "is this empty space or just a
    sparse patch" decision is scaled by it."""
    med = []
    for i in range(slices):
        P = _slice(V, y0 + i * dy, y0 + (i + 1) * dy)
        if len(P) >= min_pts:
            d = np.diff(np.sort(P[:, 0]))
            d = d[d > 1e-6]
            if len(d):
                med.append(float(np.median(d)))
    return float(np.median(med)) if med else 0.0


def _gap_threshold(spacing: float, H: float, k: float, floor_frac: float, cap_frac: float) -> float:
    """A gap counts as empty space only if it is `k` point spacings wide --
    resolution-aware instead of a fraction of body height. Clipped to
    [floor_frac, cap_frac] * H: never stricter than the old height fraction on
    a sparse mesh (cap), and able to see a thin arm hanging close to the torso
    on a dense one (floor)."""
    return float(np.clip(k * spacing, floor_frac * H, cap_frac * H))


# --------------------------------------------------------------------------- #
# body analysis
# --------------------------------------------------------------------------- #
def analyze_body(V: np.ndarray, slices: int = 100) -> Dict:
    """Global landmarks that every marker proposal hangs off."""
    V = np.asarray(V, dtype=float)
    y0, y1 = float(V[:, 1].min()), float(V[:, 1].max())
    H = y1 - y0
    dy = H / slices
    body: Dict = {"height": H, "y_floor": y0, "y_top": y1, "slice": dy}
    spacing = _point_spacing(V, y0, dy, slices)
    gaps = {"crotch": _gap_threshold(spacing, H, 3.0, 0.008, 0.03),
            "arm": _gap_threshold(spacing, H, 5.0, 0.012, 0.05)}
    body["spacing"] = round(spacing, 3)
    body["gap_thresholds"] = {k: round(v, 2) for k, v in gaps.items()}

    # Crotch: lowest slice above the shins whose center (x=0) is filled.
    crotch = None
    for i in range(int(slices * 0.15), slices):
        lo = y0 + i * dy
        P = _slice(V, lo, lo + dy)
        if len(P) < 4:
            continue
        if _gap_spanning_zero(np.sort(P[:, 0])) < gaps["crotch"]:
            crotch = lo + dy * 0.5
            break
    body["crotch_y"] = crotch

    # Arms: outer clusters separated from the torso, per side; armpit = highest
    # slice where that separation still exists.
    body["arms"] = {}
    for label, side in (("L", 1), ("R", -1)):
        # Walk bottom-up collecting every contiguous run of slices with a
        # separated outer cluster. The feet of a character with hanging arms
        # make a run of their own (a shoe sticks out past the shin), then the
        # legs' outer edge, then the arm: each run is validated on its own and
        # the lowest VALID run is the arm. Taking only the first run put the
        # "arm" of a stylised short character on its boots.
        runs: List[Dict] = []
        pts, armpit, misses, prev_x = [], None, 0, None
        for i in range(slices + 1):
            lo = y0 + i * dy
            P = _slice(V, lo, lo + dy) if i < slices else np.zeros((0, 3))
            C = _outer_cluster(P, side, gaps["arm"]) if len(P) else None
            # A real arm slice has a few points and continues the previous
            # slice in x; a spurious gap in a sparse slice does neither.
            ok = C is not None and len(C) >= 3
            if ok and prev_x is not None:
                ok = abs(float(np.abs(C[:, 0]).mean()) - prev_x) < 0.08 * H
            if ok:
                pts.append(C)
                prev_x = float(np.abs(C[:, 0]).mean())
                armpit = lo + dy
                misses = 0
            elif pts:
                misses += 1
                if misses > 2 or i >= slices:  # low-poly meshes leave the odd empty slice
                    if len(pts) >= 3:
                        runs.append({"pts": pts, "armpit": armpit})
                    pts, prev_x, misses = [], None, 0
        chosen = None
        for run in runs:
            A = np.vstack(run["pts"])
            c = A.mean(0)
            _u, _s, vt = np.linalg.svd(A - c, full_matrices=False)
            axis = vt[0]
            if axis[1] > 0:
                axis = -axis  # point from shoulder toward the hand
            t = (A - c) @ axis
            span = float(t.max() - t.min())
            # An A/T/hanging arm lies in the XY plane and spans a decent
            # fraction of the height; a run that merges into a wide body
            # (coat, belly) stops early and its principal axis is meaningless
            # (it once pointed along Z and put a shoulder 3 m in front).
            reason = None
            if span < 0.22 * H:
                reason = "run too short ({:.0f} cm, {:.2f} H)".format(span, span / H)
            elif abs(float(axis[2])) > 0.7:
                reason = "axis along Z (|z| = {:.2f}); not an arm in the XY plane".format(abs(float(axis[2])))
            if reason is None and chosen is None:
                # Last check: extrapolating this run to a shoulder must land
                # inside the body. Arms that merge into a wide coat (the
                # chibis) produce a long, plausible-looking run whose shoulder
                # comes out 30 cm behind the back; that is not an arm we can
                # trust, the markers carry the pose instead.
                cand = {"points": A, "centroid": c, "axis": axis, "t": t, "armpit_y": run["armpit"],
                        "t_min": float(t.min()), "t_max": float(t.max())}
                try:
                    sh = _shoulder_from_arm(V, body, cand)
                    lo, hi = V.min(axis=0), V.max(axis=0)
                    if not (lo[2] <= sh[2] <= hi[2] and lo[0] <= sh[0] <= hi[0]):
                        reason = "shoulder extrapolated outside the body (x={:.0f}, z={:.0f})".format(sh[0], sh[2])
                except Exception as exc:  # noqa: BLE001 - a degenerate run is a rejected run
                    reason = "shoulder extrapolation failed ({})".format(type(exc).__name__)
            run.update({"A": A, "c": c, "axis": axis, "t": t, "span": span, "reason": reason})
            if reason is None and chosen is None:
                chosen = run
        if chosen is None:
            body["arms"][label] = None
            rejected = [r for r in runs if "reason" in r]
            if rejected:
                body.setdefault("arm_rejected", {})[label] = "; ".join(r["reason"] for r in rejected)
                # A short separated run is still worth a hint (the hand of an
                # arm hanging by a wide coat) -- but only at arm height: a
                # boot sticking out at the floor is not a hand.
                plausible = [r for r in rejected if y0 + 0.25 * H < float(r["A"][:, 1].mean()) < y0 + 0.85 * H]
                if plausible:
                    r = max(plausible, key=lambda r: len(r["A"]))
                    A = r["A"]
                    body.setdefault("arm_hint", {})[label] = {
                        "centroid": r["c"], "y_top": float(A[:, 1].max()), "y_bottom": float(A[:, 1].min()),
                        "x_mean": float(A[:, 0].mean()), "n": int(len(A))}
            continue
        A, c, axis, t = chosen["A"], chosen["c"], chosen["axis"], chosen["t"]
        angle = math.degrees(math.atan2(abs(axis[0]), abs(axis[1])))
        body["arms"][label] = {
            "points": A, "centroid": c, "axis": axis, "t": t,
            "armpit_y": chosen["armpit"],
            "angle_from_vertical_deg": angle,
            "t_min": float(t.min()), "t_max": float(t.max()),
            "runs_seen": len(runs),
        }

    angles = [a["angle_from_vertical_deg"] for a in body["arms"].values() if a]
    if not angles:
        pose = "arms_down_or_undetected"
    else:
        mean = sum(angles) / len(angles)
        pose = "T" if mean > 70 else "A" if mean > 15 else "arms_down"
    body["pose"] = pose
    body["arm_angle_deg"] = round(sum(angles) / len(angles), 1) if angles else None

    xs = V[:, 0]
    body["armspan_over_height"] = float((xs.max() - xs.min()) / H)
    return body


def _arm_profile(arm: Dict, step: float) -> List[Tuple[float, np.ndarray, float]]:
    """(t, centroid, mean radius) along the arm axis, proximal to distal."""
    A, axis, t = arm["points"], arm["axis"], arm["t"]
    proj = np.eye(3) - np.outer(axis, axis)
    rows = []
    for tb in np.arange(t.min(), t.max(), step):
        m = (t >= tb) & (t < tb + step)
        if m.sum() < 3:
            continue
        Q = A[m]
        c = Q.mean(0)
        # Geometric mean of the two cross-section extents: a flat palm is wide
        # in one direction and thin in the other, a wrist is thin in both.
        # Width of the cross-section (largest extent). A wrist is narrow in
        # every direction; a palm is wide one way; spread fingers span the
        # palm width even though each finger is thin.
        D = (Q - c) @ proj
        cov = np.cov(D.T) if len(Q) > 3 else np.eye(3) * 1e-6
        r = float(np.sqrt(max(np.abs(np.linalg.eigvalsh(cov)).max(), 1e-9)))
        rows.append((float(tb), c, r))
    return rows


def _torso_depth(V: np.ndarray, y: float, dy: float) -> Tuple[float, float]:
    """(z_back, z_front) of the torso column at height y; slab is 3 slices tall
    because low-poly backs leave single slices nearly empty."""
    H = V[:, 1].max() - V[:, 1].min()
    P = _slice(V, y - 1.5 * dy, y + 1.5 * dy)
    # Centreline column only: lats and pecs at |x| ~ 0.1H stick out far
    # behind and in front of where the spine actually is.
    P = P[np.abs(P[:, 0]) < 0.06 * H]
    if len(P) == 0:
        return 0.0, 0.0
    # Split the z distribution at empty gaps and keep the most populated
    # cluster: a braid, cape or tail behind the back is a separate cluster and
    # must not count as "the back".
    zs = np.sort(P[:, 2])
    cuts = np.where(np.diff(zs) > 0.04 * H)[0]
    groups = np.split(zs, cuts + 1)
    torso = max(groups, key=len)
    return float(torso.min()), float(torso.max())


def _shoulder_from_arm(V: np.ndarray, body: Dict, arm: Dict, prof=None) -> np.ndarray:
    """Shoulder joint on the arm axis, between the top of the arm/torso
    separation (under the deltoid) and the top of the shoulder mass (where the
    body narrows toward the neck)."""
    H, y1 = body["height"], body["y_top"]
    prof = prof or _arm_profile(arm, step=0.02 * H)
    axis = arm["axis"]
    top_c = prof[0][1]
    sep_top = max(float(top_c[1]), float(arm["armpit_y"]))
    ref = _slice(V, sep_top - 0.02 * H, sep_top + 0.01 * H)
    w_ref = float(np.ptp(ref[:, 0])) if len(ref) else 0.0
    mass_top = sep_top
    for y in np.arange(sep_top, y1, 0.03 * H):
        P = _slice(V, y, y + 0.03 * H)
        if len(P) and np.ptp(P[:, 0]) > 0.8 * w_ref:
            mass_top = y + 0.015 * H
        else:
            break
    y_sh = 0.5 * (sep_top + mass_top)
    cos_a = max(abs(axis[1]), 0.2)
    c = top_c - axis * (y_sh - top_c[1]) / cos_a
    c[1] = y_sh
    return c


# --------------------------------------------------------------------------- #
# marker proposal
# --------------------------------------------------------------------------- #
def propose_markers(V: np.ndarray, body: Optional[Dict] = None, pose_hint: str = "A") -> Dict:
    """Guess the 8 Mixamo markers (+ optional shoulder/ankle) from geometry.

    A proposal, not an answer: the user drags whatever is wrong. Returns
    {"markers": {name: [x,y,z]}, "optional": {...}, "notes": [...]} .
    """
    V = np.asarray(V, dtype=float)
    body = body or analyze_body(V)
    H, y0, y1, dy = body["height"], body["y_floor"], body["y_top"], body["slice"]
    notes: List[str] = []
    M: Dict[str, Vec] = {}
    O: Dict[str, Vec] = {}

    # --- groin: crotch point on the centerline ---------------------------------
    cy = body["crotch_y"]
    if cy is None:
        cy = y0 + 0.47 * H
        notes.append("crotch not detected (legs touching?); groin from anthropometric 0.47H")
    P = _slice(V, cy - dy, cy + 2 * dy)
    gz = float(P[:, 2].mean()) if len(P) else 0.0
    M["groin"] = [0.0, float(cy), gz]

    # --- knees: leg centroid at ~55% of floor->crotch ---------------------------
    ky = y0 + 0.55 * (cy - y0)
    for label, side in (("L", 1), ("R", -1)):
        P = _slice(V, ky - 2 * dy, ky + 2 * dy)
        P = P[P[:, 0] * side > 0]
        if len(P):
            M["knee_" + label] = [float(P[:, 0].mean()), float(ky), float(P[:, 2].mean())]
        else:
            M["knee_" + label] = [side * 0.06 * H, float(ky), 0.0]
            notes.append("knee_{} leg slice empty; placed on anthropometric guess".format(label))
        # optional ankle: same leg column, just above the floor, rear third of the foot
        F = _slice(V, y0, y0 + 0.05 * H)
        F = F[F[:, 0] * side > 0]
        if len(F):
            zb, zf = float(F[:, 2].min()), float(F[:, 2].max())
            O["ankle_" + label] = [float(F[:, 0].mean()), y0 + 0.045 * H, zb + 0.25 * (zf - zb)]

    # --- wrists / elbows from the arm axis radius profile -----------------------
    for label in ("L", "R"):
        arm = body["arms"].get(label)
        side = 1 if label == "L" else -1
        if not arm:
            hint = body.get("arm_hint", {}).get(label)
            if hint is not None:
                # Partial detection (the hand cluster): wrist on the hand, elbow
                # halfway to the shoulder line at the body's edge. Still a
                # proposal to be checked, but it sits on the real arm.
                y_sh = float(M["chin"][1] - 0.075 * H) if "chin" in M else y0 + 0.8 * H
                top = _column_top(V, float(hint["x_mean"]), H, float(hint["centroid"][1]), dy)
                if top is not None:
                    y_sh = min(y_sh, top - 0.06 * H)   # hanging arm: shoulder = top of the arm column
                band = _slice(V, y_sh - 0.02 * H, y_sh + 0.02 * H)
                edge = float(np.abs(band[:, 0]).max()) if len(band) else abs(hint["x_mean"])
                x_sh = min(abs(hint["x_mean"]), max(edge - 0.04 * H, 0.06 * H))
                shoulder_est = np.array([side * x_sh, y_sh, float(hint["centroid"][2])])
                wrist = np.array(hint["centroid"], dtype=float)
                elbow = 0.5 * (wrist + shoulder_est)
                M["wrist_" + label] = [float(v) for v in wrist]
                M["elbow_" + label] = [float(v) for v in elbow]
                notes.append("arm_{} only partially separable (hand cluster at {:.2f} H): wrist on the hand, "
                             "elbow interpolated to the shoulder line; check them".format(label, (hint["centroid"][1] - y0) / H))
                continue
            # Arm not detected: anthropometric placeholders for the pose the
            # user declared. They are meant to be dragged; wherever they end
            # up is the arm angle, so the detector never has the last word.
            if pose_hint == "T":
                M["wrist_" + label] = [side * 0.44 * H, y0 + 0.82 * H, 0.0]
                M["elbow_" + label] = [side * 0.28 * H, y0 + 0.82 * H, 0.0]
            elif pose_hint == "down":
                M["wrist_" + label] = [side * 0.13 * H, y0 + 0.47 * H, 0.0]
                M["elbow_" + label] = [side * 0.135 * H, y0 + 0.63 * H, 0.0]
            else:
                M["wrist_" + label] = [side * 0.35 * H, y0 + 0.5 * H, 0.0]
                M["elbow_" + label] = [side * 0.25 * H, y0 + 0.62 * H, 0.0]
            notes.append("arm_{} not separable from torso; wrist/elbow are placeholders, place them by hand".format(label))
            continue
        prof = _arm_profile(arm, step=0.02 * H)
        if len(prof) < 6:
            notes.append("arm_{} profile too sparse".format(label))
        ts = np.array([p[0] for p in prof])
        rs = np.array([p[2] for p in prof])
        span = ts.max() - ts.min()
        # wrist: thinnest cross-section in the distal half of the arm run, but
        # not the very tip (fingertips are thinner than the wrist).
        distal = (ts > ts.min() + 0.45 * span) & (ts < ts.max() - 0.08 * span)
        if distal.any():
            idx = np.where(distal)[0]
            rmin = rs[idx].min()
            # ties go to the most proximal candidate: fingers can be as narrow
            # as the wrist, the wrist is always closer to the elbow.
            k = int(idx[rs[idx] <= 1.1 * rmin][0])
        else:
            k = len(prof) - 2
        wrist_c = prof[k][1]
        M["wrist_" + label] = [float(v) for v in wrist_c]
        # shoulder: the arm run ends at the armpit; the joint sits ~0.1H above
        # it, continued along the arm axis (angle-aware).
        shoulder_c = _shoulder_from_arm(V, body, arm, prof)
        lo, hi = V.min(axis=0), V.max(axis=0)
        if not (lo[2] <= shoulder_c[2] <= hi[2] and lo[0] <= shoulder_c[0] <= hi[0]):
            notes.append("shoulder_{} from the detected arm fell outside the body; not proposed".format(label))
            # Elbow still needs a proximal anchor: the shoulder line under
            # the chin, at the body's edge (same rule as the hint path).
            y_sh = float(M["chin"][1] - 0.075 * H) if "chin" in M else y0 + 0.8 * H
            band = _slice(V, y_sh - 0.02 * H, y_sh + 0.02 * H)
            edge = float(np.abs(band[:, 0]).max()) if len(band) else abs(float(wrist_c[0]))
            shoulder_c = np.array([side * max(edge - 0.04 * H, 0.06 * H), y_sh, float(wrist_c[2])])
        else:
            O["shoulder_" + label] = [float(v) for v in shoulder_c]
        # elbow: midway between shoulder and wrist, snapped to the nearest
        # profile centroid so it lands on the limb axis.
        mid = 0.5 * (shoulder_c + wrist_c)
        j = int(np.linalg.norm(np.array([p[1] for p in prof]) - mid, axis=1).argmin())
        M["elbow_" + label] = [float(v) for v in prof[j][1]]

    # --- chin: bottom of the head on the centerline -----------------------------
    head_top = y1
    head_band = _slice(V, y1 - 0.15 * H, y1 - 0.03 * H)
    head_w = float(np.median([
        (np.ptp(_slice(V, y, y + dy)[:, 0]) if len(_slice(V, y, y + dy)) else np.nan)
        for y in np.arange(y1 - 0.15 * H, y1 - 0.03 * H, dy)
    ]) if len(head_band) else 0.1 * H)
    if not np.isfinite(head_w):
        head_w = 0.1 * H
    zf_head = float(head_band[:, 2].max()) if len(head_band) else 0.0
    chin_y = None
    for y in np.arange(y1 - 0.08 * H, y0 + 0.5 * H, -dy):
        P = _slice(V, y, y + dy)
        if len(P) < 3:
            continue
        w = float(np.ptp(P[:, 0]))
        zf = float(P[:, 2].max())
        if w > 1.5 * head_w or zf < 0.75 * zf_head:
            # the width test fires where the jaw/beard flares, a touch above
            # the real bottom of the head
            chin_y = y - 0.01 * H
            break
    if chin_y is None:
        chin_y = y1 - 0.13 * H
        notes.append("chin not detected; placed at 0.13H below the top")
    P = _slice(V, chin_y - dy, chin_y + 2 * dy)
    P = P[np.abs(P[:, 0]) < 0.6 * head_w]
    chin_z = float(P[:, 2].max()) - 0.015 * H if len(P) else zf_head - 0.02 * H
    M["chin"] = [0.0, float(chin_y), chin_z]

    return {"markers": M, "optional": O, "notes": notes,
            "pose": body["pose"], "arm_angle_deg": body["arm_angle_deg"],
            "height": H, "head_top": head_top}


# --------------------------------------------------------------------------- #
# refinement of user-placed markers
# --------------------------------------------------------------------------- #
def limb_cross_section(V: np.ndarray, p: Vec, axis: Optional[Vec], H: float,
                       slab_frac: float = 0.02, probe_frac: float = 0.08,
                       gap_frac: float = 0.035, min_cluster: int = 4,
                       edge_pct: float = 90.0) -> Optional[float]:
    """Measured local radius of a limb at point `p`, perpendicular to `axis`.

    Takes a thin slab across the limb and sorts the in-plane distances of its
    vertices. The limb is the *contiguous* near cluster: foreign mass (the
    torso, the opposite limb) sits past a gap. So the cluster is cut at the
    first jump larger than `gap_frac*H`, and its outer edge (`edge_pct`) is the
    limb half-thickness. A percentile alone is fooled when foreign mass matches
    the limb in point count; the gap is not. Returns None when the slab is too
    sparse to trust.

    This is the volumetric signal that lets thresholds scale with anatomy
    instead of body height, so a skinny character or a child does not break the
    height-fraction heuristics. On thick limbs (no gap within reach) it returns
    a large radius and the caller saturates its height-fraction cap unchanged.
    """
    V = np.asarray(V, dtype=float)
    q = np.array(p, dtype=float)
    a = np.array(axis if axis is not None else [0.0, 1.0, 0.0], dtype=float)
    a = a / max(np.linalg.norm(a), 1e-9)
    d = V - q
    along = d @ a
    slab = np.abs(along) < slab_frac * H
    pn = np.linalg.norm(d[slab] - np.outer(along[slab], a), axis=1)
    pn = np.sort(pn[pn < probe_frac * H])
    if pn.size < min_cluster:
        return None
    jumps = np.where(np.diff(pn) > gap_frac * H)[0]
    end = int(jumps[0]) + 1 if jumps.size else pn.size
    cluster = pn[:max(end, min_cluster)]
    return float(np.percentile(cluster, edge_pct))


def refine_limb_marker(V: np.ndarray, p: Vec, H: float, axis: Optional[Vec] = None,
                       slab_frac: float = 0.02, reach_frac: float = 0.08,
                       reach_scale: float = 2.2, min_reach_frac: float = 0.025) -> Tuple[Vec, float]:
    """Pull a limb marker onto the limb axis without moving it along the limb.

    Takes the vertices in a thin slab perpendicular to the limb direction (the
    cross-section at the marker's height) and re-centres the marker on the
    midrange of that section. Midrange, not centroid: vertex density is
    uneven and a centroid drifts toward the dense side. The coordinate along
    the axis is left exactly where the user put it. Returns (point, shift).

    The perpendicular reach is **volumetric**: rather than a fixed fraction of
    body height, it is scaled (`reach_scale`) from the measured local limb
    cross-section, bounded by `reach_frac*H`. On a thick limb it saturates at
    that cap (the reference body behaviour unchanged); on a thin limb (skinny character,
    child) it shrinks so the section does not reach across to the torso or the
    opposite limb -- the failure a fixed reach hits off the reference body's proportions.
    """
    V = np.asarray(V, dtype=float)
    q = np.array(p, dtype=float)
    a = np.array(axis if axis is not None else [0.0, 1.0, 0.0], dtype=float)
    a = a / max(np.linalg.norm(a), 1e-9)
    d = V - q
    along = d @ a
    perp = d - np.outer(along, a)
    pn = np.linalg.norm(perp, axis=1)
    slab = np.abs(along) < slab_frac * H
    cap = reach_frac * H
    radius = limb_cross_section(V, q, a, H, slab_frac=slab_frac, probe_frac=reach_frac)
    reach = cap if radius is None else float(np.clip(reach_scale * radius, min_reach_frac * H, cap))
    m = slab & (pn < reach)
    if m.sum() < 4:
        return [float(v) for v in q], 0.0
    pm = perp[m]
    # two in-plane directions
    u = np.cross(a, [0.0, 0.0, 1.0] if abs(a[2]) < 0.9 else [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    w = np.cross(a, u)
    cu, cw = pm @ u, pm @ w
    shift = 0.5 * (cu.min() + cu.max()) * u + 0.5 * (cw.min() + cw.max()) * w
    nq = q + shift
    return [float(v) for v in nq], float(np.linalg.norm(shift))


def symmetrize_to_right(markers: Dict[str, Vec]) -> Tuple[Dict[str, Vec], Dict[str, float]]:
    """Fold L/R pairs onto the -X side (the side the FitSkeleton lives on).

    Returns one entry per pair keyed without side suffix, plus the L/R
    disagreement per pair so asymmetric placement is visible, not silent.
    """
    out: Dict[str, Vec] = {}
    asym: Dict[str, float] = {}
    names = {k.rsplit("_", 1)[0] for k in markers if k.endswith(("_L", "_R"))}
    for n in sorted(names):
        L = markers.get(n + "_L")
        R = markers.get(n + "_R")
        if L is not None and R is not None:
            Lm = np.array([-L[0], L[1], L[2]])
            Rv = np.array(R, dtype=float)
            out[n] = [float(v) for v in (Lm + Rv) / 2]
            asym[n] = float(np.linalg.norm(Lm - Rv))
        elif R is not None:
            out[n] = [float(v) for v in R]
        elif L is not None:
            out[n] = [-float(L[0]), float(L[1]), float(L[2])]
    for k, v in markers.items():
        if not k.endswith(("_L", "_R")):
            out[k] = [0.0, float(v[1]), float(v[2])]  # centre markers snap to x=0
    return out, asym


# --------------------------------------------------------------------------- #
# fit joint derivation
# --------------------------------------------------------------------------- #
def _shoulder_from_markers(elbow: np.ndarray, wrist: np.ndarray, chin: np.ndarray,
                           V: np.ndarray, body: Dict) -> np.ndarray:
    """Shoulder from the markers alone, valid for any arm pose (A, T, arms
    down, bent elbow). The Mixamo idea: placing the references already gives
    the arm angle, so a failed A/T detection must not matter.

    The wrist->elbow direction is the arm angle the user gave. Continue it one
    anthropometric upper-arm length past the elbow (exact for a straight arm:
    A, T, hanging), then snap only the height to the shoulder line (a fixed
    drop below the chin) and the depth to the torso's mid-depth. Lateral
    position comes from the markers alone, so it is well conditioned for a
    near-vertical arm -- solving a sphere against the shoulder line is not
    (5 cm of elbow height became 17 cm of shoulder drift). A bent elbow is the
    documented limit; that is what the optional shoulder marker is for.
    """
    H, dy = body["height"], body["slice"]
    d = elbow - wrist
    forearm = float(np.linalg.norm(d))
    upper = float(np.clip(1.2 * forearm, 0.15 * H, 0.22 * H))
    cont = elbow + d / max(forearm, 1e-6) * upper
    # Shoulder line: a fixed drop below the chin, but never above the body's
    # "shoulder shelf" (the highest slice that is already wide). A big-headed
    # character's chin proposal sits inside the head and the chin rule alone
    # put the shoulder in the neck, 4 cm outside the mesh.
    y_sh = float(chin[1] - 0.075 * H)
    # The joint sits ~0.05 H below the top of the shoulder surface (deltoid /
    # coat); placed on the shelf itself it grazed the mesh from outside.
    y_shelf = _shoulder_shelf(V, H, dy)
    if y_shelf is not None:
        y_sh = min(y_sh, y_shelf - 0.05 * H)
    if abs(float(elbow[0] - wrist[0])) < 0.06 * H:
        # Hanging arm: the shoulder is where the arm column ends. Walk up the
        # slab at the elbow's x until it empties; a hat brim above is skipped
        # by the gap. (The width shelf alone picked a a wide-brimmed hat.)
        top = _column_top(V, float(elbow[0]), H, float(elbow[1]), dy)
        if top is not None:
            y_sh = min(y_sh, top - 0.06 * H)
    # Depth comes from the markers too (a straight arm lies in the shoulder's
    # plane). The torso column is only a clamp, and only when it read a real
    # span: at shoulder height it can return a sliver of the back (it did:
    # 3.7 cm wide, 34 cm off), so a thin span is ignored.
    z_sh = float(cont[2])
    zb, zf = _torso_depth(V, y_sh, dy)
    if zf - zb > 0.08 * H:
        z_sh = float(np.clip(z_sh, zb, zf))
    side = -1.0 if elbow[0] < 0 else 1.0
    # never past the midline, never lateral of the elbow, never outside the
    # body at that height (hanging arms put the elbow lateral of the shoulder)
    band = _slice(V, y_sh - 0.02 * H, y_sh + 0.02 * H)
    half = float(np.abs(band[:, 0]).max()) if len(band) else abs(elbow[0])
    x_lim = min(abs(elbow[0]), max(half - 0.03 * H, 0.06 * H))
    x_sh = side * float(np.clip(abs(cont[0]) if cont[0] * side > 0 else 0.0, 0.06 * H, x_lim))
    return _push_inside(V, np.array([x_sh, y_sh, z_sh]), H)


def _surrounded(V: np.ndarray, p: np.ndarray, H: float) -> bool:
    """Cheap inside test on a point cloud: surface points on both sides of p
    in x and in z within its horizontal slab."""
    S = _slice(V, p[1] - 0.02 * H, p[1] + 0.02 * H)
    if len(S) < 4:
        return False
    nx = S[np.abs(S[:, 2] - p[2]) < 0.06 * H]
    nz = S[np.abs(S[:, 0] - p[0]) < 0.06 * H]
    return bool(len(nx) and (nx[:, 0] < p[0]).any() and (nx[:, 0] > p[0]).any()
                and len(nz) and (nz[:, 2] < p[2]).any() and (nz[:, 2] > p[2]).any())


def _push_inside(V: np.ndarray, p: np.ndarray, H: float, steps: int = 10) -> np.ndarray:
    """Nudge a joint toward the midline and down until the cloud surrounds it.
    A shoulder placed on the shoulder line can graze the top of the deltoid /
    coat from the outside by a few mm (it did: 0.5 cm, all three rays missing)."""
    q = np.array(p, dtype=float)
    for _ in range(steps):
        if _surrounded(V, q, H):
            return q
        q[0] *= 0.92
        q[1] -= 0.01 * H
        # re-centre in depth on the slab around the new x
        S = _slice(V, q[1] - 0.02 * H, q[1] + 0.02 * H)
        near = S[np.abs(S[:, 0] - q[0]) < 0.06 * H]
        if len(near) >= 4:
            q[2] = 0.5 * (float(near[:, 2].min()) + float(near[:, 2].max()))
    return q


def _column_top(V: np.ndarray, x_col: float, H: float, y_from: float, dy: float,
                width_frac: float = 0.06, min_pts: int = 3) -> Optional[float]:
    """Top of the contiguous run of slices (from y_from upward) that have
    points within `width_frac*H` of x_col -- the top of a hanging arm."""
    seen, top, misses = False, None, 0
    step = 0.03 * H                      # thick slabs: sparse meshes leave thin ones empty
    for y in np.arange(y_from, float(V[:, 1].max()), step):
        P = _slice(V, y, y + step)
        n = int((np.abs(P[:, 0] - x_col) < width_frac * H).sum()) if len(P) else 0
        if n >= min_pts:
            seen, top, misses = True, y + step, 0
        elif seen:
            misses += 1
            if misses > 1:               # a real gap (shoulder -> hat brim), not a sparse slab
                break
    return top


def _shoulder_shelf(V: np.ndarray, H: float, dy: float, frac: float = 0.45) -> Optional[float]:
    """Highest slice (scanning down from 0.95 H) whose width reaches `frac` of
    the body's maximum width between 0.4 H and 0.95 H: where the shoulders
    start, whatever the head looks like."""
    y0 = float(V[:, 1].min())
    widths = []
    for f in np.arange(0.95, 0.40, -0.01):
        P = _slice(V, y0 + f * H, y0 + (f + 0.02) * H)
        widths.append((f, float(np.ptp(P[:, 0])) if len(P) > 3 else 0.0))
    wmax = max(w for _f, w in widths)
    if wmax <= 0:
        return None
    for f, w in widths:              # top-down: first slice that is "wide"
        if w >= frac * wmax:
            return y0 + f * H
    return None


def derive_fit_positions(markers: Dict[str, Vec], V: np.ndarray,
                         body: Optional[Dict] = None, spine2: bool = True) -> Dict[str, Vec]:
    """World positions for the 21 body fit joints, all side joints on -X.

    `markers` is the symmetrized dict from symmetrize_to_right: chin, groin,
    wrist, elbow, knee, and optionally shoulder, ankle.
    """
    V = np.asarray(V, dtype=float)
    body = body or analyze_body(V)
    H, y0, y1, dy = body["height"], body["y_floor"], body["y_top"], body["slice"]
    P: Dict[str, Vec] = {}

    def v(name: str) -> np.ndarray:
        return np.array(markers[name], dtype=float)

    chin, groin, wrist, elbow, knee = v("chin"), v("groin"), v("wrist"), v("elbow"), v("knee")

    # --- arm -------------------------------------------------------------------
    if "shoulder" in markers:
        shoulder = v("shoulder")
    elif body.get("arms", {}).get("R"):
        shoulder = _shoulder_from_arm(V, body, body["arms"]["R"])
        lo, hi = V.min(axis=0), V.max(axis=0)
        if not (lo[0] - 0.05 * H <= shoulder[0] <= 0 and lo[2] <= shoulder[2] <= hi[2]
                and shoulder[1] > chin[1] - 0.25 * H):
            # detected arm gave a shoulder outside the body / off the fit side /
            # below the chest: distrust it, the markers are the pose
            shoulder = _shoulder_from_markers(elbow, wrist, chin, V, body)
    else:
        # No detected arm (T-pose, arms down, odd mesh): the markers ARE the
        # pose. Elbow + wrist + chin place the shoulder for any arm angle,
        # bent elbow included -- no detection needed.
        shoulder = _shoulder_from_markers(elbow, wrist, chin, V, body)
    P["Shoulder"], P["Elbow"], P["Wrist"] = shoulder.tolist(), elbow.tolist(), wrist.tolist()
    # No fingers: the wrist still needs a child or AS builds no FK control for
    # it (and the elbow's twist setup then fails). An *End joint gives the
    # wrist its aim without adding a controller: one hand-length down the
    # forearm direction.
    fd = wrist - elbow
    fd = fd / max(np.linalg.norm(fd), 1e-6)
    P["WristEnd"] = (wrist + fd * 0.14 * H).tolist()

    # --- leg -------------------------------------------------------------------
    if "ankle" in markers:
        ankle = v("ankle")
        F = _slice(V, y0, y0 + 0.05 * H)
        F = F[F[:, 0] < 0]
        zb, zf = (float(F[:, 2].min()), float(F[:, 2].max())) if len(F) else (ankle[2] - 0.05 * H, ankle[2] + 0.12 * H)
    else:
        F = _slice(V, y0, y0 + 0.05 * H)
        F = F[F[:, 0] < 0]
        if len(F):
            zb, zf = float(F[:, 2].min()), float(F[:, 2].max())
            ax = float(F[:, 0].mean())
        else:
            zb, zf, ax = knee[2] - 0.05 * H, knee[2] + 0.12 * H, knee[0]
        ankle = np.array([ax, y0 + 0.045 * H, zb + 0.25 * (zf - zb)])
    foot = zf - zb
    P["Ankle"] = ankle.tolist()
    P["Heel"] = [ankle[0], y0, zb + 0.05 * foot]
    P["Toes"] = [ankle[0], y0 + 0.011 * H, zb + 0.72 * foot]
    P["ToesEnd"] = [ankle[0], y0 + 0.006 * H, zf]
    # Foot roll pivots (bipedBendy): the inner and outer edge of the foot at
    # the ball, on the floor. Inner is the edge nearer the centre line.
    ball = _slice(V, y0, y0 + 0.03 * H)
    ball = ball[(ball[:, 0] < 0) & (ball[:, 2] > zb + 0.45 * foot) & (ball[:, 2] < zb + 0.85 * foot)]
    if len(ball) >= 4:
        x_in, x_out = float(ball[:, 0].max()), float(ball[:, 0].min())
    else:
        x_in, x_out = float(ankle[0]) + 0.035 * H, float(ankle[0]) - 0.035 * H
    P["FootSideInner"] = [min(x_in, -1e-3), y0, zb + 0.65 * foot]
    P["FootSideOuter"] = [x_out, y0, zb + 0.65 * foot]

    # hip: leg column at crotch height, a little above the crotch
    # Thigh column just below the crotch, where the legs are still separate.
    # Bounded in x because in an A-pose the hands share this slice, far out.
    crotch = body.get("crotch_y") or groin[1]
    T = _slice(V, crotch - 0.04 * H, crotch - 0.01 * H)
    T = T[(T[:, 0] < 0) & (np.abs(T[:, 0]) < max(2.2 * abs(knee[0]), 0.12 * H))]
    thigh_w = float(np.ptp(T[:, 0])) if len(T) else 0.1 * H
    hip_x = float(0.5 * (0.5 * (T[:, 0].min() + T[:, 0].max()) + knee[0])) if len(T) else knee[0]
    # Mixamo's groin marker is read two ways: at the crotch (the hip joint is
    # a bit above it) or at the hip joint itself. Whichever is higher wins.
    hip_y = max(float(groin[1]), crotch + 0.35 * thigh_w)
    zb_h, zf_h = _torso_depth(V, hip_y, dy)
    hip_z = zb_h + 0.45 * (zf_h - zb_h) if zf_h > zb_h else knee[2]
    P["Hip"] = [hip_x, hip_y, hip_z]
    P["Knee"] = knee.tolist()

    # --- spine -----------------------------------------------------------------
    root = np.array([0.0, hip_y + 0.012 * H, hip_z])
    neck_y = shoulder[1] + 0.027 * H
    # The neck slab mixes traps, chin and beard; its depth is unreliable. Sit
    # the neck between the shoulder line and the head instead.
    head_z = chin[2] - 0.077 * H
    neck = np.array([0.0, neck_y, 0.5 * (shoulder[2] + head_z)])
    P["Root"] = root.tolist()
    # Two templates, two spines: bipedGame Root/Spine1/Spine2/Chest (fractions
    # tuned on the reference body); bipedBendy Root/Spine1/Chest with the artist's
    # proportions (Spine1 at 0.26, Chest at 0.62 of root->neck on the bar).
    spine_fracs = (("Spine1", 0.24), ("Spine2", 0.48), ("Chest", 0.73)) if spine2 else (("Spine1", 0.26), ("Chest", 0.62))
    for name, frac in spine_fracs:
        y = root[1] + frac * (neck[1] - root[1])
        zb_s, zf_s = _torso_depth(V, y, dy)
        z_line = root[2] + frac * (neck[2] - root[2])
        # Half measured torso depth, half straight line root->neck: a lumpy
        # back (loincloth, belt, tail) must not zig-zag the spine.
        z = 0.5 * (zb_s + 0.3 * (zf_s - zb_s)) + 0.5 * z_line if zf_s > zb_s else z_line
        if zf_s > zb_s:  # never outside the centreline column, whatever the blend says
            z = min(max(z, zb_s + 0.15 * (zf_s - zb_s)), zf_s - 0.15 * (zf_s - zb_s))
        P[name] = [0.0, float(y), float(z)]
    P["Neck"] = neck.tolist()
    P["Scapula"] = [0.29 * shoulder[0], float(shoulder[1]), float(shoulder[2])]

    # --- head ------------------------------------------------------------------
    # A big-headed chibi puts the chin at shoulder height: keep a minimum
    # neck (AS wants Neck -> Head >= 0.03 H) by lifting the head joint.
    head = np.array([0.0, max(chin[1] + 0.05 * H, neck[1] + 0.035 * H), chin[2] - 0.077 * H])
    P["Head"] = head.tolist()
    # HeadEnd aims the head joint, so it must sit on the top of the SKULL:
    # a hair crest, a pom-pom or a hood tip is a narrow slab above it (the
    # coated character's top knot tilted the head bone 20 cm backwards). Walk down
    # from the top until the slab is at least half as wide as the head.
    # Head width is measured above the chin only (a thick collar or wide
    # shoulders just under the neck would make every skull slab "narrow"
    # and walk HeadEnd down below the head joint, as it did on a gloved character).
    ys = np.arange(head[1] + 0.03 * H, y1, 0.01 * H)
    widths = []
    for y in ys:
        S = _slice(V, y - 0.015 * H, y + 0.015 * H)
        widths.append(float(np.ptp(S[:, 0])) if len(S) >= 4 else 0.0)
    # The skull is most of the slabs, a crest a few: the 80th percentile of
    # the widths is the skull's, whatever sticks up above it.
    head_w = float(np.percentile([w for w in widths if w > 0], 80)) if any(w > 0 for w in widths) else 0.0
    top_y, top_z = float(y1), float(head[2])
    for y, w in zip(ys[::-1], widths[::-1]):
        if w >= 0.6 * head_w and w > 0:
            S = _slice(V, y - 0.015 * H, y + 0.015 * H)
            top_y, top_z = float(y + 0.015 * H), float(S[:, 2].mean())
            break
    top_y = max(top_y, float(head[1] + 0.03 * H))
    P["HeadEnd"] = [0.0, top_y, top_z]
    P["Jaw"] = [0.0, float(head[1] - 0.018 * H), float(head[2] + 0.023 * H)]
    P["JawEnd"] = chin.tolist()
    eye_y = head[1] + 0.01 * H
    E = _slice(V, eye_y - dy, eye_y + dy)
    zf_e = float(E[:, 2].max()) if len(E) else chin[2]
    P["Eye"] = [-0.02 * H, float(eye_y), zf_e - 0.03 * H]
    P["EyeEnd"] = [-0.02 * H, float(eye_y), zf_e - 0.02 * H]

    return {k: [float(x) for x in P[k]] for k in BODY_FIT_JOINTS if k in P}


def eye_from_shells(shells: Sequence[Dict], H: float, neck_y: float, head_z: float) -> Optional[Dict]:
    """Eyeballs are the one face feature game meshes give away for free:
    a mirror pair of small, closed, near-spherical shells inside the head.
    `shells`: per shell {n, open, centre (x,y,z), bbox (dx,dy,dz)} (all of
    them; the body shells are filtered out by size). Returns the right eye
    (-X) centre and radius, or None when no such pair exists (then the
    proportional Eye stays)."""
    cand = []
    for i, sh in enumerate(shells):
        c, bb = np.asarray(sh["centre"], float), np.asarray(sh["bbox"], float)
        d = float(bb.max())
        if sh.get("open", 0.0) > 0.01 or bb.min() <= 0 or d < 0.012 * H or d > 0.06 * H:
            continue
        if d / float(bb.min()) > 1.3:            # not a ball
            continue
        if c[1] <= neck_y or abs(c[0]) > 0.08 * H or abs(c[0]) < 0.005 * H or c[2] < head_z:
            continue
        cand.append((i, c, d))
    best = None
    for i, c, d in cand:
        for j, c2, d2 in cand:
            if j <= i:
                continue
            same = abs(d - d2) < 0.25 * d and abs(c[1] - c2[1]) < 0.01 * H and abs(c[2] - c2[2]) < 0.01 * H
            if not same or abs(c[0] + c2[0]) > 0.01 * H:   # mirror pair about x=0
                continue
            score = d                                # bigger balls first: eyes beat teeth
            if best is None or score > best[0]:
                right = c if c[0] < 0 else c2
                best = (score, right, 0.5 * d)
    if best is None:
        return None
    return {"centre": [float(v) for v in best[1]], "radius": float(best[2]), "pairs": len(cand)}


def _offset_from_line(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    """Signed z offset of p from the a->b line at p's height (positive = in front)."""
    ab = b - a
    t = float(np.clip((p - a) @ ab / max(ab @ ab, 1e-9), 0.0, 1.0))
    return float(p[2] - (a + t * ab)[2])


def _enforce_bend_direction(P: Dict[str, Vec], H: float, margin_frac: float = 0.015) -> None:
    """IK bend direction comes from the fit: the knee must sit in front of
    the hip->ankle line and the elbow behind the shoulder->wrist line, or
    AdvancedSkeleton builds a leg that folds backwards. Only the depth (z)
    moves, and only when the joint is on the wrong side or too close to
    the line to disambiguate."""
    m = margin_frac * H
    knee, hip, ankle = (np.array(P[k], dtype=float) for k in ("Knee", "Hip", "Ankle"))
    off = _offset_from_line(knee, hip, ankle)
    if off < m:
        knee[2] += m - off
        P["Knee"] = knee.tolist()
    elbow, shoulder, wrist = (np.array(P[k], dtype=float) for k in ("Elbow", "Shoulder", "Wrist"))
    off = _offset_from_line(elbow, shoulder, wrist)
    if off > -m:
        elbow[2] -= off + m
        P["Elbow"] = elbow.tolist()


# --------------------------------------------------------------------------- #
# geometric verification helpers (Maya-free)
# --------------------------------------------------------------------------- #
def bone_length_report(P: Dict[str, Vec], H: float) -> Dict[str, Dict]:
    """Segment lengths as a fraction of height, with loose anthropometric bands.

    Bands are deliberately wide (stylized characters), meant to catch a joint
    that landed in the wrong limb, not to gloved character proportions.
    """
    pairs = {
        "upper_arm": ("Shoulder", "Elbow", 0.08, 0.35),
        "forearm": ("Elbow", "Wrist", 0.08, 0.30),
        "thigh": ("Hip", "Knee", 0.10, 0.35),
        "shin": ("Knee", "Ankle", 0.08, 0.35),
        "spine": ("Root", "Neck", 0.15, 0.55),   # stylised/chibi torsos reach 0.48 H (measured)
        "neck_head": ("Neck", "Head", 0.03, 0.45),
    }
    out = {}
    for k, (a, b, lo, hi) in pairs.items():
        L = float(np.linalg.norm(np.array(P[a]) - np.array(P[b])))
        f = L / H
        out[k] = {"length": round(L, 2), "frac_of_height": round(f, 3),
                  "ok": lo <= f <= hi, "band": [lo, hi]}
    return out


# --------------------------------------------------------------------------- #
# volume (Maya-free)
# --------------------------------------------------------------------------- #
def mesh_volume(V: np.ndarray, tris: np.ndarray) -> float:
    """Enclosed volume of a triangle mesh (divergence theorem: sum of signed
    tetrahedra to the origin), in cubic units of V. Compared between the rest
    and a pose it is the candy-wrapper / collapse metric: skinning that pinches
    a joint loses volume, an exploded limb gains it. Absolute value, so the
    winding convention does not matter."""
    V = np.asarray(V, dtype=float)
    T = np.asarray(tris, dtype=int).reshape(-1, 3)
    if T.size == 0:
        return 0.0
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    return abs(float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))


def orient_consistently(tris: np.ndarray) -> np.ndarray:
    """Flip triangles so every shared edge is traversed in opposite directions
    by its two faces (one winding convention per connected component).

    This OBJ ships with inconsistent normals. A region wound the wrong way
    contributes its signed volume with the opposite sign, so moving it (raising
    an arm) shifts the total exactly like an open boundary would -- arms_up
    read as +19 % body volume with the wrists already capped. Orientation is
    fixed once, on the rest triangles, and reused for every pose."""
    T = np.asarray(tris, dtype=int).reshape(-1, 3).copy()
    n = len(T)
    if n == 0:
        return T
    edge_faces: Dict[Tuple[int, int], List[int]] = {}
    for i, (a, b, c) in enumerate(T):
        for u, v in ((a, b), (b, c), (c, a)):
            edge_faces.setdefault((u, v) if u < v else (v, u), []).append(i)

    def directed(i: int, key: Tuple[int, int]) -> bool:
        """True if triangle i traverses edge `key` as key[0] -> key[1]."""
        a, b, c = T[i]
        return (a, b) == key or (b, c) == key or (c, a) == key

    visited = np.zeros(n, dtype=bool)
    for start in range(n):
        if visited[start]:
            continue
        visited[start] = True
        stack = [start]
        while stack:
            i = stack.pop()
            a, b, c = T[i]
            for u, v in ((a, b), (b, c), (c, a)):
                key = (u, v) if u < v else (v, u)
                faces = edge_faces[key]
                if len(faces) != 2:  # boundary or non-manifold: no constraint
                    continue
                j = faces[0] if faces[1] == i else faces[1]
                if visited[j]:
                    continue
                # consistent neighbours traverse the shared edge in opposite directions
                if directed(i, key) == directed(j, key):
                    T[j] = T[j][::-1]
                visited[j] = True
                stack.append(j)
    return T


def boundary_loops(tris: np.ndarray) -> List[List[int]]:
    """Closed loops of boundary vertices (edges with a single face), each
    listed in the direction the adjacent triangle traverses the edge. A body
    with no hands has open wrists; eye sockets and mouths are loops too."""
    T = np.asarray(tris, dtype=int).reshape(-1, 3)
    count: Dict[Tuple[int, int], int] = {}
    directed: Dict[Tuple[int, int], Tuple[int, int]] = {}
    for a, b, c in T:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            count[key] = count.get(key, 0) + 1
            directed[key] = (int(u), int(v))
    nxt: Dict[int, List[int]] = {}
    for key, n in count.items():
        if n == 1:
            u, v = directed[key]
            nxt.setdefault(u, []).append(v)
    loops: List[List[int]] = []
    seen: set = set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, cur = [], start
        while cur is not None and cur not in seen:
            seen.add(cur)
            loop.append(cur)
            cand = [v for v in nxt.get(cur, []) if v not in seen]
            cur = cand[0] if cand else None
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def closed_volume(V: np.ndarray, tris: np.ndarray, loops: Optional[List[List[int]]] = None) -> float:
    """`mesh_volume` after capping every boundary loop with a fan to its
    centroid. An open shell's divergence volume shifts with any translation of
    the open region (raising an arm with open wrists read as +14 % body
    volume); capped, it is well defined and pose comparisons mean something.
    The cap triangles run the boundary edge backwards, so they are wound
    consistently with their neighbour whatever the mesh's global convention."""
    V = np.asarray(V, dtype=float)
    T = np.asarray(tris, dtype=int).reshape(-1, 3)
    if loops is None:
        loops = boundary_loops(T)
    if not loops:
        return mesh_volume(V, T)
    extra, caps = [], []
    n = len(V)
    for i, loop in enumerate(loops):
        extra.append(V[loop].mean(axis=0))
        cid = n + i
        for j in range(len(loop)):
            u, v = loop[j], loop[(j + 1) % len(loop)]
            caps.append([v, u, cid])
    Vx = np.vstack([V, np.array(extra)])
    Tx = np.vstack([T, np.array(caps, dtype=int)])
    return mesh_volume(Vx, Tx)


# --------------------------------------------------------------------------- #
# Generalised winding number: inside/outside that survives game meshes
# --------------------------------------------------------------------------- #
def winding_numbers(V: np.ndarray, tris: np.ndarray, points: np.ndarray, chunk: int = 4000) -> np.ndarray:
    """Solid-angle winding number of every query point (Jacobson et al. 2013,
    Van Oosterom's formula per triangle). ~1 inside a closed shell, ~0
    outside, and still sensible where a shell is open or shells overlap --
    which is where ray parity (odd/even hits) lied on every game character
    built from open, intersecting pieces (stylised short character vest: Root 'outside').
    Sign follows the winding convention; callers use the magnitude."""
    V = np.asarray(V, dtype=float)
    T = np.asarray(tris, dtype=int).reshape(-1, 3)
    Q = np.asarray(points, dtype=float).reshape(-1, 3)
    out = np.zeros(len(Q))
    for s in range(0, len(T), chunk):
        a = V[T[s:s + chunk, 0]][None, :, :] - Q[:, None, :]
        b = V[T[s:s + chunk, 1]][None, :, :] - Q[:, None, :]
        c = V[T[s:s + chunk, 2]][None, :, :] - Q[:, None, :]
        la, lb, lc = (np.linalg.norm(a, axis=2), np.linalg.norm(b, axis=2), np.linalg.norm(c, axis=2))
        num = np.einsum("qtk,qtk->qt", a, np.cross(b, c))
        den = la * lb * lc + np.einsum("qtk,qtk->qt", a, b) * lc + np.einsum("qtk,qtk->qt", a, c) * lb \
            + np.einsum("qtk,qtk->qt", b, c) * la
        out += (2.0 * np.arctan2(num, den)).sum(axis=1)
    return out / (4.0 * math.pi)


def inside_by_winding(V: np.ndarray, tris: np.ndarray, points: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    return np.abs(winding_numbers(V, tris, points)) > threshold


def solid_spans_z(V: np.ndarray, tris: np.ndarray, x: float, y: float, z_lo: float, z_hi: float,
                  samples: int = 80, threshold: float = 0.5) -> List[Tuple[float, float]]:
    """(z_enter, z_exit) of every solid span along Z through (x, y), sampled
    with the winding number; longest first. Replaces the ray-parity version."""
    zs = np.linspace(z_lo, z_hi, samples)
    Q = np.column_stack([np.full(samples, x), np.full(samples, y), zs])
    inside = inside_by_winding(V, tris, Q, threshold)
    spans: List[Tuple[float, float]] = []
    start = None
    for i, flag in enumerate(inside):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            spans.append((float(zs[start]), float(zs[i - 1])))
            start = None
    if start is not None:
        spans.append((float(zs[start]), float(zs[-1])))
    return sorted(spans, key=lambda s: s[0] - s[1])
