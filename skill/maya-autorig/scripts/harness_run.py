"""Tool entry `harness_run` -> harness.run.

The dcc-mcp runtime execs a tool's source_file and calls its main(**params);
it does not honour an `entrypoint` key. This wrapper maps the tool onto the
module function so the real code stays in one place.
"""

from __future__ import annotations


def main(**kwargs):
    import harness

    return harness.run(**kwargs)
