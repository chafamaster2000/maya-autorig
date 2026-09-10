"""Discover and resolve AdvancedSkeleton MEL procedures on the installed version.

Why this exists
---------------
AdvancedSkeleton ships as MEL and its procedure names drift between major
versions. "Fit Auto Place" in particular is a newer feature whose proc name is
not documented publicly. Rather than hardcode a guess that fails silently at
runtime, every capability declares a *candidate list*; we resolve it against the
procedures actually present in the installed package and fail loudly, with
instructions, when nothing matches.

Discovery order:
  1. MEL `exists` on each candidate (catches procs already sourced).
  2. A scan of `global proc` declarations across the installed .mel files
     (catches procs that exist but have not been sourced yet).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_GLOBAL_PROC = re.compile(r"^\s*global\s+proc\s+(?:[\w\[\]]+\s+)?([A-Za-z_]\w*)\s*\(", re.M)

# Capability -> candidate proc names, most-likely first.
# VERIFIED procs (seen in AdvancedSkeleton MEL source) are marked; the rest are
# candidates to be confirmed by discover_procs on your installed version.
PROC_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    # All names below marked VERIFIED were confirmed present in AdvancedSkeleton
    # 6.910 by scanning the installed MEL files. Legacy names are kept as
    # fallbacks so older installs keep working.
    "fit_auto_place": (
        "asFitAutoPlace",  # VERIFIED 6.910
        "asAutoPlaceFitJoints",
        "asFitModeAutoPlace",
    ),
    "fit_auto_scale": ("asFitAutoScale",),  # VERIFIED 6.910
    "import_fit_skeleton": (
        "asFitSkeletonImport",  # VERIFIED 6.910
        "asImportFitSkeleton",  # legacy, <= 5.x
    ),
    "export_fit_skeleton": ("asFitSkeletonExport",),  # VERIFIED 6.910
    "build_rig": (
        "asReBuildAdvancedSkeleton",  # VERIFIED 6.910
        "asBuildAdvancedSkeleton",
    ),
    "create_skeleton": ("asCreateSkeleton",),  # VERIFIED 6.910
    "copy_skin": ("asCopySkin",),  # VERIFIED 6.910
    "smooth_skin": ("asSmoothSkin",),  # VERIFIED 6.910
    "set_bind_options": ("asSetSmoothBindOptions",),  # VERIFIED 6.910
    "delete_skeleton": ("asDeleteSkeleton",),  # VERIFIED 6.910
}


def maya_modules() -> Tuple[Any, Any]:
    """Import Maya lazily so this module is importable outside Maya."""
    import maya.cmds as cmds  # type: ignore
    import maya.mel as mel  # type: ignore

    return cmds, mel


_ENTRY_NAMES = ("AdvancedSkeleton.mel", "advancedSkeleton.mel")

# Searched when MAYA_SCRIPT_PATH is unset or does not contain the package --
# the case when these tools run outside Maya, e.g. over the bridge.
_FALLBACK_ROOTS = (
    "~/Library/Preferences/Autodesk/maya/scripts",   # macOS
    "~/Library/Preferences/Autodesk/maya",            # macOS
    "~/Documents/maya/scripts",                      # Windows (and macOS docs)
    "~/maya/scripts",                                # Linux
)


def _entry_in(base: Path) -> Optional[Path]:
    """AdvancedSkeleton.mel directly in `base`, or in one of its subfolders.

    AdvancedSkeleton is normally installed into its own folder inside a scripts
    directory, so the entry point sits one level below the path entry itself.
    """
    for name in _ENTRY_NAMES:
        if (base / name).is_file():
            return base
    if not base.is_dir():
        return None
    try:
        children = sorted(base.iterdir())
    except OSError:
        return None
    for child in children:
        if not child.is_dir():
            continue
        for name in _ENTRY_NAMES:
            if (child / name).is_file():
                return child
    return None


def advancedskeleton_dir() -> Path:
    """Directory holding the installed AdvancedSkeleton MEL files.

    Honours ADVANCEDSKELETON_DIR first, then MAYA_SCRIPT_PATH, then the usual
    per-platform install locations.
    """
    override = os.environ.get("ADVANCEDSKELETON_DIR", "").strip()
    if override:
        found = _entry_in(Path(override).expanduser())
        if found:
            return found.resolve()
        raise RuntimeError(
            "ADVANCEDSKELETON_DIR is set to '{}' but no AdvancedSkeleton.mel "
            "was found there.".format(override)
        )

    searched: List[str] = []
    for raw_dir in os.environ.get("MAYA_SCRIPT_PATH", "").split(os.pathsep):
        if not raw_dir:
            continue
        searched.append(raw_dir)
        found = _entry_in(Path(raw_dir))
        if found:
            return found.resolve()

    for raw_dir in _FALLBACK_ROOTS:
        base = Path(raw_dir).expanduser()
        searched.append(str(base))
        found = _entry_in(base)
        if found:
            return found.resolve()

    raise RuntimeError(
        "AdvancedSkeleton was not found. Searched:\n  {}\n"
        "Set ADVANCEDSKELETON_DIR to the folder containing "
        "AdvancedSkeleton.mel.".format("\n  ".join(searched) or "(nothing)")
    )


def scan_installed_procs(keyword: Optional[str] = None) -> Dict[str, List[str]]:
    """Map proc name -> files declaring it, across the installed .mel files.

    This reads the real installed version, so it is the ground truth for what
    your AdvancedSkeleton can actually do.
    """
    found: Dict[str, List[str]] = {}
    root = advancedskeleton_dir()
    needle = keyword.lower() if keyword else None
    for mel_file in sorted(root.rglob("*.mel")):
        try:
            text = mel_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for proc in _GLOBAL_PROC.findall(text):
            if needle and needle not in proc.lower():
                continue
            found.setdefault(proc, []).append(mel_file.name)
    return found


def mel_proc_exists(name: str) -> bool:
    """True when MEL can resolve `name` as a command or procedure right now."""
    _cmds, mel = maya_modules()
    try:
        return bool(int(mel.eval('exists "{}"'.format(name))))
    except Exception:
        return False


def resolve_proc(
    capability: str, extra_candidates: Sequence[str] = ()
) -> str:
    """Return the proc name implementing `capability` on this install.

    Raises with actionable guidance when no candidate resolves, instead of
    letting a wrong guess fail deep inside Maya.
    """
    candidates: Iterable[str] = tuple(extra_candidates) + PROC_CANDIDATES.get(capability, ())
    if not candidates:
        raise KeyError("Unknown capability: {}".format(capability))

    for name in candidates:
        if mel_proc_exists(name):
            return name

    declared = scan_installed_procs()
    for name in candidates:
        if name in declared:
            return name

    hint_keyword = capability.split("_")[-1]
    nearby = sorted(n for n in declared if hint_keyword.lower() in n.lower())
    raise RuntimeError(
        "Could not resolve an AdvancedSkeleton procedure for '{cap}'.\n"
        "Tried: {tried}\n"
        "Similar procs on your install: {nearby}\n\n"
        "To find the real name: in Maya open Script Editor, enable "
        "History > Echo All Commands, click the corresponding button in the "
        "AdvancedSkeleton UI, and read the echoed MEL. Then add that name to "
        "PROC_CANDIDATES['{cap}'] in _as_procs.py.".format(
            cap=capability,
            tried=", ".join(candidates) or "(none)",
            nearby=", ".join(nearby[:15]) or "(none found)",
        )
    )


def capability_report() -> Dict[str, Any]:
    """Which capabilities resolve on this install, and to what."""
    declared = scan_installed_procs()
    report: Dict[str, Any] = {}
    for capability in PROC_CANDIDATES:
        try:
            report[capability] = {"resolved": resolve_proc(capability), "available": True}
        except RuntimeError:
            report[capability] = {"resolved": None, "available": False}
    report["_total_procs_installed"] = len(declared)
    return report
