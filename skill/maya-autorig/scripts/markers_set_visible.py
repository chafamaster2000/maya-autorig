"""Tool entry `markers_set_visible` -> markers.set_visible.

The dcc-mcp runtime execs a tool's source_file and calls its main(**params);
it does not honour an `entrypoint` key. This wrapper maps the tool onto the
module function so the real code stays in one place.
"""

from __future__ import annotations


def main(**kwargs):
    import markers

    return markers.set_visible(**kwargs)
