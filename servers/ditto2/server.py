"""Local MCP tools for collecting and reviewing Android Flutter evidence."""

from typing import Annotated

from fastmcp import FastMCP
from pydantic import Field

import core

mcp = FastMCP('ditto2')


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def analyze_apk(apk_path: str, output_dir: str) -> dict:
    """Run Apktool, JADX and r2Flutter; retain full exports in a fresh evidence/analysis directory.

    Requires an ARM64 Flutter APK. JADX code errors retain its source export with status partial. Tool failures retain output marked .incomplete.
    """
    return core.analyze_apk(apk_path, output_dir)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': True, 'openWorldHint': True})
def explore_apk(apk_path: str, output_dir: str, device_serial: str,
                event_count: Annotated[int, Field(ge=1, le=1000)] = 100,
                timeout_seconds: Annotated[int, Field(ge=30, le=3600)] = 600) -> dict:
    """Install and explore the APK on the selected device; keep the app installed afterward.

    Use a fresh evidence/exploration directory. Actions can change app data and contact services.
    Retains all DroidBot screenshots, state files, events and navigation data for review.
    """
    return core.explore_apk(apk_path, output_dir, device_serial, event_count, timeout_seconds)


@mcp.tool(annotations={'readOnlyHint': True, 'openWorldHint': False})
def inspect_exploration(exploration_dir: str, offset: Annotated[int, Field(ge=0)] = 0,
                        limit: Annotated[int, Field(ge=1, le=100)] = 50) -> dict:
    """Page through screenshot and event paths. Open these files before confirming behavior."""
    return core.inspect_exploration(exploration_dir, offset, limit)


@mcp.tool(annotations={'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False})
def unify_evidence(apk_path: str, evidence_dir: str,
                   confirmed_screen_ids: Annotated[list[str], Field(min_length=1)],
                   confirmed_event_ids: list[str],
                   findings: Annotated[list[core.Finding], Field(min_length=1)], gaps: list[str]) -> dict:
    """Connect reviewed evidence in a directory containing complete analysis/ and exploration/ exports.

    Adds a fresh review.json index with cited findings, reviewed screens/actions, and gaps.
    The handoff is the entire directory, including all unreviewed raw data. This index is not
    a completeness certificate. Returns collection paths and review counts, not the full corpus.
    """
    return core.unify_evidence(apk_path, evidence_dir, confirmed_screen_ids,
                               confirmed_event_ids, findings, gaps)


if __name__ == '__main__':
    mcp.run()
