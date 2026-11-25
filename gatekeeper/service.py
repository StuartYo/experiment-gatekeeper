"""The workflow shared by the CLI, the MCP server and the console.

draft -> (preflight clean) -> request approval -> a person approves ->
start -> verdict (read results under the pre-registered rule) ->
request ship -> a person approves -> ship (complete the experiment in Flagsmith).

Agents can call every step except the two approvals.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import preflight as pf
from .flagsmith import Client, FlagsmithError, Settings
from .ledger import Record, canonical_hash, now
from .plan import Plan
from .verdict import decide


class GateRefused(RuntimeError):
    """A step was refused by the gate. The message says what a person must do."""


def client() -> Client:
    return Client(Settings.from_env())


# ------------------------------------------------------------------ preflight

def run_preflight(path: str, c: Client | None = None, rec: Record | None = None) -> dict:
    result = pf.scan(path)
    live: list[pf.Finding] = []
    if c is not None and rec is not None:
        fs = rec.data["flagsmith"]
        feature = c.feature(fs["feature_id"])
        feature["multivariate_options"] = c.variations(fs["feature_id"])
        metrics = [m for m in c.metrics() if m["id"] in fs["metric_ids"]]
        live = pf.live_checks(result, feature, metrics)
    elif rec is not None:
        spec = rec.data["spec"]
        feature = {"name": spec["flag"], "multivariate_options": [{"key": v["key"], "string_value": v["value"]} for v in spec["variants"]]}
        live = pf.live_checks(result, feature, [{"name": spec["metric"]["name"], "definition": {"event": spec["metric"]["event"]}}])
    result.findings.extend(live)
    out = result.to_dict()
    out["at"] = now()
    out["clean"] = out["blocking"] == 0
    if rec is not None:
        rec.data["preflight"] = out
        rec.log("gatekeeper", "preflight", f"{out['blocking']} blocking, {out['warnings']} warnings in {out['files_scanned']} files")
    return out


# ------------------------------------------------------------------ fingerprint

def fingerprint(c: Client, rec: Record) -> tuple[str, dict]:
    fs = rec.data["flagsmith"]
    exp = c.experiment(fs["experiment_id"])
    options = sorted(({"id": o["id"], "key": o.get("key"), "value": o.get("string_value")}
                      for o in c.variations(fs["feature_id"])), key=lambda o: o["id"])
    metrics = sorted(({"id": m["id"], "event": (m.get("definition") or {}).get("event"),
                       "aggregation": m.get("aggregation"), "direction": m.get("direction")}
                      for m in c.metrics() if m["id"] in fs["metric_ids"]), key=lambda m: m["id"])
    attached = sorted(m.get("metric") for m in c.experiment_metrics(fs["experiment_id"]))
    rollout = exp.get("experiment_rollout") or {}
    split = sorted(((v.get("multivariate_feature_option"), v.get("percentage_allocation"))
                    for v in rollout.get("multivariate_feature_state_values", []) or []), key=lambda x: x[0] or 0)
    snapshot = {"feature": fs["feature_id"], "options": options, "metrics": metrics, "attached_metrics": attached,
                "split": split, "rollout_percentage": rollout.get("rollout_percentage"),
                "plan": rec.data["plan"]["decision_rule"]}
    return canonical_hash(snapshot), snapshot


# ------------------------------------------------------------------ draft

def draft(*, name: str, flag: str, control_value: str, variants: list[dict], metric_event: str,
          metric_name: str | None, hypothesis: str, baseline: float, mde: float, code_path: str,
          daily_visitors: int | None = None, min_days: int = 0, actor: str = "cli",
          c: Client | None = None) -> Record:
    """Create flag, variations, metric and experiment in Flagsmith without starting it."""
    c = c or client()
    plan = Plan(baseline_rate=baseline, mde_relative=mde, variants=len(variants) + 1,
                daily_visitors=daily_visitors, min_days=min_days)
    share = round(100 / (len(variants) + 1), 4)

    feature = c.find_feature(flag) or c.create_multivariate_feature(flag, control_value, hypothesis[:200])
    existing = {o.get("key"): o for o in c.variations(feature["id"])}
    option_ids: dict[str, int] = {}
    for v in variants:
        o = existing.get(v["key"]) or c.add_variation(feature["id"], v["value"], v["key"], weight=0)
        option_ids[v["key"]] = o["id"]

    metric_name = metric_name or metric_event
    metric = next((m for m in c.metrics() if m["name"] == metric_name), None) or \
        c.create_metric(metric_name, metric_event)

    exp = c.create_experiment(feature["id"], name, hypothesis, metric["id"],
                              {oid: share for oid in option_ids.values()}, control_value)

    split = {"control": round(1 - share * len(variants) / 100, 6), **{k: share / 100 for k in option_ids}}
    rec = Record.create(
        name,
        spec={"flag": flag, "control_value": control_value, "variants": variants, "split": split,
              "metric": {"name": metric_name, "event": metric_event}, "hypothesis": hypothesis},
        plan={**plan.to_dict(), "split": split},
        code_path=str(Path(code_path).resolve()),
        flagsmith={"api_url": c.settings.api_url, "dashboard": c.settings.dashboard_url,
                   "project_id": c.project_id(), "environment_key": c.settings.environment_key,
                   "feature_id": feature["id"], "feature_name": flag, "option_ids": option_ids,
                   "metric_ids": [metric["id"]], "experiment_id": exp["id"]},
    )
    rec.log(actor, "drafted in Flagsmith", f"flag {flag}, metric {metric_event}, experiment #{exp['id']} (not started)")
    run_preflight(code_path, c, rec)
    rec.data["status"] = "draft" if rec.data["preflight"]["clean"] else "blocked"
    rec.save()
    return rec


# ------------------------------------------------------------------ approvals

def request_approval(slug: str, actor: str = "agent", note: str = "", c: Client | None = None) -> dict:
    rec = Record.load(slug)
    c = c or client()
    pre = run_preflight(rec.data["code_path"], c, rec)
    if not pre["clean"]:
        rec.data["status"] = "blocked"
        rec.save()
        raise GateRefused(f"Preflight found {pre['blocking']} blocking problem(s). Fix them, then ask again: "
                          + "; ".join(f"{f['rule']} {f['file']}:{f['line']} {f['title']}" for f in pre["findings"] if f["severity"] == "block"))
    fp, _ = fingerprint(c, rec)
    rec.data["pending_fingerprint"] = fp
    rec.request("start", actor, note)
    return {"status": "awaiting_approval", "fingerprint": fp,
            "next": f"A person must approve: run `gk approve {slug}` in a terminal, or press Approve in the console (`gk serve`)."}


def approve(slug: str, via: str, kind: str = "start", c: Client | None = None, by: str | None = None) -> dict:
    rec = Record.load(slug)
    c = c or client()
    if kind == "start":
        if not (rec.data.get("preflight") or {}).get("clean"):
            raise GateRefused("Preflight is not clean; nothing to approve yet.")
        fp, _ = fingerprint(c, rec)
    else:
        v = (rec.data.get("verdicts") or [None])[-1]
        if not v or v["decision"] not in ("ship", "drop"):
            raise GateRefused("There is no ship or drop verdict to approve.")
        fp = ship_fingerprint(rec, v)
    return rec.approve(kind, fp, via, by)


def start(slug: str, actor: str = "agent", c: Client | None = None) -> dict:
    rec = Record.load(slug)
    c = c or client()
    fp, _ = fingerprint(c, rec)
    ok = rec.valid_approval("start", fp)
    if not ok:
        last = rec.latest_approval("start")
        reason = ("No person has approved this experiment." if last is None else
                  "The setup in Flagsmith changed after it was approved (flag, split or metric). It needs a new approval.")
        rec.log(actor, "start refused", reason)
        raise GateRefused(reason + f" Run `gk approve {slug}` in a terminal or use the console.")
    exp = c.start(rec.data["flagsmith"]["experiment_id"])
    rec.data["status"] = "running"
    rec.data["started_at"] = exp.get("started_at") or now()
    rec.data["started_fingerprint"] = fp
    rec.log(actor, "started in Flagsmith", f"approved by {ok['by']} at {ok['at']}")
    return {"status": "running", "experiment": exp, "approved_by": ok["by"]}


# ------------------------------------------------------------------ verdict

def verdict(slug: str, actor: str = "agent", refresh: bool = True, c: Client | None = None) -> dict:
    rec = Record.load(slug)
    c = c or client()
    exp_id = rec.data["flagsmith"]["experiment_id"]
    refreshed = c.refresh_results(exp_id) if refresh else False
    res = c.results(exp_id) or {}
    payload = res.get("payload")
    current_fp, _ = fingerprint(c, rec)
    started = rec.data.get("started_at")
    days = None
    if started:
        try:
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() / 86400
        except ValueError:
            days = None
    need = rec.data["plan"]["decision_rule"]["min_exposures_per_arm"]
    smallest = 0
    if payload and payload.get("metrics"):
        smallest = min((v.get("n", 0) for v in payload["metrics"][0].get("variants", {}).values()), default=0)
    rec.data["peeks"].append({"at": now(), "smallest_arm": smallest, "target": need, "by": actor})
    peeks_before = sum(1 for p in rec.data["peeks"] if p["smallest_arm"] < p["target"])
    v = decide(payload, rec.data["plan"], approved_hash=rec.data.get("started_fingerprint"),
               current_hash=current_fp, peeks_before_target=peeks_before, days_running=days,
               primary_metric_id=rec.data["flagsmith"]["metric_ids"][0]).to_dict()
    v.update(at=now(), as_of=res.get("as_of"), refreshed=refreshed, peeks=len(rec.data["peeks"]))
    rec.data["verdicts"].append(v)
    rec.data["status"] = {"invalid": "invalid", "ship": "decided", "drop": "decided"}.get(v["decision"], rec.data["status"])
    rec.log(actor, "read results", f"{v['decision']}: {v['headline']}")
    return v


def ship_fingerprint(rec: Record, v: dict) -> str:
    return canonical_hash({"slug": rec.slug, "decision": v["decision"], "winner": v.get("winner"),
                           "arms": [(a["variant"], a["exposures"]) for a in v.get("arms", [])],
                           "started": rec.data.get("started_fingerprint")})


def request_ship(slug: str, actor: str = "agent", c: Client | None = None) -> dict:
    rec = Record.load(slug)
    v = (rec.data.get("verdicts") or [None])[-1]
    if not v:
        raise GateRefused("Read the results first (gk verdict).")
    if v["decision"] not in ("ship", "drop"):
        raise GateRefused(f"The verdict is '{v['decision']}': {v['headline']} Nothing can be rolled out yet.")
    rec.request("ship", actor, f"{v['decision']} {v.get('winner') or ''}".strip())
    return {"status": "awaiting_ship_approval",
            "next": f"A person must approve the rollout: `gk approve {slug} --ship` or the console."}


def ship(slug: str, actor: str = "agent", c: Client | None = None) -> dict:
    rec = Record.load(slug)
    c = c or client()
    v = (rec.data.get("verdicts") or [None])[-1]
    if not v or v["decision"] not in ("ship", "drop"):
        raise GateRefused("No ship or drop verdict.")
    fp = ship_fingerprint(rec, v)
    ok = rec.valid_approval("ship", fp)
    if not ok:
        rec.log(actor, "ship refused", "no matching approval")
        raise GateRefused(f"No person has approved this rollout. Run `gk approve {slug} --ship`.")
    fs = rec.data["flagsmith"]
    if v["decision"] == "ship":
        variant_id = fs["option_ids"].get(v["winner"])
        exp = c.complete(fs["experiment_id"], "variant", variant_id)
    else:
        exp = c.complete(fs["experiment_id"], "control")
    rec.data["status"] = "shipped"
    rec.log(actor, "completed in Flagsmith", f"{v['decision']} {v.get('winner')}, approved by {ok['by']}")
    return {"status": "shipped", "experiment": exp}


__all__ = ["GateRefused", "FlagsmithError", "run_preflight", "draft", "request_approval", "approve", "start",
           "verdict", "request_ship", "ship", "fingerprint", "client"]
