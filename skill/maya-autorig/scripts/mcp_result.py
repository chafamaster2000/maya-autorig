"""MCP-facing summary of a stage result. Pure Python, no Maya.

Every stage `main` builds a full result dict (positions, ray votes, weight
locality, per-pose displacement...) and writes it to `<evidence_dir>/<stage>.json`.
The agent driving the pipeline needs almost none of that in its context: it
needs pass/fail, *which* checks failed, the render paths for a reviewer, and
where the full JSON lives. Returning the full dict cost 1.2k-4.5k tokens per
stage call (and the gateway echoes it twice, as return_repr and return_value).
`slim` is what a stage returns by default; `compact=False` still returns the
full dict for the harness and for debugging.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def _failed(checks: Any) -> List[str]:
    out: List[str] = []
    for c in checks or []:
        if isinstance(c, dict) and not c.get("ok", True):
            out.append(str(c.get("check") or c.get("name") or "?"))
    return out


def _images(renders: Any) -> List[str]:
    imgs: List[str] = []
    for r in renders or []:
        if isinstance(r, dict):
            if r.get("ok") and r.get("path"):
                imgs.append(r["path"])
        elif isinstance(r, str):
            imgs.append(r)
    return imgs


def slim(out: Dict[str, Any], stage: str, evidence_dir: Optional[str] = None,
         metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reduce a full stage result to what the calling agent needs."""
    verify = out.get("verify") if isinstance(out.get("verify"), dict) else {}
    checks = out.get("checks") or verify.get("checks") or []
    renders = out.get("renders") or verify.get("renders") or []
    if not renders and isinstance(out.get("render"), dict):
        renders = [out["render"]]
    res: Dict[str, Any] = {
        "success": True,   # the tool ran; `passed` is what the verification said
        "stage": stage,
        "passed": bool(out.get("passed", out.get("success", False))),
        "failed": _failed(checks),
        "images": _images(renders),
        "expectation": out.get("expectation") or verify.get("expectation") or "",
    }
    if evidence_dir:
        path = os.path.join(evidence_dir, stage + ".json")
        if os.path.isfile(path):
            res["evidence_json"] = path
    if metrics:
        res["metrics"] = {k: v for k, v in metrics.items() if v is not None}
    for key in ("message", "error", "warning"):
        if out.get(key):
            res[key] = out[key]
    return res
