"""Run the whole demo against a real (self-hosted) Flagsmith and record it.

Every frame in the output is a snapshot of Gatekeeper's record right after a
real step: real API calls, real Flagsmith results computed from SIMULATED
traffic. The console's replay mode steps through these frames.

    python demo/record_run.py --out gatekeeper/static/replay.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from gatekeeper import service as svc  # noqa: E402
from gatekeeper.ledger import Record  # noqa: E402

DEMO = ROOT / "demo"


def use_env(key: str) -> None:
    os.environ["FLAGSMITH_ENVIRONMENT"] = key
    os.environ["FLAGSMITH_ENVIRONMENT_KEY"] = key


def traffic(visitors: int, scenario: str, seed: int, prefix: str) -> str:
    r = subprocess.run([sys.executable, str(DEMO / "traffic.py"), "--visitors", str(visitors), "--scenario", scenario,
                        "--seed", str(seed), "--prefix", prefix], capture_output=True, text=True, env=os.environ.copy())
    print(r.stdout.strip(), r.stderr[-300:] if r.returncode else "", flush=True)
    if r.returncode:
        raise SystemExit("traffic failed")
    return r.stdout.strip()


class Recorder:
    def __init__(self):
        self.frames: dict[str, list] = {}

    def snap(self, slug: str, action: str, stage: str, msg: str | None = None, kind: str = "note", button: str = "primary"):
        d = copy.deepcopy(Record.load(slug).data)
        d.update(replay_action=action, replay_stage=stage, replay_button=button)
        if msg:
            d.update(replay_msg=msg, replay_msg_kind=kind)
        self.frames.setdefault(slug, []).append(d)
        print(f"[frame] {slug}: {action}", flush=True)


def wait_refresh(seconds: int = 305) -> None:
    print(f"waiting {seconds}s (Flagsmith allows one results refresh every 5 minutes)", flush=True)
    time.sleep(seconds)


def scenario_main(rec: Recorder, env: str) -> str:
    use_env(env)
    r = svc.draft(name="Signup form: 3 fields vs 6", flag="signup_form", control_value="control",
                  variants=[{"key": "short", "value": "short"}], metric_event="signup_completed", metric_name="Signups",
                  hypothesis="Asking for 3 fields instead of 6 gets more visitors to finish signing up.",
                  baseline=0.30, mde=0.15, code_path=str(DEMO / "signup_broken"), actor="agent")
    slug = r.slug
    rec.snap(slug, "Agent drafts the experiment", "preflight")
    try:
        svc.request_approval(slug, "agent", "ready to start")
    except svc.GateRefused as e:
        rec.snap(slug, "Agent asks to start anyway", "preflight", f"Refused: {e}", "refused")
    r = Record.load(slug)
    r.data["code_path"] = str((DEMO / "signup_fixed").resolve())
    r.log("agent", "fixed the instrumentation", "enable_events, events URL, default handler, stored visitor cookie, same ID for conversion")
    svc.run_preflight(r.data["code_path"], svc.client(), r)
    r.data["status"] = "draft"
    r.save()
    rec.snap(slug, "Agent fixes the code, re-runs preflight", "preflight")
    svc.request_approval(slug, "agent", "preflight clean, plan: 2 groups")
    rec.snap(slug, "Agent asks a person to approve", "approval")
    try:
        svc.start(slug, "agent")
    except svc.GateRefused as e:
        rec.snap(slug, "Agent tries to start without waiting", "approval", f"Refused: {e}", "refused")
    svc.approve(slug, via="console", by="reviewer")
    rec.snap(slug, "Approve start", "approval", button="gate")
    svc.start(slug, "agent")
    rec.snap(slug, "Agent starts the experiment", "running")
    plan_n = Record.load(slug).data["plan"]["per_arm"]
    traffic(400, "healthy", 3, "early")
    time.sleep(3)
    svc.verdict(slug, "agent")
    rec.snap(slug, "Agent reads results after 400 simulated visitors", "verdict")
    traffic(int(plan_n * 2.15) - 400, "healthy", 4, "main")
    wait_refresh()
    svc.verdict(slug, "agent")
    rec.snap(slug, "Agent reads results at the planned sample", "verdict")
    try:
        svc.request_ship(slug, "agent")
        rec.snap(slug, "Agent asks to roll out the winner", "rollout")
        svc.approve(slug, via="console", kind="ship", by="reviewer")
        rec.snap(slug, "Approve rollout", "rollout", button="gate")
        svc.ship(slug, "agent")
        rec.snap(slug, "Agent rolls out the approved winner", "rollout")
    except svc.GateRefused as e:
        print("ship refused:", e)
    return slug


def scenario_srm(rec: Recorder, env: str) -> str:
    use_env(env)
    r = svc.draft(name="Signup form: slow control page", flag="signup_form", control_value="control",
                  variants=[{"key": "short", "value": "short"}], metric_event="signup_completed", metric_name="Signups",
                  hypothesis="Asking for 3 fields instead of 6 gets more visitors to finish signing up.",
                  baseline=0.30, mde=0.15, code_path=str(DEMO / "signup_fixed"), actor="agent")
    slug = r.slug
    svc.request_approval(slug, "agent")
    svc.approve(slug, via="console", by="reviewer")
    svc.start(slug, "agent")
    traffic(int(r.data["plan"]["per_arm"] * 2.3), "srm", 9, "srm")
    time.sleep(3)
    rec.snap(slug, "Running", "running")
    svc.verdict(slug, "agent")
    rec.snap(slug, "Agent reads results", "verdict")
    return slug


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "gatekeeper" / "static" / "replay.json"))
    ap.add_argument("--only", choices=["main", "srm"], default=None)
    args = ap.parse_args()
    os.environ.setdefault("GK_HOME", str(ROOT / ".gk-demo"))
    rec = Recorder()
    slugs = []
    if args.only in (None, "srm"):
        slugs.append(("srm", scenario_srm(rec, os.environ["FLAGSMITH_ENV_SRM"])))
    if args.only in (None, "main"):
        slugs.append(("main", scenario_main(rec, os.environ["FLAGSMITH_ENV_MAIN"])))
    stories = {
        "main": "A coding agent sets up a signup-form test. Gatekeeper stops the broken first version, waits for a person, and refuses an early winner.",
        "srm": "Same test, but the control page is slow and some visitors leave before they are counted. The split drifts and Gatekeeper calls the data invalid.",
    }
    out = {"mode": "replay", "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "environment": "self-hosted (local)", "flagsmith_version": (ROOT / ".flagsmith-version").read_text().strip()
           if (ROOT / ".flagsmith-version").exists() else "",
           "experiments": [{"start": 0, "story": stories[k], "frames": rec.frames[s]} for k, s in reversed(slugs)]}
    Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("wrote", args.out)


if __name__ == "__main__":
    main()
