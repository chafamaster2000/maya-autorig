"""Names of an AdvancedSkeleton rig, sorted into the generic humanoid and the rest.

The yardstick rig (an artist's AS biped) carries joints for a coat, a beard,
hair, a belt, glasses... The auto-rig targets the *generic humanoid* under all
that, so every comparison first strips a joint list down to the AS canonical
humanoid names (`core`), keeps AS's optional anatomy (`optional`: belly,
breast, ears, tongue...), and sets everything else aside as `extra`. Rig
internals (FKX/IKX/Bend duplicates, non-symmetry copies) are `internal`.
Pure Python: no Maya.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List

# (group, regex on the base name -- side suffix _R/_L/_M already removed)
CORE = [
    ("spine", r"Root(Part\d+)?$"), ("spine", r"Spine\d+(Part\d+)?$"), ("spine", r"Chest$"),
    ("neck_head", r"Neck(Part\d+)?$"), ("neck_head", r"Head$"), ("neck_head", r"HeadEnd$"),
    ("face", r"Eye$"), ("face", r"EyeEnd$"), ("face", r"Jaw$"), ("face", r"JawEnd$"),
    ("shoulder", r"Scapula$"), ("shoulder", r"Shoulder(Part\d+)?$"),
    ("arm", r"Elbow(Part\d+)?$"), ("arm", r"Wrist$"),
    ("hand", r"(Thumb|Index|Middle|Ring|Pinky)Finger\d+$"), ("hand", r"Cup$"),
    ("leg", r"Hip(Part\d+)?$"), ("leg", r"Knee(Part\d+)?$"), ("leg", r"Ankle$"),
    ("foot", r"Toes$"), ("foot", r"ToesEnd$"), ("foot", r"Heel$"),
    ("foot", r"FootSideInner$"), ("foot", r"FootSideOuter$"),
]
OPTIONAL = [
    ("spine", r"Belly$"), ("spine", r"Breast$"), ("face", r"Tongue\d*$"), ("face", r"Ear\d*$"),
    ("face", r"Eyebrow\d*$"), ("face", r"Cheek$"), ("face", r"Lip\w*$"), ("face", r"Nose$"),
]
INTERNAL = re.compile(r"^(FKX|IKX|IKAc|IKSp|IKfake|IKfk|Bend|Unreal|Main|Sub|Aim|Root(X|Part\d+X)|HipSwinger|Center|Group|Curve|Fk|Ik)")
SIDE = re.compile(r"^(?P<base>.+?)(?P<side>_[RLM])?$")
GROUPS = [g for g, _ in dict.fromkeys(CORE)] and ["spine", "neck_head", "face", "shoulder", "arm", "hand", "leg", "foot"]


def classify(name: str) -> Dict[str, object]:
    """One joint name -> {name, base, side, group, kind} with
    kind in core | optional | extra | internal | nonsym."""
    short = name.split("|")[-1].split(":")[-1]
    if short.endswith("_NonSymmetry"):
        return {"name": short, "base": short[:-12], "side": "", "group": "", "kind": "nonsym"}
    if INTERNAL.match(short):
        return {"name": short, "base": short, "side": "", "group": "", "kind": "internal"}
    m = SIDE.match(short)
    base, side = m.group("base"), (m.group("side") or "")
    for group, rx in CORE:
        if re.match(rx, base):
            return {"name": short, "base": base, "side": side, "group": group, "kind": "core"}
    for group, rx in OPTIONAL:
        if re.match(rx, base):
            return {"name": short, "base": base, "side": side, "group": group, "kind": "optional"}
    return {"name": short, "base": base, "side": side, "group": "extra", "kind": "extra"}


def is_twist(base: str) -> bool:
    return bool(re.search(r"Part\d+$", base))


def sort_names(names: Iterable[str]) -> Dict[str, List[str]]:
    """Bucket joint names by kind."""
    out: Dict[str, List[str]] = {"core": [], "optional": [], "extra": [], "internal": [], "nonsym": []}
    for n in names:
        out[classify(n)["kind"]].append(classify(n)["name"])
    return out


def core_bases(names: Iterable[str]) -> Dict[str, List[str]]:
    """Group -> sorted unique base names (side stripped) of the core joints."""
    out: Dict[str, set] = {g: set() for g in GROUPS}
    for n in names:
        c = classify(n)
        if c["kind"] == "core":
            out[c["group"]].add(c["base"])
    return {g: sorted(v) for g, v in out.items()}


def features(names: Iterable[str]) -> Dict[str, object]:
    """Headline anatomy of a joint list: finger chains, twist parts, etc."""
    cs = [classify(n) for n in names]
    core = [c for c in cs if c["kind"] == "core"]
    fingers = sorted({c["base"] for c in core if c["group"] == "hand" and "Finger" in c["base"]})
    chains = sorted({re.sub(r"\d+$", "", f) for f in fingers})
    twists = sorted({c["base"] for c in core if is_twist(c["base"])})
    return {
        "finger_chains": chains, "finger_joints": len(fingers),
        "twist_parts": twists, "n_twist_parts": len(twists),
        "has_scapula": any(c["base"] == "Scapula" for c in core),
        "has_cup": any(c["base"] == "Cup" for c in core),
        "has_eyes": any(c["base"] == "Eye" for c in core),
        "has_jaw": any(c["base"] == "Jaw" for c in core),
        "has_heel": any(c["base"] == "Heel" for c in core),
        "has_foot_sides": any(c["base"] == "FootSideInner" for c in core),
        "spine_joints": sorted({c["base"] for c in core if c["group"] == "spine" and not is_twist(c["base"])}),
        "neck_parts": sorted({c["base"] for c in core if c["base"].startswith("NeckPart")}),
        "optional": sorted({c["base"] for c in cs if c["kind"] == "optional"}),
        "extras": sorted({c["name"] for c in cs if c["kind"] == "extra"}),
    }
