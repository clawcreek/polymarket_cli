# poly/groups/report.py
"""Precomputed market report — clawcreek fast path (additive to upstream poly).

`poly report` fetches a full precomputed opportunity scan in ONE HTTP call from the
clawcreek polymarket-gateway (`GET /v1/market-report`), so an agent can skip the slow
~150-call MCP enrichment. The report is public, read-only market data
(`{generated_at, age_seconds, stale, report:{universe, opportunities, enrichment,
account:null, meta}}`). If the endpoint is unreachable the command exits non-zero and
the caller falls back to the normal live commands — nothing else in poly changes.
"""
import json
import os
import urllib.error
import urllib.request

import typer

from ..output import emit, print_error

DEFAULT_TIMEOUT = 10


def _fetch(url: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """GET the report endpoint and return parsed JSON. Raises on HTTP/parse error."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed gateway URL)
        return json.loads(resp.read().decode("utf-8"))


def report_cmd(
    ctx: typer.Context,
    url: str = typer.Option(None, "--url", help="Report endpoint; defaults to $POLYMARKET_REPORT_URL."),
    timeout: int = typer.Option(DEFAULT_TIMEOUT, "--timeout", help="HTTP timeout in seconds."),
) -> None:
    """Fetch the clawcreek precomputed market report (fast path for scans/dashboards)."""
    endpoint = url or os.environ.get("POLYMARKET_REPORT_URL")
    if not endpoint:
        raise typer.BadParameter("no report URL: pass --url or set POLYMARKET_REPORT_URL")
    try:
        payload = _fetch(endpoint, timeout)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
        # Standard error envelope; a non-zero exit tells the agent to fall back to live reads.
        print_error(ctx.obj.output, f"report unavailable: {exc}")
        raise typer.Exit(1)
    emit(ctx.obj.output, payload)
