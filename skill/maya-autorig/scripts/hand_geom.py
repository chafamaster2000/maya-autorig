"""Hand geometry from the mesh: finger lobes, thumb, the real wrist, and the
AdvancedSkeleton finger fit joints (Cup, ThumbFinger1-4 ... PinkyFinger1-4).

Pure numpy, no Maya, testable offline on a vertex/triangle dump.

The idea mirrors the body work in marker_geom: slice the hand along its own
axis, and in every slab take the connected components of the mesh (edges,
not distance): a finger is a component that stays separate slab after slab
down to the tip. Mesh connectivity is what makes this robust on 3-4k vertex
game meshes, where fingers are 3 cm apart and a distance clustering would
either fuse them or split a palm.

What it does not assume: that the wrist marker sits at the wrist (on suited
characters the arm walk stops in the forearm), that fingers exist (mittens,
chibis), or that the hand hangs along the forearm.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

Vec = Sequence[float]

FINGER_ORDER = ("Index", "Middle", "Ring", "Pinky")   # from the thumb outward
CUP_FINGERS = ("Ring", "Pinky")                       # AS parents these under Cup

# Slab thickness, link radius between slabs and the minimum lobe size are
# fractions of the height H, like every other threshold in the pipeline.
SLAB = 0.01
LINK = 0.03
MIN_LOBE_PTS = 3
MIN_FINGER_SLABS = 2
# Finger joints along the tracked finger curve (MCP=0, tip=1).
PHALANX_FRACS = (0.0, 0.45, 0.75, 1.0)
THUMB_FRACS = (0.0, 0.55, 1.0)      # from the thumb's MCP (Thumb2) to its tip


def adjacency(tris: np.ndarray, n: int) -> List[set]:
    adj: List[set] = [set() for _ in range(n)]
    for a, b, c in tris:
        a, b, c = int(a), int(b), int(c)
        adj[a].add(b); adj[b].add(a)
        adj[b].add(c); adj[c].add(b)
        adj[c].add(a); adj[a].add(c)
    return adj


def _components(verts: Sequence[int], adj: List[set]) -> List[List[int]]:
    left = set(int(v) for v in verts)
    out: List[List[int]] = []
    while left:
        v0 = left.pop()
        comp = [v0]
        stack = [v0]
        while stack:
            v = stack.pop()
            for w in adj[v]:
                if w in left:
                    left.discard(w)
                    comp.append(w)
                    stack.append(w)
        out.append(sorted(comp))
    return out


def _flood(seed: Sequence[int], allow: np.ndarray, adj: List[set]) -> List[int]:
    seen = set(int(v) for v in seed)
    stack = list(seen)
    while stack:
        v = stack.pop()
        for w in adj[v]:
            if w not in seen and allow[w]:
                seen.add(w)
                stack.append(w)
    return sorted(seen)


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def hand_region(V: np.ndarray, tris: np.ndarray, wrist: Vec, elbow: Vec, H: float,
                adj: Optional[List[set]] = None) -> Dict[str, Any]:
    """Vertices of the hand: everything connected to the wrist ring on the
    far side of the wrist plane, within a radius of the forearm axis (so a
    hand hanging against a thigh never swallows the leg, and a sleeve never
    leads back into the torso)."""
    wrist = np.asarray(wrist, float)
    elbow = np.asarray(elbow, float)
    if adj is None:
        adj = adjacency(tris, len(V))
    a0 = _unit(wrist - elbow)
    rel = V - wrist
    t = rel @ a0
    r = np.linalg.norm(rel - np.outer(t, a0), axis=1)
    band = np.where((np.abs(t) < 0.015 * H) & (r < 0.06 * H))[0]
    allow = (t > -0.02 * H) & (r < 0.12 * H) & (t < 0.35 * H)
    best: List[int] = []
    for comp in _components(band, adj):
        region = _flood(comp, allow, adj)
        if len(region) > len(best):
            best = region
    idx = np.array(best, dtype=int)
    out: Dict[str, Any] = {"idx": idx, "a0": a0, "adj": adj, "n": int(len(idx)), "origin": wrist}
    if len(idx) < 4 * MIN_LOBE_PTS:
        out["axis"] = a0
        return out
    P = V[idx]
    tt = t[idx]
    distal = P[tt >= np.percentile(tt, 85)]
    axis = _unit(distal.mean(0) - wrist)
    if axis @ a0 < 0.3:            # hand folded back on the forearm: trust the forearm
        axis = a0
    t2 = (P - wrist) @ axis
    perp = (P - wrist) - np.outer(t2, axis)
    m = t2 > 0
    Q = perp[m] if m.sum() >= 3 else perp
    w, v = np.linalg.eigh(Q.T @ Q / max(1, len(Q)))
    u1, u2 = v[:, -1], v[:, -2]
    out.update({"axis": axis, "u1": u1, "u2": u2, "t": t2, "x1": perp @ u1, "x2": perp @ u2,
                "length": float(t2.max())})
    return out


def _slab_components(reg: Dict[str, Any], V: np.ndarray, H: float) -> List[Dict[str, Any]]:
    idx, t, adj = reg["idx"], reg["t"], reg["adj"]
    pos = {int(v): k for k, v in enumerate(idx)}
    slabs = []
    step = SLAB * H
    a = 0.0
    while a < reg["length"] + step:
        s = np.where((t >= a) & (t < a + step))[0]
        comps = []
        for comp in _components([int(idx[i]) for i in s], adj):
            if len(comp) < MIN_LOBE_PTS:
                continue
            k = [pos[v] for v in comp]
            pts = V[idx[k]]
            comps.append({"verts": comp, "centroid": pts.mean(0), "n": len(comp),
                          "x1": float(np.mean(reg["x1"][k])), "x2": float(np.mean(reg["x2"][k])),
                          "width": float(np.ptp(reg["x1"][k])), "t": float(a + 0.5 * step)})
        slabs.append({"t": a, "comps": comps})
        a += step
    return slabs


def _track(slabs: List[Dict[str, Any]], k0: int, c0: int, H: float, direction: int) -> List[Dict[str, Any]]:
    """Follow one lobe slab by slab (direction +1 distal, -1 proximal) while
    it stays a separate component; a merge (the nearest component of the
    next slab is also nearest for another lobe of the current slab) ends it."""
    out = [slabs[k0]["comps"][c0]]
    cur = slabs[k0]["comps"][c0]
    k = k0
    misses = 0
    while True:
        k += direction
        if k < 0 or k >= len(slabs):
            break
        comps = slabs[k]["comps"]
        if not comps:
            misses += 1
            if misses > 1:
                break
            continue
        d = [np.hypot(c["x1"] - cur["x1"], c["x2"] - cur["x2"]) for c in comps]
        j = int(np.argmin(d))
        if d[j] > LINK * H:
            misses += 1
            if misses > 1:
                break
            continue
        # merge test: another lobe of the slab we came from also maps to j
        prev = [c for c in slabs[k - direction]["comps"] if c is not cur]
        merged = any(int(np.argmin([np.hypot(c["x1"] - p["x1"], c["x2"] - p["x2"]) for c in comps])) == j
                     and np.hypot(comps[j]["x1"] - p["x1"], comps[j]["x2"] - p["x2"]) < LINK * H
                     for p in prev)
        if merged or comps[j]["width"] > 2.2 * max(cur["width"], 0.01 * H):
            break
        misses = 0
        cur = comps[j]
        out.append(cur)
    return out


def analyze_hand(V: np.ndarray, tris: np.ndarray, wrist: Vec, elbow: Vec, H: float,
                 adj: Optional[List[set]] = None) -> Dict[str, Any]:
    """Finger lobes of the hand hanging from `wrist`, in world units.

    Returns fingers as tracks (list of centroids from the knuckle to the
    tip), which one is the thumb, the wrist re-derived from the palm, and a
    verdict: 'fingers' (>=2 lobes), 'mitten' (one lobe or none)."""
    wrist = np.asarray(wrist, float)
    reg = hand_region(V, tris, wrist, elbow, H, adj)
    out: Dict[str, Any] = {"region_n": reg["n"], "_region_idx": reg["idx"], "axis": reg["axis"].tolist(),
                           "wrist_in": wrist.tolist(),
                           "fingers": [], "thumb": None, "kind": "mitten", "wrist": wrist.tolist(),
                           "wrist_shift_frac_H": 0.0, "hand_length_frac_H": 0.0}
    if "t" not in reg:
        out["reason"] = "no hand geometry beyond the wrist"
        return out
    out["hand_length_frac_H"] = round(reg["length"] / H, 4)
    slabs = _slab_components(reg, V, H)
    # Fingers live in the distal half. The slab with the most lobes there
    # names how many fingers the mesh has; each lobe is then tracked both
    # ways to get its knuckle and its tip.
    half = int(len(slabs) * 0.45)
    counts = [len(s["comps"]) for s in slabs]
    if not any(c >= 2 for c in counts[half:]):
        out["reason"] = "one lobe all the way down: mitten"
        return out
    best_k = max(range(half, len(slabs)), key=lambda k: (counts[k], k))
    # Prefer the most proximal slab that still shows the full count: lobes
    # there are fat and well sampled, tips are needle-thin.
    for k in range(half, len(slabs)):
        if counts[k] == counts[best_k]:
            best_k = k
            break
    fingers = []
    claimed: set = set()
    for c0 in range(len(slabs[best_k]["comps"])):
        f = _finger_from_lobe(slabs, best_k, c0, H)
        if f is not None:
            fingers.append(f)
            claimed.update(id(c) for c in f["comps"])
    if len(fingers) < 2:
        out["reason"] = "lobes too short to be fingers: mitten"
        return out
    fingers.sort(key=lambda f: f["x1"])
    # Thumb, first try: an outermost lobe of the knuckle row whose knuckle
    # sits clearly below the others' (a thumb hanging beside the palm).
    thumb = None
    if len(fingers) >= 3:
        cand = [(_base_gap(fingers, 0, H), 0), (_base_gap(fingers, len(fingers) - 1, H), len(fingers) - 1)]
        gap, i = max(cand)
        if gap >= 0.012:
            thumb = i
    # Second try: a lobe that leaves the palm proximal to the knuckle row and
    # never reaches it (stylised short character: thumb sticks out of the palm's side and ends
    # a slab before the fingers separate). Any unclaimed, non-palm component
    # in the slabs below the row that tracks for >= 2 slabs at an outer x1.
    if thumb is None:
        x1s = [f["x1"] for f in fingers]
        spacing = float(np.median(np.diff(sorted(x1s)))) if len(x1s) > 1 else 0.03 * H
        best_t = None
        for k in range(0, best_k):
            comps = slabs[k]["comps"]
            if len(comps) < 2:
                continue
            palm = max(comps, key=lambda c: c["n"])
            for c0, c in enumerate(comps):
                if c is palm or id(c) in claimed:
                    continue
                f = _finger_from_lobe(slabs, k, c0, H)
                if f is None:
                    continue
                claimed.update(id(cc) for cc in f["comps"])
                outer = f["x1"] < min(x1s) - 0.5 * spacing or f["x1"] > max(x1s) + 0.5 * spacing
                if outer and f["width"] < 2.0 * float(np.median([g["width"] for g in fingers])):
                    if best_t is None or f["length"] > best_t["length"]:
                        best_t = f
        if best_t is not None:
            fingers.append(best_t)
            fingers.sort(key=lambda f: f["x1"])
            thumb = fingers.index(best_t)
    if thumb is not None and thumb == len(fingers) - 1:
        fingers.reverse()            # thumb first, then index ... pinky
        thumb = 0
    rest = [i for i in range(len(fingers)) if i != thumb]
    for k, i in enumerate(rest):
        fingers[i]["name"] = FINGER_ORDER[k] if k < len(FINGER_ORDER) else None
    if thumb is not None:
        fingers[thumb]["name"] = "Thumb"
    fingers = [f for f in fingers if f.get("name")]
    names = [f["name"] for f in fingers]
    body = [f for f in fingers if f["name"] != "Thumb"]
    t_base = float(np.median([f["t_base"] for f in body]))
    Lf = float(np.median([f["length"] for f in body]))
    thumb_base = next((f["t_base"] for f in fingers if f["name"] == "Thumb"), None)
    t_w, wrist_new, how = _wrist_from_palm(reg, t_base, Lf, H, thumb_base)
    if wrist_new is not None and t_w > 0.01 * H:
        out["wrist"] = wrist_new.tolist()
        out["wrist_shift_frac_H"] = round(t_w / H, 4)
    out["wrist_rule"] = how
    # Palm centreline: the biggest lobe of every slab from the wrist to the
    # knuckle row. Cup and Thumb1 hang off it (a straight line from the wrist
    # to a knuckle leaves a thin, angled palm: gloved character Cup 'outside').
    palm = []
    for sl in slabs:
        if sl["t"] > t_base or not sl["comps"]:
            continue
        big = max(sl["comps"], key=lambda c: c["n"])
        palm.append([float(sl["t"] + 0.5 * SLAB * H)] + [float(v) for v in big["centroid"]])
    out["palm_line"] = palm
    out["u1"] = reg["u1"].tolist()
    out.update({"kind": "fingers", "thumb": "Thumb" in names, "names": names,
                "finger_length_frac_H": round(Lf / H, 4), "knuckle_t_frac_H": round(t_base / H, 4),
                "fingers": [{k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in f.items()
                             if k != "comps"} for f in fingers]})
    return out


def _finger_from_lobe(slabs: List[Dict[str, Any]], k: int, c0: int, H: float) -> Optional[Dict[str, Any]]:
    distal = _track(slabs, k, c0, H, +1)
    proximal = _track(slabs, k, c0, H, -1)
    track = list(reversed(proximal[1:])) + distal   # knuckle ... tip
    if len(track) < MIN_FINGER_SLABS:
        return None
    pts = np.array([c["centroid"] for c in track])
    return {"track": pts, "comps": track, "x1": float(np.mean([c["x1"] for c in track])),
            "x2": float(np.mean([c["x2"] for c in track])),
            "t_base": float(track[0]["t"]), "t_tip": float(track[-1]["t"]),
            "length": float(track[-1]["t"] - track[0]["t"] + SLAB * H),
            "width": float(np.median([c["width"] for c in track]))}


def _base_gap(fingers: List[Dict[str, Any]], i: int, H: float) -> float:
    others = [f for j, f in enumerate(fingers) if j != i]
    return float(np.median([f["t_base"] for f in others]) - fingers[i]["t_base"]) / H


def _smoothed_width(reg: Dict[str, Any], a: float, H: float, win: float = 0.015) -> Optional[Tuple[float, np.ndarray]]:
    t, x1 = reg["t"], reg["x1"]
    s = (t >= a - win * H) & (t <= a + win * H)
    if s.sum() < 6:
        return None
    return float(np.percentile(x1[s], 95) - np.percentile(x1[s], 5)), s


def _wrist_from_palm(reg: Dict[str, Any], t_base: float, Lf: float, H: float,
                     thumb_base: Optional[float]) -> Tuple[float, Optional[np.ndarray], str]:
    """Where the palm starts. Walking from the knuckle row toward the arm,
    the palm is the wide part; the wrist is where the width has dropped to
    3/4 of it. A local minimum would do on a bare arm, but a sleeve is wider
    than the wrist and the region is clipped by the forearm radius, so the
    'first drop' is what holds on every case. The thumb's root (its CMC
    joint is at the wrist) caps how far down the hand it can go; a hand with
    no drop before the marker keeps the marker."""
    palm_lo, palm_hi = t_base - 1.5 * Lf, t_base - 0.3 * Lf
    widths = []
    a = palm_hi
    while a >= max(0.0, palm_lo):
        w = _smoothed_width(reg, a, H)
        if w is not None:
            widths.append((a, w[0]))
        a -= 0.005 * H
    if not widths:
        return 0.0, None, "no palm samples"
    palm_w = max(w for _, w in widths[: max(1, len(widths) // 2)])
    limit = max(0.0, t_base - 2.2 * Lf)
    a = palm_hi
    found = None
    while a >= limit:
        w = _smoothed_width(reg, a, H)
        if w is not None and w[0] <= 0.75 * palm_w:
            found = (a, w[1])
            break
        a -= 0.005 * H
    how = "palm width drop"
    if found is None:
        if thumb_base is not None:
            a = thumb_base - 0.25 * max(0.0, t_base - thumb_base)
            how = "thumb root"
        else:
            a = t_base - 0.9 * Lf
            how = "palm ratio"
        w = _smoothed_width(reg, a, H, win=0.02)
        if w is None or a <= 0.0:
            return 0.0, None, how + " (kept marker)"
        found = (a, w[1])
    a, mask = found
    if thumb_base is not None and a > thumb_base:
        a = thumb_base
        w = _smoothed_width(reg, a, H, win=0.02)
        if w is None:
            return 0.0, None, how + " (kept marker)"
        mask = w[1]
        how += ", capped at thumb root"
    return float(a), reg_point(reg, a, mask), how


def reg_point(reg: Dict[str, Any], a: float, mask: np.ndarray) -> np.ndarray:
    """World point at parameter `a` along the hand axis, centred on the
    masked vertices' perpendicular offset."""
    # P = origin + t*axis + x1*u1 + x2*u2; only the projections are stored.
    c1 = float(np.mean(reg["x1"][mask]))
    c2 = float(np.mean(reg["x2"][mask]))
    return reg["origin"] + a * reg["axis"] + c1 * reg["u1"] + c2 * reg["u2"]


