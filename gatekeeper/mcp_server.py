"""MCP server: the same workflow for coding agents, minus the approvals.

There is deliberately no ``approve`` tool. An agent can draft, check, ask,
start (only after a person approved), read results and ask to ship. The two
approvals happen in a terminal (``gk approve``) or in the console.
"""

from __future__ import annotations

import json

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import service as svc
from .ledger import Record
from .plan import Plan

mcp = FastMCP("experiment-gatekeeper")


def _safe(fn, *a, **kw) -> str:
    try:
        return json.dumps(fn(*a, **kw), indent=2, default=str)
    except svc.GateRefused as e:
        return json.dumps({"refused": True, "reason": str(e)})
    except svc.FlagsmithError as e:
        return json.dumps({"error": str(e)})
    except FileNotFoundError as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def preflight(path: str, experiment: str | None = None) -> str:
    """Scan an app for experiment tracking mistakes (events off, new visitor ID per request,
    mismatched identifiers, unflushed events, missing variation keys, metric/event name mismatch).
    Returns findings with file, line and fix. Blocking findings stop the experiment from being approved."""
    def run():
        rec = Record.load(experiment) if experiment else None
        return svc.run_preflight(path, svc.client() if rec else None, rec)
    return _safe(run)


@mcp.tool()
def plan(baseline_rate: float, min_detectable_lift: float, groups: int = 2, daily_visitors: int | None = None) -> str:
    """Visitors needed per group before a winner may be read. baseline_rate 0.30 = 30%; min_detectable_lift 0.10 = +10% relative."""
    return _safe(lambda: Plan(baseline_rate, min_detectable_lift, variants=groups, daily_visitors=daily_visitors).to_dict())


@mcp.tool()
def draft_experiment(name: str, flag: str, control_value: str, variants: list[dict], conversion_event: str,
                     hypothesis: str, baseline_rate: float, min_detectable_lift: float, code_path: str) -> str:
    """Create the multivariate flag (with keyed variations), metric and experiment in Flagsmith WITHOUT starting it,
    record the pre-registered plan, and run preflight. variants: [{"key": "short_form", "value": "3 fields"}]."""
    def run():
        rec = svc.draft(name=name, flag=flag, control_value=control_value, variants=variants,
                        metric_event=conversion_event, metric_name=None, hypothesis=hypothesis,
                        baseline=baseline_rate, mde=min_detectable_lift, code_path=code_path, actor="agent")
        return {"experiment": rec.slug, "status": rec.data["status"], "preflight": rec.data["preflight"],
                "plan": rec.data["plan"]}
    return _safe(run)


@mcp.tool()
def request_start_approval(experiment: str, note: str = "") -> str:
    """Ask a person to approve starting the experiment. Re-runs preflight first; refused while blocking problems remain."""
    return _safe(svc.request_approval, experiment, "agent", note)


@mcp.tool()
def start_experiment(experiment: str) -> str:
    """Start the experiment in Flagsmith. Refused unless a person approved this exact setup."""
    return _safe(svc.start, experiment, "agent")


@mcp.tool()
def read_verdict(experiment: str, refresh: bool = True) -> str:
    """Read results and apply the pre-registered rule: ship / drop / keep_running / invalid, with every check explained.
    Each call is counted as a peek."""
    return _safe(svc.verdict, experiment, "agent", refresh)


@mcp.tool()
def request_ship_approval(experiment: str) -> str:
    """Ask a person to approve rolling out the verdict. Refused unless the verdict is ship or drop."""
    return _safe(svc.request_ship, experiment, "agent")


@mcp.tool()
def ship_experiment(experiment: str) -> str:
    """Complete the experiment in Flagsmith with the approved decision. Refused without a person's approval."""
    return _safe(svc.ship, experiment, "agent")


@mcp.tool()
def status(experiment: str | None = None) -> str:
    """List experiments, or show one with its timeline and open approval requests."""
    def run():
        if experiment:
            return Record.load(experiment).data
        return [{"experiment": r.slug, "status": r.data["status"], "flag": r.data["spec"]["flag"]} for r in Record.all()]
    return _safe(run)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
