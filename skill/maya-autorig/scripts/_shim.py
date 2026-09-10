"""Optional dcc_mcp_core contract.

Upstream (dcc-mcp-maya-advancedskeleton) requires dcc_mcp_core. We keep the exact
same call surface but fall back to a local implementation so these tools also run
standalone over Maya's commandPort, without pulling the whole dcc-mcp ecosystem.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict

try:  # pragma: no cover - exercised only inside a dcc-mcp runtime
    from dcc_mcp_core.skills_helper import (  # type: ignore
        run_main,
        skill_entry,
        skill_error_from_exception,
        skill_success,
    )

    USING_DCC_MCP_CORE = True

except ImportError:  # standalone fallback
    USING_DCC_MCP_CORE = False

    def skill_entry(func: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
        """No-op in standalone mode; dcc_mcp_core uses it for registration."""
        func.__is_skill_entry__ = True  # type: ignore[attr-defined]
        return func

    def skill_success(message: str, prompt: str = "", **context: Any) -> Dict[str, Any]:
        return {
            "success": True,
            "message": message,
            "prompt": prompt,
            "context": context,
        }

    def skill_error_from_exception(
        exc: BaseException, message: str = "", prompt: str = "", **context: Any
    ) -> Dict[str, Any]:
        return {
            "success": False,
            "message": message or str(exc),
            "prompt": prompt,
            "context": dict(context, error=str(exc), error_type=type(exc).__name__),
        }

    def run_main(main: Callable[..., Dict[str, Any]]) -> None:
        import sys

        params: Dict[str, Any] = {}
        if len(sys.argv) > 1:
            params = json.loads(sys.argv[1])
        print(json.dumps(main(**params), indent=2))
