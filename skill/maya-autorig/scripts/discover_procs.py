"""Report which AdvancedSkeleton procedures exist on the installed version.

Run this FIRST on a new machine or after an AdvancedSkeleton upgrade. It tells
you which capabilities resolve and which need their proc name filled in.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _shim import run_main, skill_entry, skill_error_from_exception, skill_success  # noqa: E402
from _as_procs import advancedskeleton_dir, capability_report, scan_installed_procs  # noqa: E402


@skill_entry
def main(keyword: str = "", **_params: Any) -> Dict[str, Any]:
    try:
        report = capability_report()
        matches = scan_installed_procs(keyword or None)
        missing = [
            cap
            for cap, info in report.items()
            if not cap.startswith("_") and not info["available"]
        ]
        return skill_success(
            "Resolved {ok}/{total} capabilities against the installed package".format(
                ok=sum(
                    1
                    for c, i in report.items()
                    if not c.startswith("_") and i["available"]
                ),
                total=sum(1 for c in report if not c.startswith("_")),
            ),
            prompt=(
                "Fill PROC_CANDIDATES in _as_procs.py for: {}. Use Script Editor > "
                "History > Echo All Commands and click the matching AdvancedSkeleton "
                "button to read the real proc name.".format(", ".join(missing))
                if missing
                else "All capabilities resolved; autorig_oneshot is safe to run."
            ),
            install_dir=str(advancedskeleton_dir()),
            capabilities=report,
            unresolved=missing,
            matched_procs={k: v for k, v in sorted(matches.items())} if keyword else {},
            matched_proc_count=len(matches),
        )
    except Exception as exc:
        return skill_error_from_exception(
            exc,
            message="Could not inspect the AdvancedSkeleton installation",
            prompt="Verify AdvancedSkeleton is installed and on MAYA_SCRIPT_PATH.",
        )


if __name__ == "__main__":
    run_main(main)