def _along(track: np.ndarray, frac: float) -> np.ndarray:
    """Point at `frac` of the arc length along a polyline."""
    if len(track) == 1:
        return track[0]
    seg = np.linalg.norm(np.diff(track, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    target = frac * cum[-1]
    i = int(np.searchsorted(cum, target, side="right") - 1)
    i = min(max(i, 0), len(track) - 2)
    d = cum[i + 1] - cum[i]
    u = 0.0 if d < 1e-12 else (target - cum[i]) / d
    return track[i] + u * (track[i + 1] - track[i])


def _palm_point(hand: Dict[str, Any], t: float, wrist: np.ndarray, axis: np.ndarray) -> np.ndarray:
    """Point on the palm centreline at parameter t measured from `wrist`
    (the line itself is parametrised from the marker wrist; falls back to
    the axis when the line has no sample there)."""
    line = hand.get("palm_line") or []
    t = t + float((wrist - np.asarray(hand["wrist_in"], float)) @ axis)
    pts = [(row[0], np.asarray(row[1:], float)) for row in line if row[0] >= 0.0]
    if not pts:
        return wrist + t * axis
    ts = np.array([p[0] for p in pts])
    if t <= ts[0]:
        return pts[0][1]
    if t >= ts[-1]:
        return pts[-1][1]
    i = int(np.searchsorted(ts, t)) - 1
    u = (t - ts[i]) / max(1e-9, ts[i + 1] - ts[i])
    return pts[i][1] + u * (pts[i + 1][1] - pts[i][1])


def finger_fit_positions(hand: Dict[str, Any], H: float) -> Dict[str, List[float]]:
    """AS fit joint positions for the detected chains: <Name>Finger1..4 per
    finger, ThumbFinger1..4, Cup. Only chains the mesh has are returned.
    Mittens get a single MiddleFinger chain down the hand axis so the wrist
    still has a child (an FK control) and the hand can curl."""
    P: Dict[str, List[float]] = {}
    wrist = np.asarray(hand["wrist"], float)
    axis = np.asarray(hand["axis"], float)
    if hand["kind"] != "fingers":
        L = hand.get("hand_length_frac_H", 0.0) * H
        if L <= 0.02 * H:
            return P
        base = wrist + 0.45 * L * axis
        tip = wrist + 0.98 * L * axis
        for k, f in enumerate(PHALANX_FRACS):
            P["MiddleFinger{}".format(k + 1)] = (base + f * (tip - base)).tolist()
        return P
    for f in hand["fingers"]:
        track = np.asarray(f["track"], float)
        # knuckle half a slab behind the first separate slab; tip half a slab
        # past the last one (the fingertip surface, like HeadEnd/ToesEnd).
        d = _unit(track[-1] - track[0]) if len(track) > 1 else axis
        knuckle = track[0] - 0.5 * SLAB * H * d
        tip = track[-1] + 0.5 * SLAB * H * d
        curve = np.vstack([knuckle, track, tip])
        if f["name"] == "Thumb":
            # Thumb1 (CMC) sits between the wrist and the thumb's knuckle,
            # inside the palm; Thumb2 is the knuckle. A lobe that leaves the
            # wrist itself (low-poly thumb along the palm) spreads the four
            # joints down its own curve instead.
            if np.linalg.norm(knuckle - wrist) >= 0.03 * H:
                t_k = float((knuckle - wrist) @ axis)
                on_palm = _palm_point(hand, 0.4 * t_k, wrist, axis)
                lateral = knuckle - _palm_point(hand, t_k, wrist, axis)
                lateral -= float(lateral @ axis) * axis
                P["ThumbFinger1"] = (on_palm + 0.5 * lateral).tolist()
                for k, fr in enumerate(THUMB_FRACS):
                    P["ThumbFinger{}".format(k + 2)] = _along(curve, fr).tolist()
            else:
                for k, fr in enumerate((0.1, 0.35, 0.7, 1.0)):
                    P["ThumbFinger{}".format(k + 1)] = _along(curve, fr).tolist()
        else:
            for k, fr in enumerate(PHALANX_FRACS):
                P["{}Finger{}".format(f["name"], k + 1)] = _along(curve, fr).tolist()
    ring = P.get("RingFinger1") or P.get("PinkyFinger1")
    if ring is not None:
        ring = np.asarray(ring, float)
        t_r = float((ring - wrist) @ axis)
        on_palm = _palm_point(hand, 0.3 * t_r, wrist, axis)
        lateral = ring - _palm_point(hand, t_r, wrist, axis)
        lateral -= float(lateral @ axis) * axis
        P["Cup"] = (on_palm + 0.4 * lateral).tolist()
    return P


def nudge_inside(V: np.ndarray, tris: np.ndarray, hand: Dict[str, Any], P: Dict[str, List[float]],
                 steps: int = 6) -> Dict[str, Any]:
    """Pull any joint the winding test calls outside toward the palm
    centreline at its own parameter, a fraction per step, until it is in.
    Tips (*Finger4) are surface points and left alone."""
    import marker_geom as mg
    wrist = np.asarray(hand["wrist"], float)
    axis = np.asarray(hand["axis"], float)
    moved: Dict[str, Any] = {}
    inside = joints_inside(V, tris, P)
    for name, ok in inside.items():
        if ok:
            continue
        p = np.asarray(P[name], float)
        target = _palm_point(hand, float((p - wrist) @ axis), wrist, axis)
        for k in range(1, steps + 1):
            q = p + (k / float(steps)) * (target - p)
            if bool(mg.inside_by_winding(V, tris, q[None, :])[0]):
                P[name] = q.tolist()
                moved[name] = {"steps": k, "shift": round(float(np.linalg.norm(q - p)), 2)}
                break
    return moved


def hand_report(hand: Dict[str, Any], H: float) -> Dict[str, Any]:
    """Slim, JSON-safe summary for evidence files and the grid."""
    return {"kind": hand["kind"], "names": hand.get("names", []), "thumb": bool(hand.get("thumb")),
            "region_n": hand["region_n"], "hand_length_frac_H": hand.get("hand_length_frac_H"),
            "finger_length_frac_H": hand.get("finger_length_frac_H"),
            "wrist_shift_frac_H": hand.get("wrist_shift_frac_H", 0.0), "reason": hand.get("reason")}


def hand_shell_tris(tris: np.ndarray, sov: Sequence[int], region_idx: Sequence[int]) -> np.ndarray:
    """Triangles of the mesh shell(s) the hand region belongs to, for a
    closed-surface inside test (the region alone is open at the wrist)."""
    sov = np.asarray(sov)
    shells = set(int(s) for s in sov[np.asarray(region_idx, dtype=int)])
    keep = np.array([int(sov[a]) in shells for a in tris[:, 0]])
    return tris[keep]


def joints_inside(V: np.ndarray, tris: np.ndarray, P: Dict[str, Sequence[float]]) -> Dict[str, bool]:
    """Winding-number inside test of the finger joints against the hand's
    shell; *Finger4 tips sit on the surface and are exempt."""
    import marker_geom as mg
    names = [k for k in P if not k.endswith("Finger4")]
    if not names:
        return {}
    pts = np.array([P[k] for k in names], dtype=float)
    ins = mg.inside_by_winding(V, tris, pts)
    return {k: bool(v) for k, v in zip(names, ins)}
