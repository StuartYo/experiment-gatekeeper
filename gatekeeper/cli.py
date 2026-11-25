"""`gk` command line."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import preflight as pf
from . import service as svc
from .ledger import Record
from .plan import Plan

app = typer.Typer(add_completion=False, no_args_is_help=True,
                  help="Experiment Gatekeeper: stops broken Flagsmith experiments before they start and unproven winners before they ship.")
con = Console()

COLOR = {"block": "red", "warn": "yellow", "pass": "green", "fail": "red", "wait": "yellow"}
DECISION = {"ship": "green", "drop": "cyan", "keep_running": "yellow", "invalid": "red"}


def _fail(msg: str, code: int = 2):
    con.print(f"[bold red]refused[/] {msg}")
    raise typer.Exit(code)


def _print_findings(res: dict) -> None:
    if not res["findings"]:
        con.print(f"[green]No problems[/] in {res['files_scanned']} files.")
        return
    for f in res["findings"]:
        tag = "BLOCK" if f["severity"] == "block" else "WARN "
        where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        con.print(f"[{COLOR[f['severity']]}]{tag} {f['rule']}[/] [bold]{f['title']}[/]  [dim]{where}[/]")
        if f["snippet"]:
            con.print(f"      [dim]> {f['snippet']}[/]")
        if f["detail"]:
            con.print(f"      {f['detail']}")
        con.print(f"      [dim]fix:[/] {f['fix']}")
    con.print(f"\n{res['blocking']} blocking, {res['warnings']} warning(s) in {res['files_scanned']} files.")


@app.command()
def preflight(path: str = typer.Argument(".", help="Project folder or file to scan"),
              experiment: Optional[str] = typer.Option(None, "--experiment", "-e", help="Also check this experiment's Flagsmith setup"),
              as_json: bool = typer.Option(False, "--json")):
    """Scan code for tracking mistakes that ruin an experiment."""
    rec = Record.load(experiment) if experiment else None
    c = svc.client() if experiment else None
    res = svc.run_preflight(path, c, rec)
    if as_json:
        print(json.dumps(res, indent=2))
    else:
        _print_findings(res)
    raise typer.Exit(1 if res["blocking"] else 0)


@app.command()
def plan(baseline: float = typer.Option(..., help="Control conversion rate, e.g. 0.30"),
         mde: float = typer.Option(..., help="Smallest relative lift worth finding, e.g. 0.10 for +10%"),
         variants: int = typer.Option(2, help="Groups including control"),
         daily: Optional[int] = typer.Option(None, help="Visitors per day entering the test")):
    """How many visitors the test needs before anyone may read a winner."""
    p = Plan(baseline_rate=baseline, mde_relative=mde, variants=variants, daily_visitors=daily)
    con.print(f"Each group needs [bold]{p.per_arm():,}[/] visitors ([bold]{p.total():,}[/] total) "
              f"to detect a {mde:+.0%} relative change from {baseline:.1%} (alpha 0.05, power 0.8).")
    if p.days_needed():
        con.print(f"At {daily:,} visitors/day that is about [bold]{p.days_needed()}[/] days.")


@app.command()
def draft(name: str = typer.Option(...), flag: str = typer.Option(...),
          control: str = typer.Option(..., help="Control value"),
          variant: list[str] = typer.Option(..., help="key=value, repeatable"),
          event: str = typer.Option(..., help="Conversion event name sent by track_event"),
          hypothesis: str = typer.Option(...),
          baseline: float = typer.Option(...), mde: float = typer.Option(...),
          code: str = typer.Option(".", help="Path of the app that will run the experiment"),
          daily: Optional[int] = typer.Option(None)):
    """Create flag, variations, metric and experiment in Flagsmith. Does not start it."""
    vs = []
    for v in variant:
        if "=" not in v:
            _fail(f"--variant must be key=value, got {v!r}")
        k, val = v.split("=", 1)
        vs.append({"key": k, "value": val})
    rec = svc.draft(name=name, flag=flag, control_value=control, variants=vs, metric_event=event, metric_name=None,
                    hypothesis=hypothesis, baseline=baseline, mde=mde, code_path=code, daily_visitors=daily)
    con.print(f"Drafted [bold]{rec.slug}[/] (Flagsmith experiment #{rec.data['flagsmith']['experiment_id']}, not started).")
    _print_findings(rec.data["preflight"])


@app.command("request")
def request_(slug: str, ship: bool = typer.Option(False, "--ship")):
    """Ask a person to approve the start (or the rollout)."""
    try:
        out = svc.request_ship(slug, "cli") if ship else svc.request_approval(slug, "cli")
    except svc.GateRefused as e:
        _fail(str(e))
    con.print(out["next"])


@app.command()
def approve(slug: str, ship: bool = typer.Option(False, "--ship", help="Approve rolling out the verdict")):
    """A person approves. Needs an interactive terminal: agents cannot run this."""
    if not sys.stdin.isatty():
        _fail("Approval needs a person at an interactive terminal. Agents and scripts cannot approve.")
    rec = Record.load(slug)
    kind = "ship" if ship else "start"
    _show(rec)
    typed = typer.prompt(f"Type the experiment name ({slug}) to approve {kind}")
    if typed.strip() != slug:
        _fail("Name did not match; nothing approved.")
    try:
        a = svc.approve(slug, via="terminal", kind=kind)
    except svc.GateRefused as e:
        _fail(str(e))
    con.print(f"[green]Approved[/] {kind} by {a['by']} at {a['at']} (fingerprint {a['fingerprint'][:12]}).")


@app.command()
def start(slug: str):
    """Start the experiment in Flagsmith. Refused without a matching approval."""
    try:
        out = svc.start(slug, "cli")
    except svc.GateRefused as e:
        _fail(str(e))
    con.print(f"[green]Running.[/] Approved by {out['approved_by']}.")


@app.command()
def verdict(slug: str, no_refresh: bool = typer.Option(False, "--no-refresh"), as_json: bool = typer.Option(False, "--json")):
    """Read the results under the rule fixed before the test started."""
    v = svc.verdict(slug, "cli", refresh=not no_refresh)
    if as_json:
        print(json.dumps(v, indent=2))
        return
    con.print(f"[bold {DECISION[v['decision']]}]{v['decision'].upper().replace('_', ' ')}[/]  {v['headline']}")
    if v["flagsmith_says"]:
        con.print(f"[dim]{v['flagsmith_says']}[/]")
    for c in v["checks"]:
        con.print(f"  [{COLOR[c['status']]}]{c['status']:>4}[/] {c['id']} {c['label']}: {c['detail']}")


@app.command()
def ship(slug: str):
    """Complete the experiment in Flagsmith with the approved decision."""
    try:
        out = svc.ship(slug, "cli")
    except svc.GateRefused as e:
        _fail(str(e))
    con.print(f"[green]Done.[/] {out['status']}")


@app.command("list")
def list_():
    """Experiments managed in this folder."""
    t = Table("experiment", "status", "flag", "per group", "updated")
    for r in Record.all():
        tl = r.data.get("timeline") or [{}]
        t.add_row(r.slug, r.data["status"], r.data["spec"]["flag"], f"{r.data['plan']['per_arm']:,}", tl[-1].get("at", ""))
    con.print(t)


def _show(rec: Record) -> None:
    d = rec.data
    con.print(f"[bold]{d['name']}[/]  ({d['status']})")
    con.print(f"  hypothesis: {d['spec']['hypothesis']}")
    con.print(f"  flag {d['spec']['flag']}: control={d['spec']['control_value']!r}, "
              + ", ".join(f"{v['key']}={v['value']!r}" for v in d["spec"]["variants"]))
    con.print(f"  metric: {d['spec']['metric']['event']}   split: "
              + ", ".join(f"{k} {s:.0%}" for k, s in d["spec"]["split"].items()))
    r = d["plan"]["decision_rule"]
    con.print(f"  rule: {r['min_exposures_per_arm']:,} visitors per group, ship at >= {r['ship_if_chance_to_win_at_least']:.0%} chance to win")


@app.command()
def show(slug: str):
    """Show one experiment and its timeline."""
    rec = Record.load(slug)
    _show(rec)
    for e in rec.data.get("timeline", []):
        con.print(f"  [dim]{e['at']}[/] {e['actor']}: {e['action']} [dim]{e['detail']}[/]")


@app.command()
def serve(port: int = typer.Option(4810), replay: Optional[str] = typer.Option(None, help="Serve a recorded run (no Flagsmith key needed)")):
    """Open the console in the browser."""
    from .web import run
    run(port=port, replay=replay)


@app.command()
def mcp():
    """Run the MCP server on stdio (for Claude Code and other agents)."""
    from .mcp_server import main
    main()


if __name__ == "__main__":
    app()
